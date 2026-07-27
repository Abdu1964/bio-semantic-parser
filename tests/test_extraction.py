import json
from unittest.mock import MagicMock, patch, ANY

import pytest

from src.schema.pydantic_model import BiologicalRelation, ExtractionResult
from src.schema.taxonomy import RelationType, TAXONOMY
from src.extraction.extractor import (
    _parse_json,
    _user_message,
    _taxonomy_block,
    _split_chunk,
    _is_truncation_error,
    _log_rejected,
    extract,
    extract_batch,
)


class TestParseJson:
    def test_parses_valid_json(self):
        data = {"relations": [{"extraction_viable": True}]}
        assert _parse_json(json.dumps(data)) == data

    def test_strips_markdown_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        assert _parse_json(raw) == {"key": "value"}

    def test_strips_markdown_fence_without_lang(self):
        raw = "```\n{\"a\": 1}\n```"
        assert _parse_json(raw) == {"a": 1}

    def test_handles_whitespace_around_json(self):
        raw = "  \n  {\"x\": 2}  \n  "
        assert _parse_json(raw) == {"x": 2}


class TestUserMessage:
    def test_includes_section_and_text(self):
        chunk = {"text": "SIRT1 upregulates FOXO3.", "section": "results"}
        msg = _user_message(chunk)
        assert "results" in msg
        assert "SIRT1 upregulates FOXO3" in msg

    def test_includes_entities_block(self):
        chunk = {
            "text": "Text.",
            "section": "abstract",
            "entities": [{"text": "SIRT1", "label": "GENE"}, {"text": "FOXO3", "label": "GENE"}],
        }
        msg = _user_message(chunk)
        assert "SIRT1" in msg
        assert "FOXO3" in msg
        assert "PRE-TAGGED ENTITIES" in msg

    def test_includes_negated_entities(self):
        chunk = {
            "text": "Text.",
            "section": "results",
            "entities": [{"text": "p53", "label": "GENE", "negated": True}],
            "negated_entities": [{"text": "p53", "label": "GENE"}],
        }
        msg = _user_message(chunk)
        assert "NEGATION FLAG" in msg
        assert "p53" in msg

    def test_no_entities_block_when_empty(self):
        chunk = {"text": "Plain text.", "section": "methods"}
        msg = _user_message(chunk)
        assert "PRE-TAGGED ENTITIES" not in msg

    def test_no_negation_block_when_empty(self):
        chunk = {"text": "Text.", "section": "discussion", "entities": []}
        msg = _user_message(chunk)
        assert "NEGATION FLAG" not in msg


class TestTaxonomyBlock:
    def test_contains_all_relation_types(self):
        block = _taxonomy_block()
        for rel in RelationType:
            assert rel.value in block

    def test_contains_definitions(self):
        block = _taxonomy_block()
        entry = TAXONOMY[RelationType.ACTIVATES]
        assert entry["definition"] in block

    def test_contains_examples(self):
        block = _taxonomy_block()
        entry = TAXONOMY[RelationType.INHIBITS]
        assert entry["example"] in block


class TestSplitChunk:
    def test_splits_into_two_halves(self):
        chunk = {"text": "Sentence one. Sentence two. Sentence three. Sentence four.", "section": "results", "document_id": "d1"}
        result = _split_chunk(chunk)
        assert len(result) == 2

    def test_sub_chunks_carry_original_metadata(self):
        chunk = {"text": "A. B. C. D.", "section": "methods", "document_id": "doc-1", "chunk_index": 0}
        result = _split_chunk(chunk)
        for sc in result:
            assert sc["section"] == "methods"
            assert sc["document_id"] == "doc-1"

    def test_first_half_has_first_sentences(self):
        chunk = {"text": "First part. Second part. Third part. Fourth part.", "document_id": "d1", "section": "abstract"}
        result = _split_chunk(chunk)
        assert "First part" in result[0]["text"]
        assert "Second part" in result[0]["text"]

    def test_second_half_has_last_sentences(self):
        chunk = {"text": "First. Second. Third. Fourth.", "document_id": "d1", "section": "abstract"}
        result = _split_chunk(chunk)
        assert "Third" in result[1]["text"]
        assert "Fourth" in result[1]["text"]

    def test_sub_chunk_markers(self):
        chunk = {"text": "A. B.", "document_id": "d1", "section": "abstract"}
        result = _split_chunk(chunk)
        assert result[0]["_sub_chunk"] == "1/2"
        assert result[1]["_sub_chunk"] == "2/2"


class TestIsTruncationError:
    def test_unterminated_string_is_truncation(self):
        assert _is_truncation_error("Unterminated string starting at line 1") is True

    def test_expecting_delimiter_is_truncation(self):
        assert _is_truncation_error("Expecting ',' delimiter") is True

    def test_expecting_property_name_is_truncation(self):
        assert _is_truncation_error("Expecting property name") is True

    def test_regular_error_is_not_truncation(self):
        assert _is_truncation_error("Some other error") is False

    def test_empty_string_is_not_truncation(self):
        assert _is_truncation_error("") is False


class TestLogRejected:
    def test_writes_to_queue_file(self, tmp_path):
        chunk = {"document_id": "doc-1", "section": "results", "text": "Sample text content here."}
        queue = tmp_path / "rejected.jsonl"
        with patch("src.extraction.extractor._QUEUE_PATH", queue):
            _log_rejected(chunk, "parse error", 3)
        assert queue.exists()
        lines = queue.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["document_id"] == "doc-1"
        assert entry["error"] == "parse error"
        assert entry["attempts"] == 3


