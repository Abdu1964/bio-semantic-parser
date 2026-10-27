"""Layer 8 — Output Layer Orchestrator: validation gate → KG write → verification."""
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from src.publish.validation_gate    import route, split_discards
from src.publish.human_review       import append as queue_for_review
from src.publish.atomspace_inserter import insert_to_atomspace
from src.publish.output_verifier    import verify_run

_KG_ROOT = Path(os.getenv("KG_OUTPUT_ROOT", "data/kg_output"))


def _slug(text: str, max_len: int = 40) -> str:
    """Convert text to a safe folder-name slug."""
    import re as _re
    s = _re.sub(r'[^\w\s-]', '', text.lower())
    s = _re.sub(r'[\s_-]+', '_', s).strip('_')
    return s[:max_len] if s else ""


def _doc_id_slug(doc_id: str) -> str:
    """
    Convert a document ID to a clean folder-name component.
    Keeps the ID readable and filesystem-safe:
      PMC13197831        → PMC13197831
      42281177           → PMID_42281177
      10.1101/2023.01.01 → DOI_10.1101_2023.01.01
      GSE210986          → GSE210986
      NCT04488601        → NCT04488601
      local/path/paper.pdf → paper
      sha256hex…         → PDF_abc123
    """
    import re as _re
    if not doc_id:
        return ""
    d = doc_id.strip()
    # PMC
    if _re.match(r"PMC\d+", d, _re.I):
        return d.upper()
    # PMID (6-9 digits)
    if _re.fullmatch(r"\d{6,9}", d):
        return f"PMID_{d}"
    # GEO
    if _re.match(r"GSE\d+", d, _re.I):
        return d.upper()
    # ClinicalTrials
    if _re.match(r"NCT\d+", d, _re.I):
        return d.upper()
    # DOI
    if d.startswith("10."):
        return "DOI_" + _re.sub(r"[/\\:?*\"<>|]", "_", d)[:40]
    # URL — extract meaningful part
    if d.startswith("http"):
        return "URL_" + _re.sub(r"[^\w]", "_", d.split("//", 1)[-1])[:30]
    # Local PDF path — use filename stem
    if d.endswith(".pdf") or "/" in d or "\\" in d:
        import os as _os
        return _os.path.splitext(_os.path.basename(d))[0][:40]
    # SHA-256 hash (64 hex chars) — local PDF without known name
    if len(d) >= 32 and _re.fullmatch(r"[0-9a-f]+", d, _re.I):
        return f"PDF_{d[:12]}"
    # Fallback — slug the raw value
    return _slug(d, 40)


_RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_|$)")


def all_run_dirs(root: Path = None) -> list:
    """Every run folder, flat or nested under {species}/{entity_id}/, newest first."""
    root = Path(root or _KG_ROOT)
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [d for d in dirnames if not d.startswith("unified")]
        for d in list(dirnames):
            if _RUN_NAME.match(d):
                found.append(Path(dirpath) / d)
                dirnames.remove(d)                 # don't walk into a run's own files
    return sorted(found, key=lambda p: p.name, reverse=True)


def run_dirs_for(doc_id: str, root: Path = None) -> list:
    """Run folders of one paper — same suffix the publisher writes (_doc_id_slug), newest first."""
    suffix = _doc_id_slug(doc_id).lower()
    return [d for d in all_run_dirs(root) if suffix and d.name.lower().endswith("_" + suffix)]


def _run_dir(paper_name: str = "", doc_id: str = "", species: str = "", entity_id: str = "") -> Path:
    """Create a timestamped run directory, nested under {species}/{entity_id}/ if given."""
    ts      = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    suffix  = _doc_id_slug(doc_id) or _slug(paper_name)
    name    = f"{ts}_{suffix}" if suffix else ts
    root    = _KG_ROOT
    if species and entity_id:
        root = root / _slug(species) / _slug(entity_id, max_len=64)
    run = root / name
    run.mkdir(parents=True, exist_ok=True)
    return run


