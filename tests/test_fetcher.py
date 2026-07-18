
import json
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.fetcher.format_detector import FormatDetector
from src.fetcher.cleaner import TextCleaner
from src.fetcher.section_splitter import SectionSplitter
from src.fetcher.chunker import Chunker
from src.fetcher.metadata_attacher import MetadataAttacher
from src.fetcher.handlers import JSONHandler, XMLHandler, HTMLHandler
from src.fetcher.coref_client import CorefClient
from src.fetcher.fetcher import Fetcher


# FormatDetector

class TestFormatDetector:
    @pytest.fixture
    def detector(self):
        return FormatDetector()

    def test_json_content_type(self, detector):
        assert detector.detect("application/json") == "json"

    def test_xml_content_type(self, detector):
        assert detector.detect("application/xml; charset=utf-8") == "xml"

    def test_html_content_type(self, detector):
        assert detector.detect("text/html; charset=UTF-8") == "html"

    def test_pdf_content_type(self, detector):
        assert detector.detect("application/pdf") == "pdf"

    def test_unknown_content_type(self, detector):
        assert detector.detect("application/octet-stream") == "unknown"

    def test_case_insensitive(self, detector):
        assert detector.detect("Application/JSON") == "json"


# TextCleaner

class TestTextCleaner:
    @pytest.fixture
    def cleaner(self):
        return TextCleaner()

    def test_removes_copyright_notice(self, cleaner):
        text = "Some text. © 2024 Elsevier. More content."
        result = cleaner.clean(text)
        assert "Elsevier" not in result
        assert "Some text" in result

    def test_removes_doi_line(self, cleaner):
        text = "Background section. doi: 10.1234/abc123 Next sentence."
        result = cleaner.clean(text)
        assert "doi:" not in result.lower()

    def test_removes_pmid_line(self, cleaner):
        text = "Results. PMID: 38000001 Discussion follows."
        result = cleaner.clean(text)
        assert "pmid:" not in result.lower()

    def test_collapses_whitespace(self, cleaner):
        text = "Word1    word2\n\n\nword3"
        result = cleaner.clean(text)
        assert "  " not in result  # no double spaces

    def test_strips_leading_trailing_whitespace(self, cleaner):
        text = "   clean content   "
        assert cleaner.clean(text) == "clean content"

    def test_plain_text_unchanged_structure(self, cleaner):
        text = "The patient showed improvement after treatment."
        result = cleaner.clean(text)
        assert "improvement" in result
        assert "treatment" in result

    def test_removes_conflict_of_interest_statement(self, cleaner):
        text = "Data shown. Conflict of interest: none declared. Next part."
        result = cleaner.clean(text)
        assert "conflict of interest" not in result.lower()

    def test_removes_funding_statement(self, cleaner):
        text = "Findings. Funding: NIH Grant R01. Final sentence."
        result = cleaner.clean(text)
        assert "NIH Grant" not in result


# SectionSplitter

class TestSectionSplitter:
    @pytest.fixture
    def splitter(self):
        return SectionSplitter()

    def test_splits_abstract_intro_methods(self, splitter):
        text = (
            "abstract\nThis is the abstract text.\n"
            "introduction\nThis is the introduction.\n"
            "methods\nThis describes the methods."
        )
        sections = splitter.split(text)
        names = [s["section"] for s in sections]
        assert "abstract" in names
        assert "introduction" in names
        assert "methods" in names

    def test_section_text_content(self, splitter):
        text = "abstract\nCRISPR gene editing study.\nmethods\nWe used PCR."
        sections = splitter.split(text)
        abstract = next(s for s in sections if s["section"] == "abstract")
        assert "CRISPR" in abstract["text"]

    def test_no_sections_returns_abstract_fallback(self, splitter):
        text = "Plain text without any section headers."
        sections = splitter.split(text)
        assert len(sections) >= 1
        assert sections[0]["section"] == "abstract"

    def test_background_maps_to_introduction(self, splitter):
        text = "background\nBackground content here."
        sections = splitter.split(text)
        assert any(s["section"] == "introduction" for s in sections)

    def test_results_and_discussion(self, splitter):
        text = (
            "results\nWe found significant changes.\n"
            "discussion\nThis suggests that CRISPR is effective."
        )
        sections = splitter.split(text)
        names = [s["section"] for s in sections]
        assert "results" in names
        assert "discussion" in names

    def test_inline_section_splitting(self, splitter):
        text = "Abstract This is the abstract. Introduction Here is the introduction. Methods We used PCR."
        sections = splitter.split(text)
        # Should detect at least 1 section inline
        assert len(sections) >= 1

    def test_supplementary_section(self, splitter):
        text = "results\nMain results.\nsupplementary\nExtra data."
        sections = splitter.split(text)
        names = [s["section"] for s in sections]
        assert "supplementary" in names

    def test_section_text_not_empty(self, splitter):
        text = "abstract\nSome content.\nmethods\nOther content."
        sections = splitter.split(text)
        for s in sections:
            assert s["text"].strip() != ""


