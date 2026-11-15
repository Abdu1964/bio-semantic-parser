"""Regression check across every saved paper: replay Layer 7 step 1 (entity IDs, species, negation, confidence)
with the current code and compare it with an accepted earlier snapshot. Run before every deploy.

  python tools/regression_check.py run <label> [--papers NAME ...] [--workers 3]
      replays each paper that has Layer 4 and Layer 6 checkpoints; writes data/regression/<label>/<paper>.json
  python tools/regression_check.py compare <base_label> <new_label>
      per-paper metrics and every changed ID or species; exits 1 when a paper got worse
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKPOINTS = ROOT / "data" / "checkpoints"
OUT = ROOT / "data" / "regression"
_UNRESOLVED = ("TEXT:", "NEEDS_REVIEW")
_WORSE = 0.01          # a share that drops by more than 1 point counts as worse


def papers() -> list:
    return sorted(p.name for p in CHECKPOINTS.iterdir()
                  if (p / "layer4_annotated.json").exists() and (p / "layer6_results.json").exists())


def _snapshot(records: list) -> list:
    keep = ("subject_name", "subject_type", "subject_id", "object_name", "object_type", "object_id",
            "species", "negated", "confidence", "section", "chunk_index")
    out = []
    for r in records:
        row = {k: r.get(k) for k in keep}
        row["relation"] = str(getattr(r.get("relation"), "value", r.get("relation"))).split(".")[-1].lower()
        row["certainty"] = next((v for k, v in (r.get("confidence_channels") or {}).items() if k.startswith("C2")), None)
        out.append(row)
    return out


def replay_one(paper: str, label: str) -> None:
    """Layer 7 step 1 on one paper's checkpoints, in this process."""
    sys.path.insert(0, str(ROOT))
    from src.postextraction.postextractor import process
    from src.schema.pydantic_model import BiologicalRelation, ExtractionResult
    ck = CHECKPOINTS / paper
    chunks = json.loads((ck / "layer4_annotated.json").read_text())
    chunks = chunks.get("chunks", chunks) if isinstance(chunks, dict) else chunks
    results = []
    for rec in json.loads((ck / "layer6_results.json").read_text()):
        rels = []
        for r in rec.get("relations", []):
            try:
                rels.append(BiologicalRelation(**r))
            except Exception:
                pass
        results.append(ExtractionResult(relations=rels, rejected=rec.get("rejected", False),
                                        rejection_reason=rec.get("rejection_reason")))
    if (ck / "layer6_figure_chunks.json").exists():
        chunks.extend(json.loads((ck / "layer6_figure_chunks.json").read_text()))

    class _Done(Exception):
        pass
    got = {}

    def after_step(step, data):
        if step == 1:
            got["records"] = data
            raise _Done
    try:
        process(list(zip(results, chunks)), resume_from_step=1, step_save_cb=after_step)
    except _Done:
        pass
    (OUT / label).mkdir(parents=True, exist_ok=True)
    (OUT / label / f"{paper}.json").write_text(json.dumps(_snapshot(got.get("records", [])), default=str))


def run(label: str, names: list, workers: int) -> None:
    def one(paper):
        done = subprocess.run([sys.executable, __file__, "_one", paper, label], cwd=ROOT,
                              capture_output=True, text=True)
        status = "ok" if done.returncode == 0 else "FAILED: " + done.stderr.strip().splitlines()[-1][:200]
        print(f"  {paper}: {status}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, names))


def metrics(rows: list) -> dict:
    sides = [r[f"{s}_id"] or "" for r in rows for s in ("subject", "object")]
    conf = [float(r["confidence"]) for r in rows if r.get("confidence") is not None]
    return {
        "facts": len(rows),
        "ids_resolved": sum(1 for i in sides if i and not i.startswith(_UNRESOLVED)) / max(len(sides), 1),
        "species_set": sum(1 for r in rows if r.get("species")) / max(len(rows), 1),
        "negated": sum(1 for r in rows if r.get("negated")),
        "hedged": sum(1 for r in rows if r.get("certainty") is not None and r["certainty"] <= 0.5),
        "confidence": statistics.mean(conf) if conf else 0.0,
    }


def compare(base: str, new: str) -> int:
    worse = []
    for path in sorted((OUT / new).glob("*.json")):
        old_path = OUT / base / path.name
        if not old_path.exists():
            continue
        a, b = json.loads(old_path.read_text()), json.loads(path.read_text())
        ma, mb = metrics(a), metrics(b)
        flags = [k for k in ("ids_resolved", "species_set") if mb[k] < ma[k] - _WORSE] + \
                (["facts"] if mb["facts"] < ma["facts"] else [])
        print(f"\n== {path.stem}" + (f"   WORSE: {', '.join(flags)}" if flags else ""))
        for k in ma:
            fmt = (lambda v: f"{v:.1%}") if k in ("ids_resolved", "species_set") else \
                  (lambda v: f"{v:.3f}") if k == "confidence" else str
            print(f"   {k:13} {fmt(ma[k]):>8} -> {fmt(mb[k]):>8}")
        ids = lambda rows: {(r[f"{s}_name"], r[f"{s}_type"]): r[f"{s}_id"] for r in rows for s in ("subject", "object")}
        ia, ib = ids(a), ids(b)
        for k in sorted(set(ia) & set(ib), key=str):
            if ia[k] != ib[k]:
                print(f"   id      {k[0]} ({k[1]}): {ia[k]} -> {ib[k]}")
        fact = lambda r: (r["subject_name"], r["relation"], r["object_name"], r.get("chunk_index"), r.get("section"))
        sa, sb = {fact(r): r.get("species") for r in a}, {fact(r): r.get("species") for r in b}
        for k in sorted(set(sa) & set(sb), key=str):
            if sa[k] != sb[k]:
                print(f"   species {k[0]} | {k[1]} | {k[2]}: {sa[k]!r} -> {sb[k]!r}")
        if flags:
            worse.append(path.stem)
    print(f"\n{len(worse)} paper(s) worse: {', '.join(worse) or 'none'}")
    return 1 if worse else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("label")
    r.add_argument("--papers", nargs="*")
    r.add_argument("--workers", type=int, default=3)
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("new")
    o = sub.add_parser("_one")
    o.add_argument("paper")
    o.add_argument("label")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.label, args.papers or papers(), args.workers)
    elif args.cmd == "compare":
        return compare(args.base, args.new)
    else:
        replay_one(args.paper, args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
