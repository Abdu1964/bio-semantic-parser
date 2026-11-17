"""OCR for scanned PDF pages (Tesseract through PyMuPDF)."""
import logging
import os

logger = logging.getLogger(__name__)

_ENABLED = os.getenv("OCR_ENABLED", "true").lower() == "true"
_LANG = os.getenv("OCR_LANG", "eng")
_DPI = int(os.getenv("OCR_DPI", "300"))
_MIN_TEXT = 20                    # fewer chars than this → no real text layer
_MIN_COVER = 0.5                  # images covering this share of the page → scanned

_available = None


def available() -> bool:
    """Tesseract and its language data can be found."""
    global _available
    if _available is None:
        try:
            import pymupdf
            _available = bool(pymupdf.get_tessdata())
        except Exception:
            _available = False
        if _ENABLED and not _available:
            logger.warning("OCR unavailable: Tesseract not found — scanned pages will have no text")
    return _available


def image_cover(page) -> float:
    """Share of the page area covered by images."""
    area = abs(page.rect) or 1
    return min(1.0, sum(abs(page.rect & info["bbox"]) for info in page.get_image_info()) / area)


def is_scanned(page, text: str = None) -> bool:
    """No usable text layer, and the page is mostly an image."""
    text = page.get_text() if text is None else text
    return len(text.strip()) < _MIN_TEXT and image_cover(page) >= _MIN_COVER


def image_text(data: bytes) -> str:
    """OCR text of an image file (each frame of a multi-page TIFF is a page)."""
    import pymupdf
    if not _ENABLED or not available():
        raise RuntimeError("OCR is unavailable (Tesseract not installed or OCR_ENABLED=false)")
    pdf = pymupdf.open("pdf", pymupdf.open(stream=data).convert_to_pdf())
    from src.fetcher.handlers import PAGE_BREAK
    return PAGE_BREAK.join(page.get_text(textpage=page.get_textpage_ocr(language=_LANG, dpi=_DPI, full=True))
                           for page in pdf)


def page_text(page) -> str:
    """The page's text; OCR when the page is a scan."""
    text = page.get_text()
    if not _ENABLED or len(text.strip()) >= _MIN_TEXT:
        return text
    try:                                       # OCR never breaks plain text extraction
        if not is_scanned(page, text) or not available():
            return text
        return page.get_text(textpage=page.get_textpage_ocr(language=_LANG, dpi=_DPI, full=True))
    except Exception as e:
        logger.warning("OCR failed on page %s: %s", getattr(page, "number", -1) + 1, e)
        return text
