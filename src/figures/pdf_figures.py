"""Figures from a PDF: image and vector-drawing areas grouped under their "Figure N" captions."""
import os
import re
from collections import Counter

from src.figures.figure import Figure
from src.figures.ocr import is_scanned

# "Figure 2. Title", "Fig. 2: ...", "FIGURE 2 Title" — not body text like "Figure 2 shows".
CAPTION_RE = re.compile(r"^\s*(?:Supplementary\s+)?(?:Fig(?:ure)?s?\.?|FIGURE)\s*(S?\d+[A-Za-z]?)"
                        r"(?:\s*[.:|]\s*|\s+(?=[A-Z(\[]))")
_DPI = int(os.getenv("FIGURE_DPI", "300"))       # small figure labels need 300 dpi to be legible
_MIN_SIDE = 60                   # points; smaller areas are icons, bullets or rules
_MIN_AREA = 0.02                 # share of the page for one piece of figure content
_MIN_UNCAPTIONED = 0.10          # an uncaptioned figure must be at least this large
_MAX_TEXT = 0.5                  # drawing clusters mostly covered by text are tables or boxes
_REPEATED = 3                    # same image on this many pages → logo or banner
_PAD = 12                        # points around a figure searched for its labels
_MAX_LABEL = 120                 # chars; longer text blocks are body text


def _captions(page) -> list:
    """[(rect, label, text)] for caption blocks on the page."""
    import pymupdf
    out = []
    for x0, y0, x1, y1, text, _, kind in page.get_text("blocks"):
        m = CAPTION_RE.match(text) if kind == 0 else None
        if m:
            out.append((pymupdf.Rect(x0, y0, x1, y1), f"Figure {m.group(1)}", " ".join(text.split())))
    return out


def _distance(a, b) -> float:
    dx = max(0.0, b.x0 - a.x1, a.x0 - b.x1)
    dy = max(0.0, b.y0 - a.y1, a.y0 - b.y1)
    return (dx * dx + dy * dy) ** 0.5


def _content(page, image_infos, pages_per_xref, captions) -> list:
    """[(rect, is_raster)] figure content on the page: images and vector-drawing clusters."""
    import pymupdf
    area = abs(page.rect)
    big = lambda r: r.width >= _MIN_SIDE and r.height >= _MIN_SIDE and abs(r) >= _MIN_AREA * area
    rects = [(pymupdf.Rect(i["bbox"]) & page.rect, True) for i in image_infos
             if pages_per_xref.get(i["xref"], 0) < _REPEATED]
    try:
        clusters = page.cluster_drawings()
    except Exception:
        clusters = []
    if clusters:
        caption_rects = [c[0] for c in captions]
        text = [pymupdf.Rect(b[:4]) for b in page.get_text("blocks")
                if b[6] == 0 and not any(pymupdf.Rect(b[:4]) == c for c in caption_rects)]
        for r in clusters:
            r = r & page.rect
            if big(r) and sum(abs(r & t) for t in text) <= _MAX_TEXT * abs(r):
                rects.append((r, False))
    return [(r, raster) for r, raster in rects if big(r)]


def _with_labels(page, rect, captions):
    """Grow a figure area to take in short text touching it (panel letters, axis labels)."""
    import pymupdf
    near = pymupdf.Rect(rect.x0 - _PAD, rect.y0 - _PAD, rect.x1 + _PAD, rect.y1 + _PAD)
    caption_rects = [c[0] for c in captions]
    out = pymupdf.Rect(rect)
    for b in page.get_text("blocks"):
        r = pymupdf.Rect(b[:4])
        if b[6] == 0 and len(b[4]) <= _MAX_LABEL and r.intersects(near) and r not in caption_rects:
            out |= r
    return out & page.rect


def _render(page, rect) -> bytes:
    return page.get_pixmap(clip=rect, dpi=_DPI).tobytes("png")


def extract_pdf_figures(data: bytes) -> list:
    """One Figure per caption (plus large uncaptioned figures); captions on the next page are paired back."""
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    infos = [page.get_image_info(xrefs=True) for page in doc]
    pages_per_xref = Counter(x for page_infos in infos
                             for x in {i["xref"] for i in page_infos if i["xref"]})
    pages = []                    # per page: (captions, {caption index or -1: rect}, uncaptioned has raster)
    for page, page_infos in zip(doc, infos):
        if is_scanned(page):
            pages.append(([], {}, False))       # OCR page, not a figure
            continue
        captions = _captions(page)
        groups, raster = {}, set()
        for r, is_raster in _content(page, page_infos, pages_per_xref, captions):
            key = min(range(len(captions)), key=lambda i: _distance(r, captions[i][0])) if captions else -1
            groups[key] = groups[key] | r if key in groups else pymupdf.Rect(r)
            if is_raster:
                raster.add(key)
        groups = {k: _with_labels(page, r, captions) for k, r in groups.items()}
        pages.append((captions, groups, -1 in raster))

    figures = []
    for n, page in enumerate(doc):
        captions, groups, _ = pages[n]
        prev = pages[n - 1][1] if n else {}
        for i, (_, label, text) in enumerate(captions):
            if i in groups:
                figures.append((n, groups[i], label, text, "image"))
            elif -1 in prev:                     # figure on the previous page, legend here
                figures.append((n - 1, prev.pop(-1), label, text, "image"))
            else:                                # no content found: keep the whole page
                figures.append((n, page.rect, label, text, "page"))
    for n, (_, groups, has_raster) in enumerate(pages):
        r = groups.get(-1)                       # never got a caption: keep large real images only
        if r is not None and has_raster and abs(r) >= _MIN_UNCAPTIONED * abs(doc[n].rect):
            figures.append((n, r, "", "", "image"))

    figures.sort(key=lambda f: f[0])
    return [Figure(fig_id=f"p{n + 1}_fig{k}", label=label, caption=text, page=n + 1, source="pdf",
                   image=_render(doc[n], rect), region=region)
            for k, (n, rect, label, text, region) in enumerate(figures, 1)]
