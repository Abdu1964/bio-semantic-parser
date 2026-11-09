"""Relations read from a paper's saved figures, shaped like Layer 6 text results."""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from src.figures.extract import figures_dir
from src.figures.vision.grounding import tokens
from src.figures.vision.reader import read_figure
from src.schema.pydantic_model import ExtractionResult

logger = logging.getLogger(__name__)

_MAX_FIGURES = int(os.getenv("FIGURE_MAX_PER_PAPER", "30"))
_WORKERS = int(os.getenv("FIGURE_WORKERS", "2"))
_CHUNK_KEYS = ("document_id", "source_name", "source_url", "doi", "original_pdf_hash")


def enabled() -> bool:
    return os.getenv("FIGURE_RELATIONS_ENABLED", "true").lower() == "true"


def review_required(record: dict, text_facts: list = ()) -> bool:
    """Whether a figure fact needs a human, beyond the checks every fact passes (FIGURE_FACTS_REVIEW):
    'conflicting' (default) — only when it contradicts the paper's text; 'uncorroborated' — unless the text
    states the same fact; 'all'; 'none'."""
    if record.get("section") != "figure":
        return False
    mode = os.getenv("FIGURE_FACTS_REVIEW", "conflicting").lower()
    if mode in ("none", "false"):
        return False
    if mode in ("all", "true"):
        return True
    if mode == "uncorroborated":
        return corroborating_fact(record, text_facts) is None
    return conflicting_fact(record, text_facts) is not None


# Opposite directions, from the taxonomy's BioNLP event types
_OPPOSITE_EVENTS = {"Positive_regulation": "Negative_regulation", "Negative_regulation": "Positive_regulation"}


def conflicting_fact(record: dict, text_facts: list):
    """A fact from the paper's text about the same entities with the opposite direction, or the same
    relation with the opposite negation — e.g. a figure misread as 'downregulates' where the text says 'activates'."""
    event = _event_type(record.get("relation"))
    for t in text_facts:
        if t.get("section") == "figure" or not (_same_entity(record, t, "subject") and _same_entity(record, t, "object")):
            continue
        t_event = _event_type(t.get("relation"))
        same_negation = bool(t.get("negated")) == bool(record.get("negated"))
        if (same_negation and _OPPOSITE_EVENTS.get(event) == t_event) or (not same_negation and t_event == event):
            return t
    return None


def _event_type(relation) -> str:
    """The relation's standard BioNLP event type from the taxonomy, else the relation itself."""
    from src.schema.taxonomy import CROSS_SCHEMA_MAP, RelationType
    value = str(getattr(relation, "value", relation))
    try:
        return CROSS_SCHEMA_MAP.get(RelationType(value), {}).get("bionlp") or value
    except ValueError:
        return value


def _verified(cid) -> bool:
    return bool(cid) and not str(cid).startswith("TEXT:") and cid != "NEEDS_REVIEW"


def _names(record: dict, side: str) -> set:
    """The entity's name, its database name and the database's synonyms, as comparable keys."""
    from src.postextraction.name_matching import term_key
    syns = record.get(f"{side}_synonyms") or ""
    syns = syns.split(";") if isinstance(syns, str) else list(syns)
    names = [record.get(f"{side}_name"), record.get(f"{side}_canonical_name"), *syns]
    return {k for k in (term_key(str(n or "")) for n in names) if len(k) >= 2}


def _genus(species: str) -> str:
    """Genus of a species name, common names included ('human' → 'homo')."""
    from src.postextraction.entity_normalization import _COMMON_TO_BINOMIAL
    name = (species or "").strip().lower().replace("_", " ")
    return _COMMON_TO_BINOMIAL.get(name, name).split(" ")[0]


def _same_entity(a: dict, b: dict, side: str) -> bool:
    """Same ID; or, in the same known species, one molecule under two ID systems (gene vs protein),
    recognised by the databases' own names and synonyms."""
    if not (_verified(a.get(f"{side}_id")) and _verified(b.get(f"{side}_id"))):
        return False
    if a.get(f"{side}_id") == b.get(f"{side}_id"):
        return True
    ga, gb = _genus(a.get("species") or ""), _genus(b.get("species") or "")
    return bool(ga) and ga == gb and bool(_names(a, side) & _names(b, side))


def corroborating_fact(record: dict, text_facts: list):
    """A fact from the paper's text with the same entities, the same event type
    and the same negation as this figure fact, or None."""
    if not (_verified(record.get("subject_id")) and _verified(record.get("object_id"))):
        return None
    event = _event_type(record.get("relation"))
    for t in text_facts:
        if (t.get("section") != "figure" and _same_entity(record, t, "subject") and _same_entity(record, t, "object")
                and bool(t.get("negated")) == bool(record.get("negated"))
                and _event_type(t.get("relation")) == event):
            return t
    return None


