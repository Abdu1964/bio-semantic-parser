"""Detect a document's format: file signature first, then file name, then Content-Type."""
import io
import json
import zipfile

ARCHIVES = {"zip", "tar", "gzip", "bz2", "xz"}

_EXTENSIONS = {
    ".pdf": "pdf", ".docx": "docx", ".epub": "epub", ".zip": "zip", ".tar": "tar", ".tgz": "gzip",
    ".gz": "gzip", ".bz2": "bz2", ".xz": "xz", ".nxml": "jats", ".xml": "xml", ".html": "html",
    ".htm": "html", ".json": "json", ".md": "markdown", ".markdown": "markdown", ".txt": "text",
}


class FormatDetector:

    SUPPORTED_FORMATS = ["pdf", "docx", "epub", "jats", "bioc", "xml", "html", "json", "markdown", "text",
                         *sorted(ARCHIVES)]

    def detect(self, content_type: str = "", data: bytes = None, filename: str = "") -> str:
        """Format name, or 'unknown'."""
        kind = self._from_bytes(data) if data else None
        if kind == "json":                                   # '{' or '[' alone does not make JSON (Markdown links)
            kind = self._json_kind(data)
        return kind \
            or self._from_name(filename) \
            or self._from_content_type(content_type or "") \
            or "unknown"

    @staticmethod
    def _json_kind(data: bytes):
        """'bioc' for a BioC collection (documents with passages), 'json' for other valid JSON, else None."""
        try:
            obj = json.loads(data.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError):
            return None
        collections = obj if isinstance(obj, list) else [obj]
        bioc = any(isinstance(c, dict) and isinstance(c.get("documents"), list) and any(
            isinstance(d, dict) and isinstance(d.get("passages"), list) for d in c["documents"]) for c in collections)
        return "bioc" if bioc else "json"

    # ── signatures ──
    def _from_bytes(self, data: bytes):
        head = data[:600]
        if head.startswith(b"%PDF"):
            return "pdf"
        if head.startswith(b"PK\x03\x04"):
            return self._zip_kind(data)
        if head.startswith(b"\x1f\x8b"):
            return "gzip"
        if head.startswith(b"BZh"):
            return "bz2"
        if head.startswith(b"\xfd7zXZ\x00"):
            return "xz"
        if len(data) > 262 and data[257:262] == b"ustar":
            return "tar"
        return self._text_kind(head)

    @staticmethod
    def _zip_kind(data: bytes) -> str:
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return "zip"
        if "word/document.xml" in names:
            return "docx"
        if "META-INF/container.xml" in names:
            return "epub"
        return "zip"

    @staticmethod
    def _text_kind(head: bytes):
        try:
            text = head.decode("utf-8", errors="strict").lstrip("﻿ \t\r\n")
        except UnicodeDecodeError:
            return None
        low = text.lower()
        if low.startswith("<?xml") or low.startswith("<!doctype") or low.startswith("<"):
            if "<html" in low or "<!doctype html" in low:      # HTML5 pages may contain <article>
                return "html"
            if "jats" in low or "<article" in low:
                return "jats"
            if "<collection" in low and ("bioc" in low or "<document" in low or "<source" in low):
                return "bioc"
            return "xml"
        if low.startswith("{") or low.startswith("["):
            return "json"                                     # checked against the full content in detect()
        return None

    # ── fallbacks ──
    @staticmethod
    def _from_name(filename: str):
        name = (filename or "").lower().split("?")[0]
        if name.endswith((".tar.gz", ".tgz")):
            return "gzip"
        for ext, fmt in _EXTENSIONS.items():
            if name.endswith(ext):
                return fmt
        return None

    @staticmethod
    def _from_content_type(content_type: str):
        ct = content_type.lower()
        for key, fmt in (("pdf", "pdf"), ("zip", "zip"), ("gzip", "gzip"), ("x-tar", "tar"),
                         ("wordprocessingml", "docx"), ("epub", "epub"), ("json", "json"),
                         ("xml", "xml"), ("html", "html"), ("markdown", "markdown"), ("text", "text")):
            if key in ct:
                return fmt
        return None
