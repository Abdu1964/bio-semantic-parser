"""Routes: /api/file, /api/discard-paper, /api/checkpoint-status, /api/config, /api/resume-from-layer, /api/results, /api/status."""
import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from api.run_state import _connections

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

router = APIRouter()


@router.get("/api/file")
async def serve_file(path: str):
    path = path.split("?")[0]  # strip cache-busting ?t= query string
    if Path(path).is_absolute():
        import re as _re
        m = _re.search(r'/(data|frontend|config|src|tools|lib|tests|docs)/', path)
        if m:
            path = path[m.start() + 1:]
    p = Path(path)
    if not p.is_absolute():
        p = _ROOT / p
    p = p.resolve()
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
    try:
        p.relative_to(_ROOT.resolve())
    except ValueError:
        return JSONResponse({"error": "Access denied"}, status_code=403)

    if p.suffix.lower() == ".html":
        import re as _re
        content = p.read_text(encoding="utf-8", errors="replace")

        def _to_api(fpath: str) -> str:
            return "/api/file?path=" + fpath.replace("file://", "")

        content = _re.sub(
            r'(src|href|value)=(["\'])file://([^"\']+)\2',
            lambda m: f'{m.group(1)}={m.group(2)}{_to_api("file://" + m.group(3))}{m.group(2)}',
            content,
        )
        content = _re.sub(
            r"'(file:///[^']*)'",
            lambda m: f"'{_to_api(m.group(1))}'",
            content,
        )
        content = _re.sub(
            r'"(file:///[^"]*)"',
            lambda m: f'"{_to_api(m.group(1))}"',
            content,
        )
        def _strip_machine_prefix(m):
            prefix, abs_path = m.group(1), m.group(2)
            match = _re.search(r'/(data|frontend|config|src|tools|lib|tests|docs)/', abs_path)
            if match:
                return prefix + abs_path[match.start() + 1:]
            return m.group(0)
        content = _re.sub(
            r'(/api/file\?path=)((?:/[A-Za-z][^"\'&\s]*)?/(?:Users|home|root|mnt)/[^"\'&\s]+)',
            _strip_machine_prefix,
            content,
        )
        return HTMLResponse(content)

    return FileResponse(str(p))


