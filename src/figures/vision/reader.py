"""Read a figure with the vision model: its kind, a short summary and the relations it shows."""
import base64
import logging
import os

from pydantic import ValidationError

from src.llm_client import call_llm, parse_json
from src.schema.pydantic_model import BiologicalRelation
from src.schema.taxonomy import ENTITY_TAXONOMY, TAXONOMY

logger = logging.getLogger(__name__)

KINDS = ("diagram", "chart", "table", "photo", "text", "other")
_MAX_SIDE = int(os.getenv("FIGURE_MAX_SIDE", "1600"))        # pixels sent to the model
_MAX_TOKENS = int(os.getenv("FIGURE_OUTPUT_TOKENS", "3000"))

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
6. If the figure shows no relation, return an empty relations list."""


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


def _png(image: bytes) -> bytes:
    """RGB PNG no larger than _MAX_SIDE on its longest side."""
    import pymupdf
    pix = pymupdf.Pixmap(image)
    if pix.alpha or pix.n > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix) if pix.n - pix.alpha > 3 else pymupdf.Pixmap(pix, 0)
    while max(pix.width, pix.height) > _MAX_SIDE:
        pix.shrink(1)
    return pix.tobytes("png")


def read_figure(image: bytes, caption: str = "", label: str = "") -> dict:
    """{'kind', 'summary', 'relations': [(panel, BiologicalRelation)], 'invalid': n}."""
    url = "data:image/png;base64," + base64.b64encode(_png(image)).decode()
    user = (f"{label}. {caption}" if label else caption) or "(no caption)"
    raw = call_llm([
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                     {"type": "text", "text": f"Caption: {user}\n\nReturn JSON only."}]},
    ], max_tokens=_MAX_TOKENS)
    data = parse_json(raw)
    kind = str(data.get("kind", "other")).strip().lower()
    kind = kind if kind in KINDS else "other"
    relations, invalid = [], 0
    if kind in ("diagram", "chart", "table"):
        for r in data.get("relations") or []:
            if not isinstance(r, dict) or r.get("extraction_viable") is False:
                continue
            panel = str(r.pop("panel", "") or "").strip()
            try:
                relations.append((panel, BiologicalRelation(**r)))
            except (ValidationError, TypeError) as e:
                invalid += 1
                logger.info("Figure relation dropped (invalid): %s", str(e)[:200])
    return {"kind": kind, "summary": str(data.get("summary", "")).strip(), "relations": relations,
            "invalid": invalid}
