"""Precision / recall / F1 for a run, through tools/verify_kg.py. run(db_path, out_dir, fetch_text,
formats) → {precision, recall, f1, tp, fp, fn, confirmed, total, html_path, json_path,
fn_examples}, or None when off or unavailable."""
from __future__ import annotations
import json
from pathlib import Path


def run(
    db_path:    Path | str | None = None,
    out_dir:    Path | str | None = None,
    fetch_text: bool  = True,
    formats:    str   = "neo4j",
) -> dict | None:
    """Run KG accuracy evaluation (precision / recall / F1)."""
    from tools.verify_kg import run_verification

    out_dir  = Path(out_dir)  if out_dir  else Path("data/kg_output")
    db_path  = Path(db_path)  if db_path  else None

    out_html = out_dir / "verification_report.html"
    out_json = out_dir / "verification_report.json"

    vresult = run_verification(
        fetch_text = fetch_text,
        db_path    = str(db_path) if db_path else None,
        out_html   = out_html,
        out_json   = out_json,
        formats    = formats,
    )

    # Load eval_report.json written by run_verification (contains recall metrics)
    eval_path = out_dir / "eval_report.json"
    eval_metrics: dict = {}
    if eval_path.exists():
        try:
            eval_metrics = json.loads(eval_path.read_text())
        except Exception:
            pass

    confirmed = vresult.get("confirmed", 0)
    mismatch  = vresult.get("mismatch",  0)
    total     = vresult.get("total",     0)

    precision = eval_metrics.get("precision", confirmed / (confirmed + mismatch) if (confirmed + mismatch) > 0 else 0.0)
    recall    = eval_metrics.get("recall",    0.0)
    f1        = eval_metrics.get("f1",        0.0)

    return {
        "precision":   round(float(precision), 4),
        "recall":      round(float(recall),    4),
        "f1":          round(float(f1),        4),
        "tp":          eval_metrics.get("tp", confirmed),
        "fp":          eval_metrics.get("fp", mismatch),
        "fn":          eval_metrics.get("fn", 0),
        "confirmed":   confirmed,
        "total":       total,
        "html_path":   out_html,
        "json_path":   out_json,
        "fn_examples": eval_metrics.get("fn_examples", []),
    }
