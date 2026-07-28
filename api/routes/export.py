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

_DB_PATHS = {
    "neo4j": _ROOT / "data" / "triple_store_neo4j.db",
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


def _zip_neo4j(rows: list, db: str) -> bytes:
    """Generate Neo4j-compatible CSVs + Cypher from rows and zip them."""
    DELIMITER = "|"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # --- Nodes ---
        node_mentions: dict = {}
        for r in rows:
            for prefix in ("subject", "object"):
                nid = r[f"{prefix}_id"] or ""
                if not nid or nid in node_mentions:
                    continue
                entry = node_mentions.setdefault(nid, {
                    "names": set(), "types": set(),
                    "canonical_names": set(), "source_urls": set(), "synonyms": set(),
                })
                name = r.get(f"{prefix}_name", "") or ""
                canon = r.get(f"{prefix}_canonical_name", "") or ""
                url = r.get(f"{prefix}_source_url", "") or ""
                syn = r.get(f"{prefix}_synonyms", "") or ""
                etype = r.get(f"{prefix}_type", "") or "OTHER"
                if name: entry["names"].add(name)
                if canon: entry["canonical_names"].add(canon)
                if url: entry["source_urls"].add(url)
                if syn: entry["synonyms"].add(syn)
                entry["types"].add(etype)

        node_fields = ["id", "name", "full_name", "entity_type", "needs_review", "source_url", "synonyms"]
        nodes_by_type: dict = {}
        for nid, entry in node_mentions.items():
            types = list(entry["types"])
            etype = max(set(types), key=types.count) if types else "OTHER"
            slug = etype.lower().replace(" ", "_")
            nodes_by_type.setdefault(slug, []).append({
                "id": nid,
                "name": next(iter(entry["names"]), nid),
                "full_name": next(iter(entry["canonical_names"]), ""),
                "entity_type": etype,
                "needs_review": "false",
                "source_url": next(iter(entry["source_urls"]), ""),
                "synonyms": "|".join(entry["synonyms"]),
            })

        node_dir = tmp_path / "nodes"
        node_dir.mkdir()
        for slug, nodes in nodes_by_type.items():
            csv_path = node_dir / f"nodes_{slug}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=node_fields, delimiter=DELIMITER, extrasaction="ignore")
                w.writeheader()
                w.writerows(nodes)

        # --- Edges ---
        edge_fields = [
            "source_id", "source_name", "source_type",
            "target_id", "target_name", "target_type",
            "relation", "confidence", "negated",
            "species", "tissue", "condition", "effect_size",
            "source_papers", "reasoning", "is_contradiction",
        ]
        edges_by_type: dict = {}
        for r in rows:
            s_type = (r.get("subject_type") or "other").lower().replace(" ", "_")
            o_type = (r.get("object_type") or "other").lower().replace(" ", "_")
            rel = (r.get("relation") or "related_to").lower().replace(" ", "_")
            key = (s_type, rel, o_type)
            src_papers = r.get("source_papers") or "[]"
            try:
                src_papers = json.dumps(json.loads(src_papers) if isinstance(src_papers, str) else src_papers)
            except Exception:
                src_papers = "[]"
            edges_by_type.setdefault(key, []).append({
                "source_id": r.get("subject_id", ""),
                "source_name": r.get("subject_name", ""),
                "source_type": r.get("subject_type", ""),
                "target_id": r.get("object_id", ""),
                "target_name": r.get("object_name", ""),
                "target_type": r.get("object_type", ""),
                "relation": r.get("relation", ""),
                "confidence": r.get("confidence", 0),
                "negated": str(bool(r.get("negated", 0))).lower(),
                "species": r.get("species", "") or "",
                "tissue": r.get("tissue", "") or "",
                "condition": r.get("condition", "") or "",
                "effect_size": r.get("effect_size", "") or "",
                "source_papers": src_papers,
                "reasoning": (r.get("reasoning", "") or "")[:300],
                "is_contradiction": str(bool(r.get("is_contradiction", 0))).lower(),
            })

        edge_dir = tmp_path / "edges"
        edge_dir.mkdir()
        for (s_slug, rel, o_slug), edges in edges_by_type.items():
            fname = f"edges_{s_slug}_{rel}_{o_slug}"
            csv_path = edge_dir / f"{fname}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=edge_fields, delimiter=DELIMITER, extrasaction="ignore")
                w.writeheader()
                w.writerows(edges)

        # --- Cypher scripts ---
        cypher_dir = tmp_path / "cypher"
        cypher_dir.mkdir()
        for slug in nodes_by_type:
            rel_path = f"../nodes/nodes_{slug}.csv"
            cypher = (
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{slug}) REQUIRE n.id IS UNIQUE;\n\n"
                f"LOAD CSV WITH HEADERS FROM 'file:///{rel_path}' AS row FIELDTERMINATOR '{DELIMITER}'\n"
                f"MERGE (n:{slug} {{id: row.id}})\n"
                f"SET n.name = row.name, n.full_name = row.full_name, n.entity_type = row.entity_type;\n"
            )
            (cypher_dir / f"nodes_{slug}.cypher").write_text(cypher, encoding="utf-8")

        for (s_slug, rel, o_slug) in edges_by_type:
            suffix = f"{s_slug}_{rel}_{o_slug}"
            rel_path = f"../edges/edges_{suffix}.csv"
            rel_upper = rel.upper()
            cypher = (
                f"LOAD CSV WITH HEADERS FROM 'file:///{rel_path}' AS row FIELDTERMINATOR '{DELIMITER}'\n"
                f"MATCH (s:{s_slug} {{id: row.source_id}})\n"
                f"MATCH (t:{o_slug} {{id: row.target_id}})\n"
                f"MERGE (s)-[r:{rel_upper}]->(t)\n"
                f"SET r.confidence = toFloat(row.confidence), r.negated = row.negated, r.species = row.species;\n"
            )
            (cypher_dir / f"edges_{suffix}.cypher").write_text(cypher, encoding="utf-8")

        return _zip_dir(tmp_path)


def _zip_metta(rows: list) -> bytes:
    """Generate a single .metta file with simple triples."""
    def _safe_id(cid: str) -> str:
        import re
        return re.sub(r"[^A-Za-z0-9_]", "_", str(cid)).strip("_") or "unknown"

    def _safe_name(v: str) -> str:
        import re
        v = str(v).strip() if v else ""
        return re.sub(r"[^A-Za-z0-9_ ]", "_", v).strip() or "unknown"

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
    db = body.get("db", "neo4j")
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

    elif fmt == "neo4j":
        data = _zip_neo4j(rows, db)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{base_name}_neo4j.zip"'},
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
    db = body.get("db", "neo4j")
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

    elif fmt == "neo4j":
        data = _zip_neo4j(rows, db)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{base_name}_neo4j.zip"'},
        )

    elif fmt == "metta":
        data = _zip_metta(rows)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.metta"'},
        )

    return JSONResponse({"error": f"Unknown format: {fmt}"}, status_code=400)
