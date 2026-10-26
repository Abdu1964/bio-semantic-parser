"""Endpoints for listing, editing, and deleting triples in staging or unified KG DBs."""
import sys
import threading as _thr
from pathlib import Path
from fastapi import APIRouter, Form, Query
from fastapi.responses import JSONResponse

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

router = APIRouter()


def _resolve(db_path: str) -> Path:
    p = Path(db_path)
    return p if p.is_absolute() else _ROOT / p


@router.get("/api/kg/relations")
async def list_relations():
    """Return all valid RelationType values for the relation dropdown."""
    try:
        from src.schema.taxonomy import RelationType
        return JSONResponse({"relations": sorted(r.value for r in RelationType)})
    except Exception as e:
        return JSONResponse({"relations": [], "error": str(e)})


@router.get("/api/kg/triples")
async def list_triples(
    db_path: str = "",
    q:       str = "",
    limit:   int = 20,
    offset:  int = 0,
):
    """Paginated triple list from any staging or unified KG DB."""
    if not db_path:
        return JSONResponse({"triples": [], "total": 0, "error": "db_path required"})
    try:
        import sqlite3 as _sq
        p = _resolve(db_path)
        if not p.exists():
            return JSONResponse({"triples": [], "total": 0, "error": f"DB not found: {db_path}"})

        conn = _sq.connect(str(p), timeout=10)
        conn.row_factory = _sq.Row

        where  = "WHERE flagged_for_review = 0"
        params = []
        if q:
            where += " AND (subject_name LIKE ? OR object_name LIKE ? OR relation LIKE ?)"
            pq = f"%{q}%"
            params.extend([pq, pq, pq])

        total = conn.execute(f"SELECT COUNT(*) FROM triples {where}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT id, subject_name, subject_id, subject_type, relation, "
            f"object_name, object_id, object_type, negated, confidence, section "
            f"FROM triples {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        conn.close()

        return JSONResponse({"triples": [dict(r) for r in rows], "total": total})
    except Exception as e:
        return JSONResponse({"triples": [], "total": 0, "error": str(e)})


@router.patch("/api/kg/triples/{triple_id}")
async def update_triple(
    triple_id:    int,
    db_path:      str = Form(...),
    run_dir:      str = Form(""),
    subject_name: str = Form(""),
    subject_id:   str = Form(""),
    relation:     str = Form(""),
    object_name:  str = Form(""),
    object_id:    str = Form(""),
):
    """Update a triple and trigger a background graph + verification rebuild."""
    if not db_path or not relation:
        return JSONResponse({"ok": False, "error": "db_path and relation required"})
    try:
        import sqlite3 as _sq
        from datetime import datetime, timezone
        p = _resolve(db_path)
        if not p.exists():
            return JSONResponse({"ok": False, "error": "DB not found"})

        now = datetime.now(timezone.utc).isoformat()
        conn = _sq.connect(str(p), timeout=10)
        conn.execute(
            "UPDATE triples SET subject_name=?, subject_id=?, relation=?, "
            "object_name=?, object_id=?, updated_at=? WHERE id=?",
            (subject_name, subject_id, relation, object_name, object_id, now, triple_id),
        )
        conn.commit()
        conn.close()

        _thr.Thread(target=_rebuild, args=(db_path, run_dir), daemon=True).start()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.delete("/api/kg/triples/{triple_id}")
async def delete_triple(
    triple_id: int,
    db_path:   str = Query(...),
    run_dir:   str = Query(""),
):
    """Delete a triple and trigger a background rebuild."""
    if not db_path:
        return JSONResponse({"ok": False, "error": "db_path required"})
    try:
        import sqlite3 as _sq
        p = _resolve(db_path)
        if not p.exists():
            return JSONResponse({"ok": False, "error": "DB not found"})

        conn = _sq.connect(str(p), timeout=10)
        conn.execute("DELETE FROM triples WHERE id=?", (triple_id,))
        conn.commit()
        conn.close()

        _thr.Thread(target=_rebuild, args=(db_path, run_dir), daemon=True).start()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def _rebuild(db_path: str, run_dir: str) -> None:
    """Re-export CSVs → rebuild graph → re-verify. Same logic as human_review rebuild."""
    sdb = _resolve(db_path)
    try:
        if run_dir:
            _run = Path(run_dir)
            if not _run.is_absolute():
                _run = _ROOT / run_dir

            from src.publish.output_layer import export_csvs_from_db
            from tools.visualize_graph import build_graph_data, render_html
            from tools.verify_kg import run_verification

            try: export_csvs_from_db(str(sdb), str(_run), formats="both")
            except Exception: pass

            _csv = _run / "neo4j"
            if _csv.exists():
                try:
                    _n, _e = build_graph_data([_csv])
                    render_html(_n, _e, _run / "graph.html", title="Paper Knowledge Graph")
                except Exception: pass

            for fmt, out_html, out_json in [
                ("neo4j", _run / "verification_report.html",       _run / "verification_report.json"),
                ("metta", _run / "verification_report_metta.html", _run / "verification_report_metta.json"),
            ]:
                try:
                    run_verification(
                        fetch_text=True, formats=fmt,
                        db_path=str(sdb) if sdb.exists() else None,
                        out_html=out_html, out_json=out_json,
                    )
                except Exception: pass
        else:
            # Unified KG rebuild
            import os as _os
            old = _os.getcwd()
            try:
                _os.chdir(str(_ROOT))
                from tools.export_unified import run_export
                run_export(generate_html=True, formats="neo4j")
            except Exception: pass
            finally:
                _os.chdir(old)
    except Exception: pass
