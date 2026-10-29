"""Read a figure with the vision model: its kind, a short summary and the relations it shows."""
import base64
import logging
import os

from pydantic import ValidationError

from src.figures.vision.grounding import figure_text, grounded, tokens
from src.llm_client import call_llm, parse_json
from src.schema.pydantic_model import BiologicalRelation
from src.schema.taxonomy import ENTITY_TAXONOMY, TAXONOMY

logger = logging.getLogger(__name__)

KINDS = ("diagram", "chart", "table", "photo", "text", "other")
_MAX_SIDE = int(os.getenv("FIGURE_MAX_SIDE", "1600"))        # pixels per image sent to the model
_TILE_FROM = int(os.getenv("FIGURE_TILE_FROM", "1200"))      # larger figures also go as 4 zoomed tiles
_MAX_TOKENS = int(os.getenv("FIGURE_OUTPUT_TOKENS", "3000"))
_MAX_OCR_CHARS = 4000

_FORMAT = """\
Return ONLY this JSON:
{
  "kind": "diagram | chart | table | photo | text | other",
  "summary": "<2-5 sentences: what each panel shows, in words>",
  "relations": [
    {
      "extraction_viable": true,
      "panel": "<panel letter, or empty>",
      "subject_name": "<entity exactly as labelled in the figure>",
      "subject_type": "<EntityType value>",
      "relation": "<RelationType value>",
      "object_name": "<entity exactly as labelled in the figure>",
      "object_type": "<EntityType value>",
      "negated": false,
      "confidence": 0.0,
      "reasoning": "<min 50 chars — what in the figure shows this (arrow, link, highlighted cell, significant bar)>",
      "species": "", "tissue": "", "condition": "", "effect_size": ""
    }
  ]
}"""

_RULES = """\
RULES:
1. kind: diagram = pathway, mechanism, network, genome-track or schematic; chart = plot or heatmap;
   table = a table of findings; photo = microscopy, blot, gel or photograph; text = a page of text;
   other = logo or decoration. Give relations only for diagram, chart and table.
2. Extract only relations the FIGURE shows — arrows, drawn links, labelled associations, cells marked
   as significant or supported. Never add background knowledge or relations found only in the caption.
3. Subjects and objects are named biological entities as labelled in the figure (genes, variants such
   as rs IDs, proteins, cell types, diseases, chemicals). Never numbers, p-values, axis labels,
   method names or panel titles.
4. relation, subject_type and object_type MUST be values from the lists above.
5. One object per distinct subject–relation–object triple; be exhaustive but precise.
6. If the figure shows no relation, return an empty relations list.
7. The first image is the whole figure; any further images are zoomed quarters of it — use them to
   read small labels. Spell every name exactly as in TEXT FOUND IN THE FIGURE.
8. In tables, pair values from the SAME row only; follow each row across before moving to the next."""


def _compact(title: str, taxonomy: dict) -> str:
    """One line per type: definition and what it is not (the full examples would double the prompt)."""
    lines = [f"{title} — use ONLY values from this list:"]
    for key, entry in taxonomy.items():
        not_this = f" [NOT: {entry['not_this']}]" if entry.get("not_this") else ""
        lines.append(f"  {key.value}: {entry['definition']}{not_this}")
    return "\n".join(lines)


def _system_prompt() -> str:
    return ("You are a biomedical relation extraction expert reading a FIGURE from a scientific paper.\n\n"
            + _compact("RELATION TYPES", TAXONOMY) + "\n\n" + _compact("ENTITY TYPES", ENTITY_TAXONOMY)
            + "\n\n" + _FORMAT + "\n\n" + _RULES)


def _rgb(image: bytes):
    import pymupdf
    pix = pymupdf.Pixmap(image)
    if pix.alpha or pix.n > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix) if pix.n - pix.alpha > 3 else pymupdf.Pixmap(pix, 0)
    return pix


def _fit(pix, clip=None) -> bytes:
    """PNG of the pixmap (or a clip of it), scaled down to at most _MAX_SIDE."""
    import pymupdf
    w, h = (clip.width, clip.height) if clip else (pix.width, pix.height)
    f = min(1.0, _MAX_SIDE / max(w, h))
    out = pymupdf.Pixmap(pix, max(1, int(w * f)), max(1, int(h * f)), clip) if (clip or f < 1) else pix
    return out.tobytes("png")


def _images(image: bytes) -> list:
    """The whole figure, plus 4 overlapping zoomed quarters when it is large."""
    import pymupdf
    pix = _rgb(image)
    out = [_fit(pix)]
    if max(pix.width, pix.height) >= _TILE_FROM:
        w, h = pix.width, pix.height
        ox, oy = w // 10, h // 10                                      # 10% overlap
        for x0, y0 in ((0, 0), (w // 2 - ox, 0), (0, h // 2 - oy), (w // 2 - ox, h // 2 - oy)):
            out.append(_fit(pix, pymupdf.IRect(x0, y0, min(w, x0 + w // 2 + ox), min(h, y0 + h // 2 + oy))))
    return out


def read_figure(image: bytes, caption: str = "", label: str = "") -> dict:
    """{'kind', 'summary', 'relations': [(panel, BiologicalRelation)], 'invalid': n, 'ungrounded': n}."""
    user = (f"{label}. {caption}" if label else caption) or "(no caption)"
    ocr = figure_text(image)
    text = f"Caption: {user}"
    if ocr.strip():
        text += "\n\nTEXT FOUND IN THE FIGURE (OCR, exact spelling):\n" + " ".join(ocr.split())[:_MAX_OCR_CHARS]
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}
               for png in _images(image)]
    raw = call_llm([
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": content + [{"type": "text", "text": text + "\n\nReturn JSON only."}]},
    ], max_tokens=_MAX_TOKENS)
    data = parse_json(raw)
    kind = str(data.get("kind", "other")).strip().lower()
    kind = kind if kind in KINDS else "other"
    known = tokens(ocr + " " + user) if ocr.strip() else None    # no OCR → no grounding check
    relations, invalid, ungrounded = [], 0, 0
    if kind in ("diagram", "chart", "table"):
        for r in data.get("relations") or []:
            if not isinstance(r, dict) or r.get("extraction_viable") is False:
                continue
            panel = str(r.pop("panel", "") or "").strip()
            try:
                rel = BiologicalRelation(**r)
            except (ValidationError, TypeError) as e:
                invalid += 1
                logger.info("Figure relation dropped (invalid): %s", str(e)[:200])
                continue
            if known is not None and not (grounded(rel.subject_name, known) and grounded(rel.object_name, known)):
                ungrounded += 1                                       # a name the figure doesn't contain
                logger.info("Figure relation dropped (not in figure): %s -> %s", rel.subject_name, rel.object_name)
                continue
            relations.append((panel, rel))
    return {"kind": kind, "summary": str(data.get("summary", "")).strip(), "relations": relations,
            "invalid": invalid, "ungrounded": ungrounded}
