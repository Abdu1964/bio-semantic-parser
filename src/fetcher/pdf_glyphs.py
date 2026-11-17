"""Recover characters a PDF font does not map to Unicode (β, κ, °, − in symbol fonts).

PyMuPDF returns them as control codes or U+FFFD, and journal fonts give their glyphs private
names, so the glyph is read from its shape: the line it sits in is rendered and the vision
model names the marked character. Answers are cached per (embedded font program, glyph).
"""
import base64
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(os.getenv("GLYPH_CACHE_PATH", "data/glyph_cache.json"))
_ENABLED = os.getenv("PDF_GLYPH_REPAIR", "true").lower() == "true"
_MARK = "⟦?⟧"
_lock = threading.Lock()
_cache = None


def _unmapped(ch: str) -> bool:
    return ch == "�" or (ord(ch) < 32 and ch not in "\n\r\t\f")


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.debug("Glyph cache not saved: %s", e)


def _read_glyph(page, line_bbox, line_text: str) -> str:
    """The character at ⟦?⟧ in a rendered text line, as the vision model reads it ('' if unsure)."""
    from src.llm_client import call_llm, parse_json
    import pymupdf
    clip = pymupdf.Rect(line_bbox) + (-2, -2, 2, 2)
    png = page.get_pixmap(clip=clip, dpi=400).tobytes("png")
    prompt = (f"This image is one line of text from a scientific paper. Its text, with one character "
              f"unreadable, is:\n{line_text}\nWhich single character is at {_MARK}? It is usually a Greek "
              f'letter or a math symbol. Return JSON only: {{"char": "<that one Unicode character, or ? if unsure>"}}')
    reply = call_llm([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        {"type": "text", "text": prompt}]}], max_tokens=32, wall_timeout=60)
    try:
        ch = str(parse_json(reply).get("char", "")).strip()
    except (ValueError, AttributeError):
        return ""
    plain = ch.isascii() and ch.isalnum()                  # a symbol-font glyph is never a plain letter/digit
    return ch if len(ch) == 1 and ch not in "?□" and not plain and not _unmapped(ch) else ""


def repair_glyphs(page, text: str) -> str:
    """The page text with each unmapped character replaced by the character its glyph shows."""
    if not _ENABLED or not any(_unmapped(c) for c in text):
        return text
    try:
        # Fonts are keyed by their embedded program: two PDFs' subsets of one font number glyphs differently
        embedded = [(f[3].split("+")[-1], f[0]) for f in page.get_fonts()]
        font_ids = {}

        def font_id(name: str):
            if name not in font_ids:
                xref = next((x for base, x in embedded if base.startswith(name) or name.startswith(base)), None)
                buf = page.parent.extract_font(xref)[3] if xref else b""
                font_ids[name] = hashlib.sha1(buf).hexdigest()[:16] if buf else None
            return font_ids[name]

        gid_at = {(round(ch[2][0], 1), round(ch[2][1], 1)): (font_id(span["font"]), ch[1])
                  for span in page.get_texttrace() for ch in span["chars"] if ch[0] == 0xFFFD or ch[0] < 32}
        found = []                                         # (cache key, line bbox, line text) per unmapped char
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                chars = [ch for span in line["spans"] for ch in span["chars"]]
                for i, ch in enumerate(chars):
                    if _unmapped(ch["c"]):
                        font, gid = gid_at.get((round(ch["origin"][0], 1), round(ch["origin"][1], 1)), (None, None))
                        shown = "".join(_MARK if j == i else ("□" if _unmapped(c["c"]) else c["c"])
                                        for j, c in enumerate(chars))
                        found.append((f"{font}|{gid}" if font else None, line["bbox"], shown))
        if len(found) != sum(_unmapped(c) for c in text):
            return text                                    # orders don't line up — leave the page as is
        with _lock:
            cache = _load()
            new = False
            for key, bbox, shown in found:
                if key and key not in cache:
                    ch = _read_glyph(page, bbox, shown)
                    if ch:                                 # failed reads are retried next time
                        cache[key] = ch
                        new = True
            if new:
                _save()
        replacements = iter(cache.get(key) or "" if key else "" for key, _, _ in found)
        return "".join((next(replacements) or c) if _unmapped(c) else c for c in text)
    except Exception as e:                                 # never lose the page text over this
        logger.warning("Glyph repair skipped on page %s: %s", page.number + 1, e)
        return text
