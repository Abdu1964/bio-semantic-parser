"""Routes: /api/human-review, /api/suggest-id, /api/human-review/action."""
import sys
from pathlib import Path

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

router = APIRouter()


@router.get("/api/human-review")
async def get_human_review(run_dir: str = ""):
    import json as _json
    try:
        rdir = Path(run_dir)
        if not rdir.is_absolute(): rdir = _ROOT / rdir
        hr_path = rdir / "human_review.jsonl"
        if not hr_path.exists():
            return JSONResponse([])
        records = [_json.loads(l) for l in hr_path.read_text().splitlines() if l.strip()]
        pending = [r for r in records if r.get("status", "PENDING") == "PENDING"]
        return JSONResponse(pending)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _clean_entity_name(name: str) -> str:
    """Remove PDF extraction artifacts from entity names before ontology lookup."""
    import re as _re
    s = _re.sub(r'-\s+', '', name)
    s = _re.sub(r'\.\d+\s*$', '', s)
    s = _re.sub(r'\s+', ' ', s).strip()
    return s


@router.get("/api/suggest-id")
def suggest_canonical_id(name: str = "", entity_type: str = "", context: str = ""):
    """Resolve a TEXT: entity to a canonical ID via the database normalization chain."""
    if not name:
        return JSONResponse({"id": None})
    try:
        from src.postextraction.entity_normalization import normalize_entity

        clean  = _clean_entity_name(name)
        etype  = entity_type.upper() if entity_type else "OTHER"
        result = normalize_entity(clean, etype, existing_id=None, context=context)
        canon  = result.get("canonical_id", "")
        source = result.get("id_source", "")

        if canon and not canon.startswith("TEXT:") and canon != "NEEDS_REVIEW":
            return JSONResponse({
                "id":            canon,
                "source":        source,
                "confidence":    "high",
                "verified":      True,
                "official_name": result.get("canonical_name", "") or clean,
                "source_url":    result.get("source_url", ""),
                "synonyms":      result.get("synonyms", ""),
            })

        # No database match — IDs never come from the LLM.
        return JSONResponse({"id": None})
    except Exception as e:
        return JSONResponse({"id": None, "error": str(e)})


@router.get("/api/suggest-correction")
def suggest_correction(
    subject: str = "", relation: str = "", object: str = "",
    subject_type: str = "", object_type: str = "",
    source_text: str = "", review_reason: str = "",
):
    """LLM-suggested correction for a SKIPPED/REJECTED triple."""
    if not subject or not relation or not object:
        return JSONResponse({"correction": None})
    try:
        import re as _re, json as _json
        from src.llm_client import call_llm

        # Taxonomy relations only
        try:
            from src.schema.taxonomy import RelationType
            valid_relations = ", ".join(sorted(r.value for r in RelationType))
        except Exception:
            valid_relations = ""

        context_line   = f'\nSource text: "{source_text[:400]}"' if source_text else ""
        reason_line    = f'\nValidation issue: "{review_reason[:300]}"' if review_reason else ""
        taxonomy_line  = f'\nVALID RELATION TYPES (relation field must be one of these):\n{valid_relations}' if valid_relations else ""

        prompt = (
            f'You are a biomedical knowledge graph expert. A relation was extracted from scientific text but failed validation.\n'
            f'Extracted triple:\n'
            f'  Subject: {subject} (type: {subject_type or "unknown"})\n'
            f'  Relation: {relation}\n'
            f'  Object: {object} (type: {object_type or "unknown"})\n'
            f'{context_line}{reason_line}{taxonomy_line}\n\n'
            f'Your task: suggest the corrected triple that accurately represents what the text states.\n'
            f'Rules:\n'
            f'- Keep entities that are correct; only change what is wrong\n'
            f'- The "relation" field MUST be one of the valid relation types listed above — never invent a new name\n'
            f'- Use OTHER only if no listed relation fits\n'
            f'- If the relation direction is reversed, swap subject and object\n'
            f'- If an entity name is vague, suggest the precise biological term\n\n'
            f'Respond ONLY with a valid JSON object:\n'
            f'{{"subject": "<corrected subject or same if correct>", '
            f'"relation": "<one relation from the valid list>", '
            f'"object": "<corrected object or same if correct>", '
            f'"explanation": "<one sentence explaining what was wrong and what you changed>", '
            f'"confidence": "<high|medium|low>"}}'
        )
        text = call_llm([{"role": "user", "content": prompt}], max_tokens=200, temperature=0.0)
        if isinstance(text, str):
            text = text.strip()
        m = _re.search(r'\{[^}]+\}', text, _re.DOTALL)
        if not m:
            return JSONResponse({"correction": None})
        data = _json.loads(m.group())
        if not data.get("relation"):
            return JSONResponse({"correction": None})
        return JSONResponse({"correction": data})
    except Exception as e:
        return JSONResponse({"correction": None, "error": str(e)})


import threading as _thr
_verification_locks: dict = {}
_verification_pending: dict = {}
_verification_locks_mu = _thr.Lock()

def _get_verification_lock(run_dir_str: str):
    with _verification_locks_mu:
        if run_dir_str not in _verification_locks:
            _verification_locks[run_dir_str] = _thr.Lock()
            _verification_pending[run_dir_str] = False
        return _verification_locks[run_dir_str]

