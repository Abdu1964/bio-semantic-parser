"""Read a paper's figures again and update its results without rerunning the paper.

The figures are read with the current figure reader; only the figure facts go through Layer 7 (IDs,
validation, text check), the paper's text facts are taken from its Layer 7 checkpoint, and Layer 8
publishes the paper again (a new run folder, outputs and results page).

Usage: python -m tools.refresh_figures <checkpoint id> [--format both] [--dry-run]
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _emit_print(msg: dict):
    if msg.get("message"):
        print(f"[L{msg.get('layer', '?')}] {msg['message']}", flush=True)


def refresh(safe_id: str, output_format: str = "both", dry_run: bool = False) -> dict:
    from src.figures.vision.relations import figure_results
    from src.postextraction import staging
    from src.postextraction.postextractor import process as postprocess

    ck = Path("data/checkpoints") / safe_id
    tagged = json.loads((ck / "layer4_annotated.json").read_text(encoding="utf-8"))
    results = json.loads((ck / "layer6_results.json").read_text(encoding="utf-8"))
    old_fig_chunks = json.loads((ck / "layer6_figure_chunks.json").read_text(encoding="utf-8")) \
        if (ck / "layer6_figure_chunks.json").exists() else []
    processed = json.loads((ck / "layer7_processed.json").read_text(encoding="utf-8"))
    text_results = results[:len(results) - len(old_fig_chunks)]
    doc_id = tagged[0].get("document_id") or safe_id

    # 1. read the figures again
    paper_text = " ".join(str(c.get("text", "")) for c in tagged if c.get("section") != "figure")
    fig_results, fig_chunks = figure_results(doc_id, tagged[0], len(tagged), log=print, paper_text=paper_text)
    new_rels = [(r.subject_name, r.relation.value, r.object_name) for res in fig_results for r in res.relations]
    old_fig = [r for r in processed if r.get("section") == "figure"]
    summary = {"paper": safe_id, "old_figure_facts": len(old_fig), "new_figure_relations": len(new_rels),
               "new": new_rels}
    if dry_run:
        return summary

    # 2. the old figure facts leave the paper's staging DB (rows no text fact shares)
    staging_db = Path(f"data/staging/{safe_id}_{output_format}.db")
    text_ids = {r.get("triple_id") for r in processed if r.get("section") != "figure"}
    drop = [r.get("triple_id") for r in old_fig if r.get("triple_id") and r.get("triple_id") not in text_ids]
    if staging_db.exists() and drop:
        with sqlite3.connect(staging_db) as c:
            c.executemany("DELETE FROM triples WHERE id = ?", [(i,) for i in drop])

    # 3. Layer 7 for the figure facts only
    staging.use(staging_db)
    fig_processed = postprocess(list(zip(fig_results, fig_chunks))) if fig_results else []
    processed = [r for r in processed if r.get("section") != "figure"] + list(fig_processed)

    # 4. checkpoints, so a later resume uses the new figure facts too
    (ck / "layer6_results.json").write_text(json.dumps(
        text_results + [r.model_dump() for r in fig_results], default=str), encoding="utf-8")
    (ck / "layer6_figure_chunks.json").write_text(json.dumps(fig_chunks, default=str), encoding="utf-8")
    (ck / "layer7_processed.json").write_text(json.dumps(processed, default=str), encoding="utf-8")

    # 5. Layer 8 publishes the paper again
    from api.layers.layer8_publish import run_layer8
    meta = json.loads((ck / "meta.json").read_text(encoding="utf-8")) if (ck / "meta.json").exists() else {}
    run_layer8(_emit_print, doc_id, safe_id, processed, tagged + fig_chunks, staging_db, output_format,
               meta.get("title") or safe_id)
    summary["figure_facts_after_layer7"] = len(fig_processed)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("safe_id")
    ap.add_argument("--format", default="both")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = refresh(a.safe_id, a.format, a.dry_run)
    print(json.dumps({k: v for k, v in out.items() if k != "new"}, indent=1))
    for t in out.get("new", []):
        print("  ", " | ".join(t))
    sys.exit(0)
