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


def review_required(record: dict) -> bool:
    """Figure facts go to human review until their precision is measured (FIGURE_FACTS_REVIEW)."""
    return record.get("section") == "figure" and os.getenv("FIGURE_FACTS_REVIEW", "true").lower() == "true"


def _figures(document_id: str) -> list:
    """Saved figures with an image, captioned ones first, at most _MAX_FIGURES."""
    d = figures_dir(document_id)
    index = d / "figures.json"
    if not index.exists():
        return []
    figs = [f for f in json.loads(index.read_text(encoding="utf-8")) if f.get("file")]
    figs.sort(key=lambda f: not f.get("caption"))
    return [{**f, "path": str(d / f["file"])} for f in figs[:_MAX_FIGURES]]


def _chunk(fig: dict, summary: str, kind: str, template: dict, index: int) -> dict:
    """A pseudo-chunk for one figure: caption + summary as text, figure provenance attached."""
    label = fig.get("label") or fig["fig_id"]
    text = f"{label}. {fig.get('caption', '')}".strip()
    if summary:
        text += f"\nFigure content: {summary}"
    return {**{k: template.get(k) for k in _CHUNK_KEYS if k in template},
            "text": text, "section": "figure", "chunk_index": index,
            "entities": [], "negated_entities": [], "has_negation": False,
            "figure": {"id": fig["fig_id"], "label": fig.get("label", ""), "page": fig.get("page", 0),
                       "file": fig["path"], "kind": kind}}


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
            + (f", {out['ungrounded']} with names not in the figure or paper dropped" if out.get("ungrounded") else ""))
        if rels:
            results.append(ExtractionResult(relations=rels))
            chunks.append(_chunk(fig, out["summary"], out["kind"], template, first_index + len(chunks)))
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
