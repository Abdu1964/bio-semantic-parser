"""Layer 8 Output A — writes validated relations to Neo4j-importable CSV + Cypher files."""
import csv
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path

from .node_naming import (
    pick_node_name, pick_node_full_name, pick_node_source_url,
    merge_node_synonyms, pick_node_entity_type, merge_node_evidence,
)

_OUT_DIR   = Path(os.getenv("NEO4J_OUTPUT_DIR", "data/output/neo4j"))
_DELIMITER = "|"


def _slug(text: str) -> str:
    """Convert to lowercase underscore slug for use in filenames."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "other").lower()).strip("_")


def write(records: list, run_dir: Path = None) -> dict:
    """Write all records to Neo4j CSV and Cypher files under run_dir/neo4j/."""
    out_dir = (run_dir / "neo4j") if run_dir else _OUT_DIR
    # Rebuilt from scratch every call — type changes between re-exports would leave orphaned files otherwise.
    if out_dir.exists():
        resolved = out_dir.resolve()
        if resolved == Path("/") or resolved == Path.home() or resolved == Path.cwd():
            raise ValueError(f"Refusing to delete unsafe output dir: {resolved}")
        shutil.rmtree(resolved)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Keyed by canonical ID only — entity_type is a majority-voted property, not a partition key.
    node_mentions: dict = defaultdict(lambda: {
        "names": [], "canonical_names": [], "source_urls": [], "synonyms": [],
        "entity_types": [], "id_sources": [], "needs_reviews": [], "evidence": [],
    })

    for r in records:
        for name_key, canon_key, type_key, id_key, src_key, review_key, url_key, syn_key, ev_key in [
            ("subject_name", "subject_canonical_name", "subject_type", "subject_id",
             "subject_id_source", "subject_needs_review", "subject_source_url", "subject_synonyms", "subject_evidence"),
            ("object_name",  "object_canonical_name",  "object_type",  "object_id",
             "object_id_source", "object_needs_review", "object_source_url", "object_synonyms", "object_evidence"),
        ]:
            cid = r.get(id_key, "") or ""
            if not cid:
                continue
            entry = node_mentions[cid]
            name  = r.get(name_key, "") or ""
            canon = r.get(canon_key, "") or ""
            url   = r.get(url_key, "") or ""
            syn   = r.get(syn_key, "") or ""
            ev    = r.get(ev_key, "") or ""
            if name:
                entry["names"].append(name)
            if canon:
                entry["canonical_names"].append(canon)
            if url:
                entry["source_urls"].append(url)
            if syn:
                entry["synonyms"].append(syn)
            if ev:
                entry["evidence"].append(ev)
            entry["entity_types"].append(r.get(type_key, "OTHER") or "OTHER")
            entry["id_sources"].append(r.get(src_key, "") or "")
            entry["needs_reviews"].append(bool(r.get(review_key, False)))

    id_to_row: dict = {}
    id_to_type_slug: dict = {}
    for cid, entry in node_mentions.items():
        etype = pick_node_entity_type(entry["entity_types"])
        id_to_type_slug[cid] = _slug(etype)
        is_uncertain = any(entry["needs_reviews"])
        id_to_row[cid] = {
            "id":           cid,
            "name":         pick_node_name(entry["names"], entry["canonical_names"], is_uncertain),
            "full_name":    pick_node_full_name(entry["names"], entry["canonical_names"], is_uncertain),
            "entity_type":  etype,
            "id_source":    entry["id_sources"][0]    if entry["id_sources"]    else "",
            "needs_review": "true" if is_uncertain else "false",
            "source_url":   pick_node_source_url(entry["source_urls"]),
            "synonyms":     merge_node_synonyms(entry["synonyms"]),
            "evidence":     merge_node_evidence(entry["evidence"]),
        }

    nodes: dict = defaultdict(dict)
    for cid, row in id_to_row.items():
        nodes[id_to_type_slug[cid]][cid] = row

    # Group edges by (source_type, relation, target_type); use WINNING type slug, not the triple's own tag.
    edge_groups: dict = defaultdict(lambda: defaultdict(list))
    for r in records:
        s_id   = r.get("subject_id", "") or ""
        o_id   = r.get("object_id",  "") or ""
        s_slug = id_to_type_slug.get(s_id) or _slug(r.get("subject_type", "OTHER") or "OTHER")
        o_slug = id_to_type_slug.get(o_id) or _slug(r.get("object_type",  "OTHER") or "OTHER")
        rel    = _slug(r.get("relation", "related_to") or "related_to")
        edge_groups[s_slug][(rel, o_slug)].append(r)

    # ── Write one folder per entity type ─────────────────────────────────────
    # neo4j/{entity_type}/
    #   nodes_{entity_type}.csv       ← all nodes of this type
    #   edges_{relation}.csv          ← edges where THIS type is the source
    node_fields = ["id", "name", "full_name", "entity_type", "id_source", "needs_review", "source_url", "synonyms", "evidence"]
    edge_fields = [
        "source_id", "source_name", "source_type",
        "target_id", "target_name", "target_type",
        "relation",
        "confidence", "negated",
        "species", "tissue", "condition", "effect_size",
        "source_paper", "section",
        "paper_source", "paper_url",
        "validation_verdict", "alignment_action",
        "reasoning", "confidence_channels", "review_reason",
    ]

    node_files: list = []
    edge_files: list = []
    all_slugs = sorted(set(nodes.keys()) | set(edge_groups.keys()))

    for slug in all_slugs:
        type_dir = out_dir / slug
        type_dir.mkdir(parents=True, exist_ok=True)

        # Node file
        if slug in nodes:
            csv_path    = type_dir / f"nodes_{slug}.csv"
            cypher_path = type_dir / f"nodes_{slug}.cypher"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=node_fields,
                                   delimiter=_DELIMITER, extrasaction="ignore")
                w.writeheader()
                for row in nodes[slug].values():
                    w.writerow(row)
            _write_node_cypher(slug, csv_path, cypher_path, out_dir)
            node_files.append(str(csv_path))

        for (rel, o_slug), rows in sorted(edge_groups.get(slug, {}).items()):
            fname       = f"edges_{slug}_{rel}_{o_slug}"
            csv_path    = type_dir / f"{fname}.csv"
            cypher_path = type_dir / f"{fname}.cypher"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=edge_fields,
                                   delimiter=_DELIMITER, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    s_id = r.get("subject_id", "") or ""
                    o_id = r.get("object_id",  "") or ""
                    if not s_id or not o_id or \
                       s_id == "NEEDS_REVIEW" or o_id == "NEEDS_REVIEW":
                        continue
                    w.writerow({
                        "source_id":           s_id,
                        "source_name":         r.get("subject_name", ""),
                        "source_type":         id_to_row.get(s_id, {}).get("entity_type") or r.get("subject_type", ""),
                        "target_id":           o_id,
                        "target_name":         r.get("object_name",  ""),
                        "target_type":         id_to_row.get(o_id, {}).get("entity_type") or r.get("object_type",  ""),
                        "relation":            r.get("relation",     "") or rel,
                        "confidence":          r.get("confidence",   0.0),
                        "negated":             str(r.get("negated", False)).lower(),
                        "species":             r.get("species",      ""),
                        "tissue":              r.get("tissue",       ""),
                        "condition":           r.get("condition",    ""),
                        "effect_size":         r.get("effect_size",  ""),
                        "source_paper":        (r.get("document_id", "") or
                                               (json.loads(r.get("source_papers", "[]") or "[]") or [""])[0]),
                        "section":             r.get("section",      ""),
                        "paper_source":        r.get("source_name",  ""),
                        "paper_url":           r.get("source_url",   ""),
                        "validation_verdict":  r.get("validation_verdict", ""),
                        "alignment_action":    r.get("alignment_action",   ""),
                        "reasoning":           (r.get("reasoning", "") or "")[:300],
                        "confidence_channels": json.dumps(r.get("confidence_channels") or {}),
                        "review_reason":       (r.get("review_reason", "") or "")[:200],
                    })
            _write_edge_cypher(rel.upper(), slug, o_slug,
                               csv_path, cypher_path, out_dir)
            edge_files.append(str(csv_path))

    return {
        "output_dir":  str(out_dir),
        "node_files":  node_files,
        "edge_files":  edge_files,
        "node_types":  list(nodes.keys()),
        "edge_types":  sorted({rel for eg in edge_groups.values() for rel, _ in eg}),
        "node_count":  sum(len(v) for v in nodes.values()),
        "edge_count":  sum(
            1
            for eg in edge_groups.values()
            for rows in eg.values()
            for r in rows
            if (r.get("subject_id") and r.get("object_id")
                and r.get("subject_id") != "NEEDS_REVIEW"
                and r.get("object_id") != "NEEDS_REVIEW")
        ),
    }


def _write_node_cypher(label: str, csv_path: Path, cypher_path: Path, out_dir: Path) -> None:
    relative = csv_path.relative_to(out_dir).as_posix()
    query = f"""// nodes_{label}.cypher — generated by bio-semantic-parser Layer 8
CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE;

CALL apoc.periodic.iterate(
    "LOAD CSV WITH HEADERS FROM 'file:///{relative}' AS row FIELDTERMINATOR '{_DELIMITER}' RETURN row",
    "MERGE (n:{label} {{id: row.id}})
     SET n.name         = row.name,
         n.full_name    = row.full_name,
         n.entity_type  = row.entity_type,
         n.id_source    = row.id_source,
         n.needs_review = row.needs_review,
         n.source_url   = row.source_url,
         n.synonyms     = row.synonyms,
         n.evidence     = row.evidence",
    {{batchSize: 1000, parallel: true}}
)
YIELD batches, total
RETURN batches, total;
"""
    cypher_path.write_text(query, encoding="utf-8")


def _write_edge_cypher(
    relation: str, source_type: str, target_type: str,
    csv_path: Path, cypher_path: Path, out_dir: Path,
) -> None:
    relative = csv_path.relative_to(out_dir).as_posix()
    query = f"""// {source_type}/edges_{source_type}_{relation.lower()}_{target_type}.cypher

CALL apoc.periodic.iterate(
    "LOAD CSV WITH HEADERS FROM 'file:///{relative}' AS row FIELDTERMINATOR '{_DELIMITER}' RETURN row",
    "MATCH (source:{source_type} {{id: row.source_id}})
     MATCH (target:{target_type} {{id: row.target_id}})
     CREATE (source)-[r:{relation}]->(target)
     SET r.confidence         = toFloat(row.confidence),
         r.negated            = row.negated,
         r.species            = row.species,
         r.tissue             = row.tissue,
         r.condition          = row.condition,
         r.effect_size        = row.effect_size,
         r.source_paper       = row.source_paper,
         r.section            = row.section,
         r.paper_source       = row.paper_source,
         r.paper_url          = row.paper_url,
         r.validation_verdict = row.validation_verdict,
         r.reasoning          = row.reasoning",
    {{batchSize: 1000}}
)
YIELD batches, total
RETURN batches, total;
"""
    cypher_path.write_text(query, encoding="utf-8")