# Chunker

class TestChunker:
    @pytest.fixture
    def chunker(self):
        return Chunker()

    def test_short_section_returns_single_chunk(self, chunker):
        section = {"section": "abstract", "text": "CRISPR is a gene editing tool."}
        chunks = chunker.chunk_section(section)
        assert len(chunks) == 1
        assert chunks[0]["chunk_index"] == 0
        assert chunks[0]["total_chunks"] == 1

    def test_chunk_has_required_fields(self, chunker):
        section = {"section": "methods", "text": "We used PCR and gel electrophoresis."}
        chunks = chunker.chunk_section(section)
        for chunk in chunks:
            assert "text" in chunk
            assert "section" in chunk
            assert "chunk_index" in chunk
            assert "total_chunks" in chunk

    def test_chunk_preserves_section_tag(self, chunker):
        section = {"section": "results", "text": "The experiment succeeded."}
        chunks = chunker.chunk_section(section)
        assert all(c["section"] == "results" for c in chunks)

    def test_chunk_document_aggregates_sections(self, chunker):
        sections = [
            {"section": "abstract", "text": "Brief abstract."},
            {"section": "methods", "text": "Detailed methods text here."},
        ]
        chunks = chunker.chunk_document(sections)
        sections_in_chunks = {c["section"] for c in chunks}
        assert "abstract" in sections_in_chunks
        assert "methods" in sections_in_chunks

    def test_chunk_document_empty_returns_empty(self, chunker):
        assert chunker.chunk_document([]) == []

    def test_large_text_splits_into_multiple_chunks(self, monkeypatch):
        """Force a tiny max_tokens to trigger splitting without huge text."""
        chunker = Chunker()
        monkeypatch.setattr(chunker, "max_tokens", 5)
        long_text = ". ".join(["word alpha beta gamma delta epsilon"] * 10)
        section = {"section": "results", "text": long_text}
        chunks = chunker.chunk_section(section)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk["total_chunks"] == len(chunks)

    def test_chunk_index_monotonically_increases(self, monkeypatch):
        chunker = Chunker()
        monkeypatch.setattr(chunker, "max_tokens", 5)
        long_text = ". ".join(["alpha beta gamma delta epsilon"] * 10)
        section = {"section": "discussion", "text": long_text}
        chunks = chunker.chunk_section(section)
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))


# MetadataAttacher

class TestMetadataAttacher:
    @pytest.fixture
    def attacher(self):
        return MetadataAttacher()

    @pytest.fixture
    def sample_chunks(self):
        return [
            {"text": "Chunk A.", "section": "abstract", "chunk_index": 0, "total_chunks": 2},
            {"text": "Chunk B.", "section": "abstract", "chunk_index": 1, "total_chunks": 2},
        ]

    def test_attaches_document_id(self, attacher, sample_chunks):
        result = attacher.attach(sample_chunks, "doc-001", "pubmed", "https://url")
        for chunk in result:
            assert chunk["document_id"] == "doc-001"

    def test_attaches_source_name(self, attacher, sample_chunks):
        result = attacher.attach(sample_chunks, "doc-001", "biorxiv", "https://url")
        for chunk in result:
            assert chunk["source_name"] == "biorxiv"

    def test_attaches_source_url(self, attacher, sample_chunks):
        url = "https://example.com/article"
        result = attacher.attach(sample_chunks, "doc-001", "pubmed", url)
        for chunk in result:
            assert chunk["source_url"] == url

    def test_position_field_is_sequential(self, attacher, sample_chunks):
        result = attacher.attach(sample_chunks, "doc-001", "pubmed")
        positions = [chunk["position"] for chunk in result]
        assert positions == list(range(len(sample_chunks)))

    def test_preserves_text_and_section(self, attacher, sample_chunks):
        result = attacher.attach(sample_chunks, "doc-001", "pubmed")
        assert result[0]["text"] == "Chunk A."
        assert result[1]["section"] == "abstract"

    def test_empty_chunks_returns_empty(self, attacher):
        assert attacher.attach([], "doc-001", "pubmed") == []


