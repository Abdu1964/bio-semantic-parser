"""Ground figure relations in the figure's own text: OCR it, and keep only names it really contains."""
import logging
import os
import re

logger = logging.getLogger(__name__)

# OCR zoom levels; each misreads different small labels, so their union reads the most names exactly.
_SCALES = [float(s) for s in os.getenv("FIGURE_OCR_SCALES", "1,2").split(",") if s.strip()]
_TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


def figure_text(image: bytes) -> str:
    """OCR text of a figure image (all zoom levels joined), or '' when OCR is unavailable."""
    from src.figures.ocr import available
    if not available():
        return ""
    import pymupdf
    try:
        pix = pymupdf.Pixmap(image)
        doc = pymupdf.open()
        page = doc.new_page(width=pix.width, height=pix.height)       # 1 point per pixel
        page.insert_image(page.rect, pixmap=pix)
        return "\n".join(page.get_text(textpage=page.get_textpage_ocr(language="eng", dpi=int(72 * s), full=True))
                         for s in _SCALES)
    except Exception as e:
        logger.warning("Figure OCR failed: %s", e)
        return ""


def tokens(text: str) -> set:
    return set(_TOKEN.findall(text.lower()))


def grounded(name: str, known: set) -> bool:
    """Every word of the name appears exactly in the figure's OCR text or caption
    (no fuzzy match: RABEP1 and RABEP3 are different genes)."""
    words = _TOKEN.findall(name.lower())
    return bool(words) and all(w in known for w in words)