def process(records: list, verbose: bool = False, text_by_doc: dict = None,
            formats: str = "both", paper_name: str = "", doc_id: str = "",
            species: str = "", entity_id: str = "") -> dict:
    """
    Run Layer 8 on all records from Layer 7.

    Args:
        records — list of post-extracted relation records
        verbose — print step-by-step progress

    Returns: summary dict with run_dir, neo4j, metta, counts
    """
    def log(msg: str):
        if verbose:
            print(f"  [Layer 8] {msg}")

    if not records:
        log("No records to process.")
        return {"auto_insert": 0, "human_review": 0,
                "neo4j": {}, "metta": {}, "run_dir": ""}

    # One timestamped directory for this entire run (with paper name suffix)
    # Auto-extract doc_id from records if not provided
    if not doc_id and records:
        doc_id = records[0].get("document_id", "") if records else ""
    run = _run_dir(paper_name, doc_id=doc_id, species=species, entity_id=entity_id)
    log(f"Run directory: {run}")

    # Build text lookup using BOTH document_id and chunk_index as key.
    # All chunks from the same paper share document_id — using doc_id alone
    # means only the last chunk's text is stored, breaking verification.
    # We store the primary text by doc_id (for the verifier) as a concatenation
    # of all chunk texts from that paper, so all entities are findable.
    if text_by_doc is None:
        from collections import defaultdict
        _chunks_by_doc: dict = defaultdict(list)
        for r in records:
            ct = r.get("_chunk_text", "")
            if ct:
                _chunks_by_doc[r.get("document_id", "")].append(ct)
        text_by_doc = {
            doc_id: " ".join(chunks)
            for doc_id, chunks in _chunks_by_doc.items()
        }

    # ── Validation Gate ───────────────────────────────────────────────────────
    log("Validation Gate")
    auto_insert, human_review = route(records)
    human_review, discarded = split_discards(human_review)
    auto_insert, generic = split_discards(auto_insert)     # generic entities can pass the validator too
    discarded += generic
    log(f"  → {len(auto_insert)} auto-insert  |  {len(human_review)} → human review  |  "
        f"{len(discarded)} discarded (rejected.jsonl)")
    if discarded:
        _write_rejected(discarded, run)
    _write_alignment(auto_insert + human_review + discarded, run)
    _write_fact_merges(auto_insert + human_review + discarded, run)

    # ── Sync gate decision back to staging DB ────────────────────────────────
    # Layer 7 Step 2 (deduplication) wrote ALL records to the staging DB before
    # semantic validation ran.  Now that the gate has decided, update the staging
    # DB so commit_staging_to_main sees the correct flagged_for_review values.
    import os as _os
    _staging_path = _os.getenv("TRIPLE_STORE_PATH", "")
    if _staging_path and _staging_path != "data/triple_store.db":
        import sqlite3 as _sq
        try:
            _sc = _sq.connect(_staging_path)
            # Rows were staged before repairs, re-resolution and species backfill — write final values.
            from src.postextraction.deduplication import sync_record
            for _r in auto_insert + human_review:
                _r["triple_id"] = sync_record(_sc, _r) or _r.get("triple_id")
            # Mark every staged triple as flagged by default, then clear the ones
            # that actually passed the gate.
            _sc.execute("UPDATE triples SET flagged_for_review=1")
            for _r in auto_insert:
                _tid = _r.get("triple_id")
                if _tid:
                    # Primary-key match — reliable even for TEXT: entity IDs
                    _sc.execute(
                        "UPDATE triples SET flagged_for_review=0 WHERE id=?",
                        (_tid,),
                    )
                else:
                    # Fallback: content match (no triple_id assigned)
                    _sid = _r.get("subject_id", "")
                    _oid = _r.get("object_id",  "")
                    _rel = _r.get("relation",   "")
                    _neg = 1 if _r.get("negated") else 0
                    if _sid and _oid and _rel:
                        _sc.execute(
                            "UPDATE triples SET flagged_for_review=0 "
                            "WHERE subject_id=? AND relation=? AND object_id=? AND negated=?",
                            (_sid, _rel, _oid, _neg),
                        )
            _sc.commit()
            _sc.close()
        except Exception:
            pass

    # ── Human Review Queue ────────────────────────────────────────────────────
    if human_review:
        n_queued = queue_for_review(human_review, run_dir=run, target_format=formats)
        log(f"  → {n_queued} record(s) written to human review queue")
        log(f"     Location: {run}/human_review.jsonl")

    # ── Auto-Insert → Atomspace ───────────────────────────────────────────────
    summary = {"auto_insert": 0, "human_review": len(human_review), "discarded": len(discarded),
               "neo4j": {}, "metta": {}, "run_dir": str(run)}

    if auto_insert:
        log(f"Writing {len(auto_insert)} record(s) to Atomspace (Neo4j CSV + MeTTa)…")
        result = insert_to_atomspace(auto_insert, run_dir=run, formats=formats)
        if formats in ("metta", "both"):
            _write_identity_atoms(run)
        summary["auto_insert"] = result["records"]
        summary["neo4j"]       = result["neo4j"]
        summary["metta"]       = result["metta"]

        log(f"  → Neo4j: {result['neo4j'].get('node_count',0)} nodes  "
            f"{result['neo4j'].get('edge_count',0)} edges")
        for f in result['neo4j'].get('node_files', []):
            log(f"     {Path(f).parent.name}/{Path(f).name}")
        for f in result['neo4j'].get('edge_files', []):
            log(f"     {Path(f).parent.name}/{Path(f).name}")
        log(f"  → MeTTa: {result['metta'].get('node_count',0)} nodes  "
            f"{result['metta'].get('edge_count',0)} edges")
        for f in result['metta'].get('edge_files', []):
            log(f"     {Path(f).parent.name}/{Path(f).name}")
    else:
        log("No records passed the validation gate — nothing auto-inserted.")

    # ── Output Verification ───────────────────────────────────────────────────
    if text_by_doc and auto_insert:
        log("Verifying generated output against original paper text…")
        verification = verify_run(run, text_by_doc)
        summary["verification"] = verification
        log(f"  → {verification['verified']}/{verification['total']} relations "
            f"verified against source paper")
        if verification["mismatched"] > 0:
            log(f"  → {verification['mismatched']} MISMATCH(ES) — check verification report")
            for r in verification["results"]:
                if not r.get("verified"):
                    log(f"     ✗ [{r.get('source_id','')}] -{r.get('label','')}→ "
                        f"[{r.get('target_id','')}]  —  {r.get('mismatch_reason','')}")
        for r in verification["results"]:
            if r.get("verified") and r.get("supporting_text"):
                log(f"  ✓ [{r.get('source_id','')}] -{r.get('label','')}→ [{r.get('target_id','')}]")
                log(f"    Source: \"{r['supporting_text'][:120]}\"")
    else:
        summary["verification"] = {"total": 0, "verified": 0, "mismatched": 0, "results": []}

    log(f"Layer 8 complete — output in: {run}")
    return summary