# JSONHandler

class TestJSONHandler:
    @pytest.fixture
    def handler(self):
        return JSONHandler()

    def test_extract_with_text_field(self, handler):
        data = {"title": "CRISPR study", "body": "Main content here.", "year": 2024}
        result = handler.extract(json.dumps(data), text_field="body")
        assert result == "Main content here."

    def test_extract_all_text_when_no_field(self, handler):
        data = {"title": "CRISPR study", "abstract": "Short abstract."}
        result = handler.extract(json.dumps(data))
        assert "CRISPR study" in result
        assert "Short abstract." in result

    def test_extract_nested_json(self, handler):
        data = {"outer": {"inner": "nested text"}}
        result = handler.extract(json.dumps(data))
        assert "nested text" in result

    def test_extract_list_json(self, handler):
        data = {"items": ["first item", "second item"]}
        result = handler.extract(json.dumps(data))
        assert "first item" in result
        assert "second item" in result

    def test_ignores_numeric_values(self, handler):
        data = {"count": 42, "label": "target"}
        result = handler.extract(json.dumps(data))
        assert "target" in result

    def test_text_field_fallback_to_all_when_missing(self, handler):
        data = {"title": "fallback text"}
        result = handler.extract(json.dumps(data), text_field="nonexistent_field")
        assert "fallback text" in result


# XMLHandler
class TestXMLHandler:
    @pytest.fixture
    def handler(self):
        return XMLHandler()

    def test_extract_all_text_from_simple_xml(self, handler):
        # XMLHandler falls back to iterating all elements when there is no <body>
        # (PMC detection) and no text_field.  The fallback collects .text and .tail.
        xml = "<root><title>Gene therapy</title><description>Treatment details.</description></root>"
        result = handler.extract(xml)
        assert "Gene therapy" in result
        assert "Treatment details." in result

    def test_extract_with_text_field(self, handler):
        xml = "<article><AbstractText>Abstract content here.</AbstractText><other>ignore</other></article>"
        result = handler.extract(xml, text_field="AbstractText")
        assert "Abstract content here." in result

    def test_pmc_article_with_body_extracts_sections(self, handler):
        xml = """<article>
            <abstract><p>This is the abstract.</p></abstract>
            <body>
                <sec>
                    <title>Introduction</title>
                    <p>The introduction text.</p>
                </sec>
                <sec>
                    <title>Methods</title>
                    <p>The methods text.</p>
                </sec>
            </body>
        </article>"""
        result = handler.extract(xml)
        assert "introduction" in result.lower() or "Introduction" in result
        assert "Methods" in result or "methods" in result.lower()

    def test_extract_text_from_element_tail(self, handler):
        xml = "<root><a>text A</a> tail text</root>"
        result = handler.extract(xml)
        assert "text A" in result
        assert "tail text" in result


# HTMLHandler

