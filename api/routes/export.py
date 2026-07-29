"""Routes: Export data from query results and unified KG.

POST /api/export/subgraph  — export subgraph query results
POST /api/export/unified   — export full unified KG
"""
import csv
import io
import json
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

router = APIRouter(prefix="/api/export", tags=["export"])

    "metta":  _ROOT / "data" / "triple_store_metta.db",
}


def _get_conn(db: str):
    path = _DB_PATHS.get(db)
    if not path or not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _zip_dir(dir_path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(dir_path.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(dir_path))
    buf.seek(0)
    return buf.read()


def _zip_metta(rows: list) -> bytes:
    """Generate a single .metta file with simple triples."""
    def _safe_id(cid: str) -> str:
        import re
        return re.sub(r"[^A-Za-z0-9_]", "_", str(cid)).strip("_") or "unknown"

    def _safe_name(v: str) -> str:
        import re
        v = str(v).strip() if v else ""
        v = re.sub(r"[^A-Za-z0-9_ ]", "_", v).strip()
        v = re.sub(r"\s+", "_", v).strip("_")
        return v or "unknown"

    lines = []
    for r in rows:
        s_name = _safe_name(r.get("subject_name", "") or r.get("subject_id", ""))
        o_name = _safe_name(r.get("object_name", "") or r.get("object_id", ""))
        rel = (r.get("relation") or "related_to").lower().replace(" ", "_")
        lines.append(f"({s_name} {rel} {o_name})")

    content = "\n".join(lines) + "\n"
    return content.encode("utf-8")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/subgraph")
async def export_subgraph(body: dict):
    """Export subgraph query results in the requested format."""
    db = body.get("db", "metta")
    entity1 = body.get("entity1", "")
    entity2 = body.get("entity2", "")
    relation = body.get("relation", "")
    entity_extra = body.get("entity_extra", [])
    limit = body.get("limit", 150)
    fmt = body.get("format", "json")

    conn = _get_conn(db)
    if not conn:
        return JSONResponse({"error": "Database not found"}, status_code=404)

    try:
        conditions, params = [], []

        if entity1:
            conditions.append(
                "(subject_name=? OR subject_id=? OR object_name=? OR object_id=?)"
            )
            params += [entity1] * 4
        if entity2:
            conditions.append(
                "(subject_name=? OR subject_id=? OR object_name=? OR object_id=?)"
            )
            params += [entity2] * 4
        for extra in entity_extra:
            if extra:
                conditions.append(
                    "(subject_name=? OR subject_id=? OR object_name=? OR object_id=?)"
                )
                params += [extra] * 4
        if relation:
            conditions.append("relation=?")
            params.append(relation)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        rows = conn.execute(
            f"SELECT * FROM triples {where} ORDER BY confidence DESC LIMIT ?",
            params,
        ).fetchall()
        rows = [dict(r) for r in rows]
    finally:
        conn.close()

    if not rows:
        return JSONResponse({"error": "No results found"}, status_code=404)

    # Build filename parts
    name_parts = ["subgraph"]
    if entity1: name_parts.append(entity1.replace(" ", "_"))
    if relation: name_parts.append(relation)
    if entity2: name_parts.append(entity2.replace(" ", "_"))
    base_name = "_".join(name_parts)[:60]

    if fmt == "json":
        # Build vis.js-compatible nodes/edges like /api/query/subgraph does
        nodes: dict = {}
        edges: list = []
        for i, r in enumerate(rows):
            for nid, nname in [
                (r["subject_id"], r["subject_name"]),
                (r["object_id"], r["object_name"]),
            ]:
                if nid not in nodes:
                    nodes[nid] = {"id": nid, "label": nname or nid}
            try:
                src_papers = json.loads(r.get("source_papers") or "[]")
            except Exception:
                src_papers = []
            edges.append({
                "id": f"e{i}",
                "from": r["subject_id"],
                "to": r["object_id"],
                "label": (r["relation"] or "").replace("_", " "),
                "relation": r["relation"],
                "confidence": r.get("confidence", 0),
                "source_papers": src_papers,
                "reasoning": r.get("reasoning", ""),
                "from_name": r.get("subject_name", ""),
                "to_name": r.get("object_name", ""),
                "from_type": r.get("subject_type", ""),
                "to_type": r.get("object_type", ""),
                "negated": bool(r.get("negated")),
                "species": r.get("species", ""),
                "tissue": r.get("tissue", ""),
                "condition": r.get("condition", ""),
                "effect_size": r.get("effect_size", ""),
            })

        payload = json.dumps({"nodes": list(nodes.values()), "edges": edges}, indent=2)
        return StreamingResponse(
            io.BytesIO(payload.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.json"'},
        )


    elif fmt == "metta":
        data = _zip_metta(rows)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.metta"'},
        )

    return JSONResponse({"error": f"Unknown format: {fmt}"}, status_code=400)


@router.post("/unified")
async def export_unified(body: dict):
    """Export the full unified KG from the triple store."""
    db = body.get("db", "metta")
    fmt = body.get("format", "json")

    conn = _get_conn(db)
    if not conn:
        return JSONResponse({"error": "Database not found"}, status_code=404)

    try:
        rows = conn.execute(
            "SELECT * FROM triples WHERE confidence >= 0 AND is_contradiction = 0 "
            "ORDER BY confidence DESC"
        ).fetchall()
        rows = [dict(r) for r in rows]
    finally:
        conn.close()

    if not rows:
        return JSONResponse({"error": "No triples found in the knowledge graph"}, status_code=404)

    base_name = f"unified_kg_{db}"

    if fmt == "json":
        clean = []
        for r in rows:
            clean.append({k: v for k, v in r.items() if k not in ("count", "db")})
        payload = json.dumps({"triples": clean}, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(payload.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.json"'},
        )


    elif fmt == "metta":
        data = _zip_metta(rows)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.metta"'},
        )

    return JSONResponse({"error": f"Unknown format: {fmt}"}, status_code=400)