def _make_fake_response(content_dict):
    """Helper: build a fake LLM response with the given content."""
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(content_dict)))]
    return resp


def _viable_response(subj="SIRT1", rel="upregulates", obj="FOXO3"):
    return _make_fake_response({
        "relations": [{
            "extraction_viable": True,
            "subject_name": subj,
            "subject_type": "GENE",
            "relation": rel,
            "object_name": obj,
            "object_type": "GENE",
            "negated": False,
            "confidence": 0.9,
            "reasoning": "SIRT1 upregulates FOXO3. This is a very clear and explicit finding reported in the text with fifty characters.",
            "species": "Mus musculus",
            "tissue": "liver",
            "condition": "",
            "effect_size": "",
        }]
    })


def _nonviable_response():
    return _make_fake_response({
        "relations": [{
            "extraction_viable": False,
            "reasoning": "No clear biological relation between named entities was found in this text, which only describes methodological details of the experimental protocol used in the study.",
        }]
    })


class TestExtract:
    @pytest.fixture
    def minimal_chunk(self):
        return {
            "text": "SIRT1 upregulates FOXO3 in mouse liver.",
            "document_id": "doc-001",
            "source_name": "pubmed",
            "section": "results",
            "chunk_index": 0,
            "total_chunks": 1,
        }

    def test_happy_path_returns_extraction_result(self, minimal_chunk):
        fake_llm = MagicMock()
        fake_llm.chat.completions.create.return_value = _viable_response()
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
        ):
            result = extract(minimal_chunk)
        assert isinstance(result, ExtractionResult)
        assert len(result.relations) == 1
        assert result.relations[0].subject_name == "SIRT1"
        assert result.relations[0].relation == RelationType.UPREGULATES
        assert result.rejected is False

    def test_returns_non_viable_when_no_relation(self, minimal_chunk):
        fake_llm = MagicMock()
        fake_llm.chat.completions.create.return_value = _nonviable_response()
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
        ):
            result = extract(minimal_chunk)
        assert result.relations[0].extraction_viable is False

    def test_retries_on_parse_error(self, minimal_chunk):
        fake_llm = MagicMock()
        fake_llm.chat.completions.create.side_effect = [
            _make_fake_response({"invalid": True}),
            _viable_response(),
        ]
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
        ):
            result = extract(minimal_chunk)
        assert isinstance(result, ExtractionResult)
        assert len(result.relations) == 1
        assert fake_llm.chat.completions.create.call_count >= 2

    def test_all_retries_exhausted_returns_rejected(self, minimal_chunk):
        fake_llm = MagicMock()
        fake_llm.chat.completions.create.return_value = _make_fake_response({"bad": True})
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
            patch("src.extraction.extractor._log_rejected") as mock_log,
        ):
            result = extract(minimal_chunk)
        assert result.rejected is True
        assert result.rejection_reason is not None
        mock_log.assert_called_once()

    def test_truncation_splits_chunk(self, minimal_chunk):
        fake_llm = MagicMock()
        truncated = '{"relations": [{"extraction_viable": true, "subject_name": "SIRT1"'
        fake_llm.chat.completions.create.side_effect = [
            _make_fake_response({"raw": truncated}),
            _viable_response(),
            _nonviable_response(),
        ]
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
        ):
            result = extract(minimal_chunk, _depth=0)
        assert isinstance(result, ExtractionResult)
        assert len(result.relations) >= 1

    def test_timeout_retries_with_fresh_messages(self, minimal_chunk):
        from concurrent.futures import TimeoutError as CFTimeoutError

        fake_llm = MagicMock()
        fake_llm.chat.completions.create.side_effect = [
            CFTimeoutError("timed out"),
            _nonviable_response(),
        ]
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
            patch("src.extraction.extractor.time.sleep"),
        ):
            result = extract(minimal_chunk)
        assert isinstance(result, ExtractionResult)


class TestExtractBatch:
    def test_processes_multiple_chunks(self):
        chunks = [
            {"text": "SIRT1 upregulates FOXO3.", "document_id": "d1", "source_name": "pubmed",
             "section": "results", "chunk_index": 0, "total_chunks": 2},
            {"text": "No relation here.", "document_id": "d1", "source_name": "pubmed",
             "section": "results", "chunk_index": 1, "total_chunks": 2},
        ]
        fake_llm = MagicMock()
        fake_llm.chat.completions.create.return_value = _nonviable_response()
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
        ):
            results = extract_batch(chunks)
        assert len(results) == 2
        assert all(isinstance(r, ExtractionResult) for r in results)

    def test_returns_in_order(self):
        chunks = [
            {"text": f"Chunk {i} text.", "document_id": "d1", "source_name": "pubmed",
             "section": "results", "chunk_index": i, "total_chunks": 3}
            for i in range(3)
        ]
        fake_llm = MagicMock()
        fake_llm.chat.completions.create.return_value = _nonviable_response()
        with (
            patch("src.extraction.extractor._client", return_value=fake_llm),
            patch("src.extraction.extractor.httpx.get", return_value=MagicMock()),
        ):
            results = extract_batch(chunks)
        assert len(results) == 3