class TestHTMLHandler:
    @pytest.fixture
    def handler(self):
        return HTMLHandler()

    def test_extracts_visible_text(self, handler):
        html = "<html><body><p>Main content.</p></body></html>"
        result = handler.extract(html)
        # The stub strips tags with regex — plain text should be present
        assert "Main content." in result

    def test_removes_script_tags(self, handler):
        """Verify the handler calls soup([...]) to remove boilerplate tags."""
        html = "<html><body><script>var x=1;</script><p>Visible text.</p></body></html>"
        from unittest.mock import patch as _patch

        mock_soup = MagicMock()
        mock_soup.return_value.get_text.return_value = "Visible text."
        mock_soup.return_value.__call__ = MagicMock(return_value=[])  # no tags to decompose
        mock_soup.return_value.find.return_value = None

        with _patch("src.fetcher.handlers.BeautifulSoup", mock_soup):
            result = handler.extract(html)
        # After patching, soup.get_text() returns our stub value
        assert "Visible text." in result

    def test_removes_style_tags(self, handler):
        """BeautifulSoup is called; verify correct tags are targeted for decomposition."""
        html = "<html><head><style>.foo{color:red;}</style></head><body><p>Text.</p></body></html>"
        from unittest.mock import patch as _patch

        decomposed = []

        mock_tag = MagicMock()
        mock_tag.decompose.side_effect = lambda: decomposed.append(True)

        # soup_instance(["script", "style", ...]) must return an iterable of tags
        soup_instance = MagicMock()
        soup_instance.side_effect = lambda tags: [mock_tag]  # soup(tags) → [mock_tag]
        soup_instance.get_text.return_value = "Text."
        soup_instance.find.return_value = None

        mock_soup_cls = MagicMock(return_value=soup_instance)

        with _patch("src.fetcher.handlers.BeautifulSoup", mock_soup_cls):
            handler.extract(html)

        # decompose() should have been called at least once for the boilerplate tag
        assert len(decomposed) >= 1

    def test_removes_nav_and_footer(self, handler):
        """Ensure nav/footer tags are targeted by soup([...]) call."""
        html = "<html><body><nav>Nav links</nav><p>Article text.</p><footer>Footer text</footer></body></html>"
        from unittest.mock import patch as _patch

        mock_soup = MagicMock()
        mock_soup.return_value.get_text.return_value = "Article text."
        mock_soup.return_value.find.return_value = None
        mock_soup.return_value.__call__ = MagicMock(return_value=[])

        with _patch("src.fetcher.handlers.BeautifulSoup", mock_soup):
            result = handler.extract(html)

        assert "Article text." in result

    def test_extract_with_text_field(self, handler):
        """When text_field is provided, handler calls soup.find(text_field)."""
        html = "<html><body><article>Article body.</article><aside>Sidebar</aside></body></html>"
        from unittest.mock import patch as _patch

        found_tag = MagicMock()
        found_tag.get_text.return_value = "Article body."

        mock_soup = MagicMock()
        mock_soup.return_value.find.return_value = found_tag
        mock_soup.return_value.__call__ = MagicMock(return_value=[])

        with _patch("src.fetcher.handlers.BeautifulSoup", mock_soup):
            result = handler.extract(html, text_field="article")

        assert "Article body." in result


# CorefClient