def export_csvs_from_db(staging_db: str, run_dir: str, formats: str = "neo4j") -> None:
    """Re-export Neo4j/MeTTa CSVs from the staging DB after human-review approval."""
    import sqlite3 as _sq
    from pathlib import Path as _Path

    sdb = _Path(staging_db)
    run = _Path(run_dir)
    if not sdb.exists():
        return

    try:
        conn = _sq.connect(str(sdb))
        conn.row_factory = _sq.Row
        rows = conn.execute(
            "SELECT * FROM triples WHERE flagged_for_review = 0"
        ).fetchall()
        conn.close()
    except Exception:
        return

    if not rows:                               # nothing approved left: empty the exports, don't keep stale ones
        import shutil as _sh
        for fmt in ("neo4j", "metta"):
            if formats in (fmt, "both"):
                _sh.rmtree(run / fmt, ignore_errors=True)
                (run / fmt).mkdir(parents=True, exist_ok=True)
        return

    records = [dict(r) for r in rows]

    try:
        import json as _json
        _safe    = (records[0].get("source_papers", "[]") if records else "[]")
        _doc_ids = _json.loads(_safe) if isinstance(_safe, str) else _safe
        _doc_id  = _doc_ids[0] if _doc_ids else ""
        if _doc_id:
            _ckpt = _Path("data/checkpoints") / _doc_id / "layer7_processed.json"
            if _ckpt.exists():
                _l7 = _json.loads(_ckpt.read_text(encoding="utf-8"))
                _ch_map = {
                    (r.get("subject_name",""), r.get("relation",""), r.get("object_name","")): r.get("confidence_channels", {})
                    for r in _l7
                }
                for rec in records:
                    key = (rec.get("subject_name",""), rec.get("relation",""), rec.get("object_name",""))
                    if not rec.get("confidence_channels") and key in _ch_map:
                        rec["confidence_channels"] = _ch_map[key]
    except Exception:
        pass

    try:
        insert_to_atomspace(records, run_dir=run, formats=formats)
        if formats in ("metta", "both"):
            _write_identity_atoms(run)
    except Exception:
        pass