def _figures(document_id: str) -> list:
    """Saved figures with an image, captioned ones first, at most _MAX_FIGURES."""
    d = figures_dir(document_id)
    index = d / "figures.json"
    if not index.exists():
        return []
    # A PDF figure without a caption (graphical abstract, logo) has nothing to anchor it; uploads are kept.
    figs = [f for f in json.loads(index.read_text(encoding="utf-8"))
            if f.get("file") and (f.get("caption") or f.get("source") != "pdf")]
    figs.sort(key=lambda f: not f.get("caption"))
    return [{**f, "path": str(d / f["file"])} for f in figs[:_MAX_FIGURES]]


def _chunk(fig: dict, out: dict, rels: list, template: dict, index: int) -> dict:
    """A pseudo-chunk for one figure. Its text is what Layer 7 checks figure facts against:
    caption, summary, the visual evidence for each relation, and the figure's own words (OCR)."""
    label = fig.get("label") or fig["fig_id"]
    text = f"{label}. {fig.get('caption', '')}".strip()
    if out["summary"]:
        text += f"\nFigure content: {out['summary']}"
    text += "\nShown in the figure:\n" + "\n".join(
        f"- {r.subject_name} {r.relation.value.replace('_', ' ')} {r.object_name}: {r.reasoning}" for r in rels)
    if out.get("ocr", "").strip():
        text += "\nText in the figure: " + " ".join(out["ocr"].split())[:3000]
    return {**{k: template.get(k) for k in _CHUNK_KEYS if k in template},
            "text": text, "section": "figure", "chunk_index": index,
            "entities": [], "negated_entities": [], "has_negation": False,
            "figure": {"id": fig["fig_id"], "label": fig.get("label", ""), "page": fig.get("page", 0),
                       "file": fig["path"], "kind": out["kind"]}}


def figure_results(document_id: str, template: dict, first_index: int, log=None, paper_text: str = "") -> tuple:
    """([ExtractionResult], [chunk]) for every figure that shows relations."""
    log = log or (lambda m: None)
    figs = _figures(document_id)
    if not figs:
        return [], []
    log(f"Reading {len(figs)} figure(s) with the vision model…")
    context = tokens(paper_text) if paper_text else None           # once per paper, shared by all figures

    def one(fig):
        try:
            with open(fig["path"], "rb") as fh:
                return fig, read_figure(fh.read(), fig.get("caption", ""), fig.get("label", ""), context)
        except Exception as e:
            logger.warning("Figure %s skipped: %s", fig["fig_id"], e)
            return fig, None

    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        read = list(ex.map(one, figs))

    results, chunks = [], []
    for fig, out in read:
        name = fig.get("label") or fig["fig_id"]
        if out is None:
            log(f"  {name}: could not be read")
            continue
        rels = []
        for panel, rel in out["relations"]:
            where = f"[{name}{', panel ' + panel if panel else ''}] "
            rels.append(rel.model_copy(update={"reasoning": where + rel.reasoning}))
        log(f"  {name} (page {fig.get('page') or '?'}): {out['kind']} — {len(rels)} relation(s)"
            + (f", {out['invalid']} invalid dropped" if out["invalid"] else "")
            + (f", {out['ungrounded']} with names not in the figure or paper dropped" if out.get("ungrounded") else "")
            + (f", {out['not_entities']} self-loops or group names dropped" if out.get("not_entities") else ""))
        if rels:
            results.append(ExtractionResult(relations=rels))
            chunks.append(_chunk(fig, out, rels, template, first_index + len(chunks)))
    return results, chunks


def add_figure_relations(document_id: str, tagged_chunks: list, results: list, save_path=None, log=None) -> int:
    """Append figure results and figure chunks in place (kept 1:1 for Layer 7); returns the relation count."""
    log = log or (lambda m: None)
    if not enabled():
        return 0
    if len(results) > len(tagged_chunks):
        log("Figure relations skipped: results and chunks are out of step")
        return 0
    results.extend(ExtractionResult(relations=[]) for _ in range(len(tagged_chunks) - len(results)))
    try:
        template = tagged_chunks[0] if tagged_chunks else {"document_id": document_id}
        paper_text = " ".join(str(c.get("text", "")) for c in tagged_chunks if c.get("section") != "figure")
        fig_results, fig_chunks = figure_results(document_id, template, len(tagged_chunks), log, paper_text)
    except Exception as e:
        log(f"Figure relations skipped: {e}")
        return 0
    results.extend(fig_results)
    tagged_chunks.extend(fig_chunks)
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(fig_chunks, default=str), encoding="utf-8")
    return sum(len(r.relations) for r in fig_results)
