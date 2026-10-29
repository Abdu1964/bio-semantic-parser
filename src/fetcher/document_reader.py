"""One entry point: bytes of any supported document or archive → text plus what was read."""
from dataclasses import dataclass, field

from src.fetcher.archive import ArchiveError, pick_main, unpack
from src.fetcher.format_detector import ARCHIVES, FormatDetector
from src.fetcher.handlers import (BioCHandler, DocxHandler, EpubHandler, HTMLHandler, JSONHandler,
                                  MarkdownHandler, PDFHandler, TextHandler, XMLHandler)


class UnsupportedFormat(ValueError):
    """The document's format can't be read."""


@dataclass
class Document:
    text: str
    format: str                              # format of the file the text came from
    source_file: str = ""                    # chosen file inside an archive
    figures: list = field(default_factory=list)   # [(path, bytes)] image files, for figure extraction


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def _extract(fmt: str, data: bytes, text_field: str = None) -> str:
    if fmt == "pdf":
        return PDFHandler().extract(data)
    if fmt == "docx":
        return DocxHandler().extract(data)
    if fmt == "epub":
        return EpubHandler().extract(data)
    if fmt in ("jats", "xml"):
        return XMLHandler().extract(_decode(data), text_field)
    if fmt == "bioc":
        return BioCHandler().extract(data)
    if fmt == "html":
        return HTMLHandler().extract(_decode(data), text_field)
    if fmt == "json":
        return JSONHandler().extract(_decode(data), text_field)
    if fmt == "markdown":
        return MarkdownHandler().extract(_decode(data))
    if fmt == "text":
        return TextHandler().extract(_decode(data), text_field)
    if fmt == "image":
        from src.figures.ocr import image_text
        return image_text(data)
    raise UnsupportedFormat(f"unsupported format: {fmt}")


def read_document(data: bytes, filename: str = "", content_type: str = "", text_field: str = None) -> Document:
    """Detect the format, unpack archives safely, and extract the main document's text."""
    fmt = FormatDetector().detect(content_type, data=data, filename=filename)
    if fmt in ARCHIVES:
        try:
            (name, content), figures = pick_main(unpack(data, fmt, filename))
        except ArchiveError as e:
            raise UnsupportedFormat(f"archive rejected: {e}")
        inner = FormatDetector().detect(data=content, filename=name)
        if inner in ARCHIVES:
            raise UnsupportedFormat("no document found inside the archive")
        return Document(_extract(inner, content, text_field), inner, name, figures)
    if fmt == "unknown":
        if b"\x00" in data[:8000]:
            raise UnsupportedFormat("unknown binary format")
        fmt = "text"                          # readable text without a recognisable format
    return Document(_extract(fmt, data, text_field), fmt, filename)