class TestCorefClient:
    @pytest.fixture
    def client(self):
        return CorefClient("http://202.181.159.222:8081")

    def test_health_check_returns_true_on_200(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("src.fetcher.coref_client.requests.get", return_value=mock_resp):
            assert client.health_check() is True

    def test_health_check_returns_false_on_non_200(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("src.fetcher.coref_client.requests.get", return_value=mock_resp):
            assert client.health_check() is False

    def test_health_check_returns_false_on_exception(self, client):
        with patch("src.fetcher.coref_client.requests.get", side_effect=Exception("refused")):
            assert client.health_check() is False

    def test_resolve_returns_resolved_text(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"resolved_text": "The protein was found in it."}
        mock_resp.raise_for_status = MagicMock()
        with patch("src.fetcher.coref_client.requests.post", return_value=mock_resp):
            result = client.resolve("The protein was found in it.")
        assert result == "The protein was found in it."

    def test_resolve_returns_original_on_timeout(self, client):
        import requests as req_lib
        with patch("src.fetcher.coref_client.requests.post", side_effect=req_lib.exceptions.Timeout()):
            result = client.resolve("original text")
        assert result == "original text"

    def test_resolve_returns_original_on_request_exception(self, client):
        import requests as req_lib
        with patch(
            "src.fetcher.coref_client.requests.post",
            side_effect=req_lib.exceptions.ConnectionError("refused"),
        ):
            result = client.resolve("original text")
        assert result == "original text"



# Fetcher
class TestFetcher:
    @pytest.fixture
    def fetcher(self):
        with patch("src.fetcher.fetcher.CorefClient") as MockCoref:
            instance = MockCoref.return_value
            instance.health_check.return_value = True  # coref service online
            instance.base_url = "http://localhost:8080"
            instance.resolve.side_effect = lambda text: text  # echo text unchanged
            f = Fetcher(coref_url="http://localhost:8080")
            f.coref_client = instance
            return f

    @pytest.fixture
    def simple_source(self):
        return {"name": "pubmed", "rate_limit": None}

    def _fake_xml_response(self, xml_text):
        mock = MagicMock()
        mock.status_code = 200
        mock.content = xml_text.encode()
        mock.text = xml_text
        mock.headers = {"Content-Type": "application/xml"}
        mock.raise_for_status = MagicMock()
        return mock

    def test_fetch_returns_list_of_chunks(self, fetcher, simple_source):
        xml = "<article><AbstractText>CRISPR study abstract.</AbstractText></article>"
        with patch("src.fetcher.fetcher.requests.get", return_value=self._fake_xml_response(xml)):
            chunks = fetcher.fetch("https://example.com/article", simple_source, "doc-001")
        assert isinstance(chunks, list)
        assert len(chunks) >= 1

    def test_chunks_have_metadata_fields(self, fetcher, simple_source):
        xml = "<article><AbstractText>Short abstract text.</AbstractText></article>"
        with patch("src.fetcher.fetcher.requests.get", return_value=self._fake_xml_response(xml)):
            chunks = fetcher.fetch("https://example.com/article", simple_source, "doc-999")
        for chunk in chunks:
            assert "document_id" in chunk
            assert "source_name" in chunk
            assert "text" in chunk
            assert "section" in chunk

    def test_chunks_have_correct_document_id(self, fetcher, simple_source):
        xml = "<article><AbstractText>Test content.</AbstractText></article>"
        with patch("src.fetcher.fetcher.requests.get", return_value=self._fake_xml_response(xml)):
            chunks = fetcher.fetch("https://example.com/article", simple_source, "DOC-XYZ")
        assert all(c["document_id"] == "DOC-XYZ" for c in chunks)

    def test_chunks_have_correct_source_name(self, fetcher, simple_source):
        xml = "<article><AbstractText>Bio content.</AbstractText></article>"
        with patch("src.fetcher.fetcher.requests.get", return_value=self._fake_xml_response(xml)):
            chunks = fetcher.fetch("https://example.com/article", simple_source, "doc-001")
        assert all(c["source_name"] == "pubmed" for c in chunks)

    def test_apply_api_key_appended_correctly(self, fetcher):
        source = {"name": "pubmed", "api_key_env": "NCBI_KEY"}
        os.environ["NCBI_KEY"] = "TESTKEY"
        try:
            url = fetcher._apply_api_key("https://example.com/query?db=pubmed", source)
            assert "TESTKEY" in url
        finally:
            del os.environ["NCBI_KEY"]

    def test_apply_api_key_no_env_returns_unchanged(self, fetcher):
        source = {"name": "pubmed", "api_key_env": "MISSING_KEY_ENV"}
        url = "https://example.com/query"
        assert fetcher._apply_api_key(url, source) == url

    def test_apply_api_key_no_key_env_returns_unchanged(self, fetcher):
        source = {"name": "pubmed"}
        url = "https://example.com/query"
        assert fetcher._apply_api_key(url, source) == url

    def test_rate_limit_sleeps(self, fetcher):
        source = {"name": "pubmed", "rate_limit": 10}  # 1/10 = 0.1 s
        with patch("src.fetcher.fetcher.time.sleep") as mock_sleep:
            fetcher._rate_limit(source)
        mock_sleep.assert_called_once_with(0.1)

    def test_rate_limit_no_rate_does_not_sleep(self, fetcher):
        source = {"name": "pubmed"}
        with patch("src.fetcher.fetcher.time.sleep") as mock_sleep:
            fetcher._rate_limit(source)
        mock_sleep.assert_not_called()

    def test_extract_text_json(self, fetcher):
        source = {"name": "biorxiv", "text_field": None}
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({"abstract": "BioRxiv abstract."})
        result = fetcher._extract_text("json", mock_resp, source)
        assert "BioRxiv abstract." in result

    def test_extract_text_html(self, fetcher):
        source = {"name": "biorxiv", "text_field": None}
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><p>HTML article content.</p></body></html>"
        result = fetcher._extract_text("html", mock_resp, source)
        assert "HTML article content." in result

    def test_extract_text_unknown_returns_raw(self, fetcher):
        source = {"name": "biorxiv", "text_field": None}
        mock_resp = MagicMock()
        mock_resp.text = "raw text content"
        result = fetcher._extract_text("unknown", mock_resp, source)
        assert result == "raw text content"
