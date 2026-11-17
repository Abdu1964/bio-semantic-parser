"""Documents and archives: safety limits, format detection, text fidelity."""
import bz2
import gzip
import io
import lzma
import zipfile

import pytest

from src.fetcher import archive
from src.fetcher.archive import ArchiveError, pick_main, unpack
from src.fetcher.document_reader import read_document
from src.fetcher.format_detector import FormatDetector
from src.fetcher.handlers import DocxHandler, EpubHandler, MarkdownHandler


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,pack", [("bz2", bz2.compress), ("xz", lzma.compress)])
def test_bz2_and_xz_unpack(fmt, pack):
    assert unpack(pack(b"Results text"), fmt, f"paper.txt.{fmt}") == [("paper.txt", b"Results text")]


def test_nested_archives_share_one_budget(monkeypatch):
    monkeypatch.setattr(archive, "MAX_TOTAL", 1500)
    inner = gzip.compress(b"x" * 1000)                  # each child fits the limit on its own
    with pytest.raises(ArchiveError):
        unpack(_zip({"a.txt.gz": inner, "b.txt.gz": inner}), "zip", "bundle.zip")


def test_fallback_skips_binary_members():
    members = [("paper.json", b'{"title": "A study", "abstract": "Text"}'), ("data.bin", b"\x00\x01" * 5000)]
    assert pick_main(members)[0][0] == "paper.json"


def test_text_inside_gzip_is_read():
    doc = read_document(gzip.compress(b"Plain results text about IL6."), filename="paper.gz")
    assert "IL6" in doc.text


def test_html5_article_is_html_and_bioc_needs_passages():
    d = FormatDetector()
    assert d.detect(data=b"<!DOCTYPE html><html><body><article>x</article></body></html>") == "html"
    assert d.detect(data=b'{"documents":[{"abstract":"Study text"}]}') == "json"
    assert d.detect(data=b'{"documents":[{"passages":[{"text":"x"}]}]}') == "bioc"
    assert d.detect(data=b"[Study](https://example.com) results", filename="notes.md") == "markdown"


def test_docx_keeps_tabs_and_line_breaks():
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = (f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>Results</w:t><w:br/><w:t>We found</w:t>'
            f'<w:tab/><w:t>IL6</w:t></w:r></w:p></w:body></w:document>')
    assert DocxHandler().extract(_zip({"word/document.xml": body})) == "Results\nWe found\tIL6"


def test_epub_href_is_decoded():
    opf = ('<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c1" href="Text/chapter%201.xhtml#top"/>'
           '</manifest><spine><itemref idref="c1"/></spine></package>')
    epub = _zip({"META-INF/container.xml": '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
                 "OEBPS/content.opf": opf, "OEBPS/Text/chapter 1.xhtml": "<html><body><p>Chapter one text</p></body></html>"})
    assert "Chapter one text" in EpubHandler().extract(epub)


def test_markdown_keeps_literal_symbols():
    out = MarkdownHandler().extract("> quote\np > 0.05 for HLA_A, *bold* and `code`")
    assert out == "quote\np > 0.05 for HLA_A, bold and code"


def test_upload_ids_with_brackets(tmp_path, monkeypatch):
    from api import pipeline_utils
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "uploads").mkdir(parents=True)
    (tmp_path / "data" / "uploads" / "paper[1].pdf").write_bytes(b"%PDF-1.4")
    src, doc_id, path = pipeline_utils._resolve_input("text", "paper[1]", "", [{"name": "pmc"}], None)
    assert doc_id == "paper[1]" and path.endswith("paper[1].pdf")