@router.post("/api/discard-paper")
async def discard_paper(doc_id: str = Form(...)):
    """Delete all outputs for a paper so it can be reprocessed from scratch."""
    try:
        import sqlite3 as _sq
        kg_root = _ROOT / "data" / "kg_output"
        safe_id = doc_id.replace("/", "_").replace(":", "_")

        deleted_dirs = []
        for d in kg_root.glob(f"*{safe_id[:12]}*"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                deleted_dirs.append(d.name)

        staging = _ROOT / "data" / "staging" / f"{safe_id}_both.db"
        if staging.exists(): staging.unlink()

        proc_db = _ROOT / "data" / "processed_ids.db"
        if proc_db.exists():
            try:
                con = _sq.connect(str(proc_db))
                con.execute("DELETE FROM processed_ids WHERE doc_id=?", (doc_id,))
                con.commit(); con.close()
            except Exception: pass

        for db_name in ["triple_store.db", "triple_store_neo4j.db", "triple_store_metta.db"]:
            db_path = _ROOT / "data" / db_name
            if db_path.exists():
                try:
                    con = _sq.connect(str(db_path))
                    con.execute("DELETE FROM triples WHERE source_papers LIKE ?", (f"%{doc_id}%",))
                    con.commit(); con.close()
                except Exception: pass

        ckpt_root = _ROOT / "data" / "checkpoints"
        for ck in ckpt_root.glob(f"{safe_id[:12]}*"):
            shutil.rmtree(ck, ignore_errors=True) if ck.is_dir() else ck.unlink()

        return {"ok": True, "deleted_dirs": deleted_dirs, "doc_id": doc_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/checkpoint-status")
async def checkpoint_status(doc_id: str = "", filename: str = ""):
    """Return checkpoint state so the frontend can show the layer selector before running."""
    try:
        import json as _j
        ckpt_root = _ROOT / "data" / "checkpoints"

        if doc_id:
            _safe = doc_id.replace("/", "_").replace(":", "_")
            ckpt  = ckpt_root / _safe / "meta.json"
            if ckpt.exists():
                meta       = _j.loads(ckpt.read_text())
                completed  = meta.get("completed_layers", [])
                next_layer = None if 8 in completed else next((l for l in range(3, 9) if l not in completed), None)
                substeps   = meta.get("substeps", {})
                return {"has_checkpoint": True, "completed_layers": completed,
                        "next_layer": next_layer, "doc_id": doc_id,
                        "substeps": substeps}

        if filename and ckpt_root.exists():
            fname_stem = filename.lower().replace(".pdf", "").replace(".txt", "")
            for meta_path in ckpt_root.glob("*/meta.json"):
                folder_name = meta_path.parent.name.lower()
                try:
                    meta       = _j.loads(meta_path.read_text())
                    stored_url = (meta.get("url") or "").lower()
                    if fname_stem in folder_name or folder_name in fname_stem or fname_stem in stored_url:
                        completed  = meta.get("completed_layers", [])
                        next_layer = None if 8 in completed else next((l for l in range(3, 9) if l not in completed), None)
                        found_id   = meta_path.parent.name
                        return {"has_checkpoint": True, "completed_layers": completed,
                                "next_layer": next_layer, "doc_id": found_id}
                except Exception:
                    continue

        return {"has_checkpoint": False}
    except Exception as exc:
        return {"has_checkpoint": False, "error": str(exc)}


@router.get("/api/config")
async def get_config():
    return {"enable_pln": os.getenv("ENABLE_PLN", "false").lower() == "true"}


@router.post("/api/resume-from-layer")
async def resume_from_layer(
    doc_id: str = Form(...),
    from_layer: int = Form(...),
    from_substep: int = Form(0),
):
    """Reset checkpoint so pipeline resumes from from_layer (optionally from_substep) on next run."""
    try:
        import json as _j
        _safe_doc = doc_id.replace("/", "_").replace(":", "_")
        ckpt_meta = _ROOT / "data" / "checkpoints" / _safe_doc / "meta.json"
        if not ckpt_meta.exists():
            return {"ok": True, "msg": "No checkpoint — pipeline will run from scratch"}

        meta           = _j.loads(ckpt_meta.read_text())
        old_layers     = meta.get("completed_layers", [])
        effective_from = max(from_layer, 3)
        ckpt_dir       = ckpt_meta.parent
        _layer_files   = {
            3: "layer3_chunks.json",
            4: "layer4_annotated.json",
            6: "layer6_results.json",
            7: "layer7_processed.json",
        }
        deleted_files = []

        if from_substep > 0:
            # Sub-step resume: clear sub-steps >= from_substep
            substeps = meta.get("substeps", {})
            key = str(from_layer)
            all_ss = substeps.get(key, [])
            for ss in all_ss:
                if ss >= from_substep:
                    f = ckpt_dir / f"layer{from_layer}_substep{ss}.json"
                    if f.exists():
                        f.unlink()
                        deleted_files.append(f.name)
            kept_ss = [s for s in all_ss if s < from_substep]
            if kept_ss:
                substeps[key] = kept_ss
            else:
                substeps.pop(key, None)
            meta["substeps"] = substeps
            # Also remove the full layer checkpoint so it re-runs
            fname = _layer_files.get(from_layer)
            if fname:
                f = ckpt_dir / fname
                if f.exists():
                    f.unlink()
                    deleted_files.append(fname)
            reset_layers = [from_layer] if from_layer in old_layers else []
            meta["completed_layers"] = [l for l in old_layers if l != from_layer]
        else:
            # Full layer reset
            reset_layers = [l for l in old_layers if l >= effective_from]
            meta["completed_layers"] = [l for l in old_layers if l < effective_from]
            # Also clear all substeps for reset layers
            substeps = meta.get("substeps", {})
            for l in reset_layers:
                for ss in substeps.pop(str(l), []):
                    f = ckpt_dir / f"layer{l}_substep{ss}.json"
                    if f.exists():
                        f.unlink()
                        deleted_files.append(f.name)
            meta["substeps"] = substeps
            for l in reset_layers:
                fname = _layer_files.get(l)
                if fname:
                    f = ckpt_dir / fname
                    if f.exists():
                        f.unlink()
                        deleted_files.append(fname)

        ckpt_meta.write_text(_j.dumps(meta, indent=2))
        return {
            "ok":            True,
            "from_layer":    from_layer,
            "from_substep":  from_substep,
            "kept_layers":   meta["completed_layers"],
            "deleted_files": deleted_files,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/results")
async def get_results(doc_id: str = ""):
    """Return output file paths and metrics for the most recent completed run of a paper."""
    try:
        import json as _j
        if not doc_id:
            return JSONResponse({"error": "doc_id required"}, status_code=400)

        _safe = doc_id.replace("/", "_").replace(":", "_")
        # Lowercase, underscores — for folder matching
        _norm = _safe.lower().replace(" ", "_").replace(".", "_")
        kg_root = _ROOT / "data" / "kg_output"

        # Run folders matching the id prefix, newest first
        _key = _norm[:18]
        candidates = sorted(
            [d for d in kg_root.iterdir()
             if d.is_dir() and _key in d.name.lower()],
            key=lambda d: d.name,
            reverse=True,
        )
        if not candidates:
            return JSONResponse({"error": "No output found for this paper"}, status_code=404)

        run_dir = candidates[0]

        eval_report = {}
        eval_path = run_dir / "eval_report.json"
        if eval_path.exists():
            try:
                eval_report = _j.loads(eval_path.read_text())
            except Exception:
                pass

        ver_results = []
        ver_json = run_dir / "verification_report.json"
        if ver_json.exists():
            try:
                raw = _j.loads(ver_json.read_text())
                if isinstance(raw, list):
                    ver_results = raw[:35]
            except Exception:
                pass

        n_verified  = sum(1 for r in ver_results if r.get("overall_status") == "CONFIRMED")
        n_total     = len(ver_results)

        graph_html = run_dir / "graph.html"
        ver_html   = run_dir / "verification_report.html"
        pln_html   = (run_dir / "pln_graph.html") if (run_dir / "pln_graph.html").exists() \
                     else (run_dir / "pln" / "pln_graph.html")

        from api.pipeline_utils import _count_run_edges, _count_run_nodes
        neo4j_edges = _count_run_edges(run_dir, "neo4j")
        metta_edges = _count_run_edges(run_dir, "metta")
        neo4j_nodes = _count_run_nodes(run_dir)

        staging_db = _ROOT / "data" / "staging" / f"{_safe}_both.db"
        if not staging_db.exists():
            staging_db = _ROOT / "data" / "staging" / f"{_safe}_neo4j.db"
        if not staging_db.exists():
            staging_db = Path("")

        return {
            "run_dir":        str(run_dir),
            "graph_html":     str(graph_html)  if graph_html.exists()  else "",
            "pln_html":       str(pln_html)    if pln_html.exists()    else "",
            "ver_html":       str(ver_html)    if ver_html.exists()    else "",
            "staging_db":     str(staging_db)  if staging_db.exists()  else "",
            "neo4j_edges":    neo4j_edges,
            "neo4j_nodes":    neo4j_nodes,
            "metta_edges":    metta_edges,
            "metta_nodes":    0,
            "verified":       n_verified,
            "total":          n_total,
            "ver_results":    ver_results,
            "paper_precision": eval_report.get("precision", 0.0),
            "paper_recall":    eval_report.get("recall",    0.0),
            "paper_f1":        eval_report.get("f1",        0.0),
            "paper_tp":        eval_report.get("tp",        0),
            "paper_fp":        eval_report.get("fp",        0),
            "paper_fn":        eval_report.get("fn",        0),
            "auto_insert":     n_verified,
            "human_review":    0,
        }
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/status")
async def status():
    return {"status": "running", "active_runs": len(_connections)}