def _write_alignment(records: list, run_dir) -> None:
    """Write concept merges to <run_dir>/alignment.jsonl."""
    import json as _json
    merges: dict = {}
    for r in records:
        for side in ("subject", "object"):
            for m in (r.get(f"{side}_alignment"), r.get(f"{side}_kg_identity")):
                if m and m.get("from"):
                    merges.setdefault((m["from"], m.get("to")), m)
    if not merges:
        return
    path = Path(run_dir) / "alignment.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for m in merges.values():
            f.write(_json.dumps(m, default=str) + "\n")


def _write_identity_atoms(run_dir) -> None:
    """metta/identity.metta from alignment.jsonl — call after each MeTTa export (it clears metta/)."""
    import json as _json
    src = Path(run_dir) / "alignment.jsonl"
    if not src.exists():
        return
    atoms = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = _json.loads(line)
        strength, conf = (1.0, 0.95) if m.get("method") in ("cross_reference", "synonym") \
            else (0.9, round(float(m.get("score") or 0.8), 3))          # strength by method
        atoms.append(f'(IdenticalTo (Concept "{m["from"]}") (Concept "{m["to"]}") (stv {strength} {conf}))')
    if atoms:
        metta_dir = Path(run_dir) / "metta"
        metta_dir.mkdir(parents=True, exist_ok=True)
        (metta_dir / "identity.metta").write_text("\n".join(atoms) + "\n", encoding="utf-8")


def _write_fact_merges(records: list, run_dir) -> None:
    """Write fact merges to <run_dir>/fact_alignment.jsonl."""
    import json as _json
    rows = [{"kept": {"subject": r.get("subject_name"), "relation": getattr(r.get("relation"), "value", r.get("relation")),
                      "object": r.get("object_name"), "subject_id": r.get("subject_id"), "object_id": r.get("object_id")},
             "merged": r["merged_facts"]}
            for r in records if r.get("merged_facts")]
    if not rows:
        return
    path = Path(run_dir) / "fact_alignment.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(_json.dumps(row, default=str) + "\n")


def _write_rejected(records: list, run_dir) -> None:
    """Append discarded triples to <run_dir>/rejected.jsonl for audit."""
    import json as _json
    from datetime import datetime, timezone
    path = Path(run_dir) / "rejected.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            row = {k: (getattr(v, "value", v)) for k, v in r.items() if not k.startswith("_")}
            row["discarded_at"] = now
            f.write(_json.dumps(row, default=str) + "\n")