def _take_verification_pending(run_dir_str: str) -> bool:
    """Atomically read-and-clear the pending flag. True means a re-run is needed."""
    with _verification_locks_mu:
        v = _verification_pending.get(run_dir_str, False)
        _verification_pending[run_dir_str] = False
        return v


def _acquire_or_mark_pending(run_dir_str: str, lock) -> bool:
    """Take the verification lock, or mark a re-run for the current holder — atomically."""
    with _verification_locks_mu:
        if lock.acquire(blocking=False):
            return True
        _verification_pending[run_dir_str] = True
        return False


def _release_unless_pending(run_dir_str: str, lock) -> bool:
    """Release the lock if no re-run is pending (True = done); else clear the flag and keep it."""
    with _verification_locks_mu:
        if _verification_pending.get(run_dir_str, False):
            _verification_pending[run_dir_str] = False
            return False
        lock.release()
        return True


@router.post("/api/human-review/action")
async def review_action(
    run_dir:    str = Form(...),
    staging_db: str = Form(...),
    records:    str = Form(...),
    action:     str = Form(...),
):
    """Approve or reject human review triples."""
    import json as _json
    try:
        recs    = _json.loads(records)
        hr_path = Path(run_dir)
        if not hr_path.is_absolute(): hr_path = _ROOT / hr_path
        hr_path = hr_path / "human_review.jsonl"

        sdb = Path(staging_db)
        if not sdb.is_absolute(): sdb = _ROOT / sdb

        def _rebuild_after_approve(sdb_path, run_dir_str):
            """Commit → export CSVs → rebuild graph → re-run verification."""
            try:
                from src.postextraction.deduplication import commit_staging_to_main
                from tools.visualize_graph import build_graph_data, render_html
                from src.publish.output_layer import export_csvs_from_db

                for fmt in ("neo4j", "metta"):
                    _sdb_fmt = sdb_path.parent / sdb_path.name.replace("_both.db", f"_{fmt}.db")
                    _sdb_use = sdb_path if sdb_path.name.endswith("_both.db") else (
                        _sdb_fmt if _sdb_fmt.exists() else sdb_path)
                    if _sdb_use.exists():
                        try: commit_staging_to_main(str(_sdb_use), formats=fmt)
                        except Exception: pass

                _run_path = Path(run_dir_str)
                if not _run_path.is_absolute(): _run_path = _ROOT / _run_path

                if _run_path.exists() and sdb_path.exists():
                    try: export_csvs_from_db(str(sdb_path), str(_run_path), formats="both")
                    except Exception: pass

                if _run_path.exists():
                    _neo4j_csv = _run_path / "neo4j"
                    if _neo4j_csv.exists():
                        try:
                            _n, _e = build_graph_data([_neo4j_csv])
                            render_html(_n, _e, _run_path / "graph.html", title="Paper Knowledge Graph")
                        except Exception: pass

                # Re-run verification so the report reflects the current approved set.
                # If verification is already running, mark it pending so it runs again
                lock = _get_verification_lock(run_dir_str)
                if _acquire_or_mark_pending(run_dir_str, lock):
                    done = False
                    try:
                        from tools.verify_kg import run_verification
                        _take_verification_pending(run_dir_str)
                        while True:
                            if _run_path.exists():
                                for fmt, out_html, out_json in [
                                    ("neo4j",
                                     _run_path / "verification_report.html",
                                     _run_path / "verification_report.json"),
                                    ("metta",
                                     _run_path / "verification_report_metta.html",
                                     _run_path / "verification_report_metta.json"),
                                ]:
                                    try:
                                        run_verification(
                                            fetch_text=True, formats=fmt,
                                            db_path=str(sdb_path) if sdb_path.exists() else None,
                                            out_html=out_html, out_json=out_json,
                                        )
                                    except Exception: pass
                            # Exit and lock release are atomic with new approvals marking "pending".
                            if _release_unless_pending(run_dir_str, lock):
                                done = True
                                break
                    finally:
                        if not done:
                            lock.release()
            except Exception: pass

        def _delayed_rebuild(sdb_path, rdir):
            import time as _t; _t.sleep(3)
            _rebuild_after_approve(sdb_path, rdir)

        if action == "approve":
            from src.publish.human_review import approve_records
            n = approve_records(recs, str(sdb), hr_path)
            _thr.Thread(target=_delayed_rebuild, args=(sdb, run_dir), daemon=True).start()
            return JSONResponse({"ok": True, "approved": n})
        else:
            import json as _js
            now  = __import__('datetime').datetime.utcnow().isoformat()
            keys = {(r.get("subject_name",""), r.get("relation",""), r.get("object_name","")) for r in recs}
            if hr_path.exists():
                lines = []
                for line in hr_path.read_text().splitlines():
                    try:
                        e = _js.loads(line)
                        k = (e.get("subject_name",""), e.get("relation",""), e.get("object_name",""))
                        if k in keys:
                            e["status"] = "REJECTED"; e["reviewed_at"] = now
                    except Exception: pass
                    lines.append(_js.dumps(e) if isinstance(e, dict) else line)
                hr_path.write_text("\n".join(lines) + "\n")
            _thr.Thread(target=_delayed_rebuild, args=(sdb, run_dir), daemon=True).start()
            return JSONResponse({"ok": True, "rejected": len(recs)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
