"""Layer 7 — align this paper's concepts with existing KG nodes; mark which nodes already exist."""
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.postextraction import concept_alignment as ca
from src.postextraction import entity_normalization as en
from src.postextraction.name_matching import EXACT, match_quality

logger = logging.getLogger(__name__)

_DB_PATH = Path("data/triple_store.db")


def _kg_paths() -> list:
    """The permanent KG stores (not this paper's staging DB)."""
    from src.postextraction.deduplication import db_path_for_format
    return [p for p in dict.fromkeys([db_path_for_format("neo4j"), db_path_for_format("metta"), _DB_PATH])
            if Path(p).exists()]


def _kg_concepts() -> dict:
    """id → concept dict for every resolved KG node."""
    concepts: dict = {}
    for path in _kg_paths():
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute("PRAGMA table_info(triples)")}
            for side in ("subject", "object"):
                fields = [f"{side}_{f}" for f in ("id", "name", "type", "canonical_name", "synonyms",
                                                  "source_url", "id_source", "evidence") if f"{side}_{f}" in cols]
                for row in conn.execute(f"SELECT {', '.join(fields)} FROM triples"):
                    r = dict(row)
                    cid = r.get(f"{side}_id", "")
                    if not ca._resolved(cid):
                        continue
                    prefix = en._ontology_prefix_of(cid)
                    c = concepts.setdefault(cid, {
                        "id": cid, "key": ca._key(cid), "prefix": prefix, "rank": en._ontology_rank(prefix),
                        "fam": en._PREFIX_FAMILY.get(prefix) or en._TYPE_FAMILY.get(r.get(f"{side}_type"), "concept"),
                        "label": r.get(f"{side}_canonical_name") or r.get(f"{side}_name") or "",
                        "names": set(), "evidence": [], "mentions": 0, "origin": "kg",
                        "rec": {"id": cid, "canonical_name": r.get(f"{side}_canonical_name") or "",
                                "id_source": r.get(f"{side}_id_source") or "", "source_url": r.get(f"{side}_source_url") or "",
                                "synonyms": r.get(f"{side}_synonyms") or "", "type": r.get(f"{side}_type") or ""},
                    })
                    c["mentions"] += 1
                    if r.get(f"{side}_name"):
                        c["names"].add(r[f"{side}_name"])
                    ev = r.get(f"{side}_evidence") or ""
                    if ev and len(c["evidence"]) < 2 and ev not in c["evidence"]:
                        c["evidence"].append(ev)
            conn.close()
        except Exception as e:
            logger.warning("Could not read KG concepts from %s: %s", path, e)
    for c in concepts.values():
        c["names"] = {n for n in c["names"] | {c["label"]} if n.strip()}
    return concepts


def _synonyms(c: dict) -> list:
    syns = c["rec"].get("synonyms") or ""
    return [x for x in (syns.split("; ") if isinstance(syns, str) else syns) if x]


def _synonym_says_same(a: dict, b: dict) -> bool:
    """A database lists one concept's name as an exact synonym of the other (reorderings aren't enough)."""
    return bool(a["label"] and b["label"]) and (
        match_quality(a["label"], b["label"], _synonyms(b)) == EXACT
        or match_quality(b["label"], a["label"], _synonyms(a)) == EXACT)


def align_with_kg(records: list) -> list:
    """Reuse existing KG nodes for this paper's concepts that mean the same thing."""
    kg = _kg_concepts()
    paper = ca._collect(records)
    new = [cid for cid in paper if cid not in kg]
    if not kg or not new:
        return records
    for cid in new:
        paper[cid]["origin"] = "paper"
    concepts = {**kg, **{cid: paper[cid] for cid in new}}

    def check(a, b):
        return not ca._blocked(concepts[a], concepts[b])

    matches: dict = {}                    # paper id → (kg id, method, score, reason)
    by_fam: dict = {}
    for cid in kg:
        by_fam.setdefault(concepts[cid]["fam"], []).append(cid)
    for a in new:
        for b in by_fam.get(concepts[a]["fam"], []):
            if check(a, b) and _synonym_says_same(concepts[a], concepts[b]):
                matches.setdefault(a, (b, "synonym", 1.0, "database synonym"))

    todo = [a for a in new if a not in matches]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cid, (xrefs, definition) in zip(todo, ex.map(ca._lookup, todo)):
            concepts[cid]["xrefs"], concepts[cid]["definition"] = xrefs, definition
    cands = [(s, a, b) for s, a, b in ca._semantic_candidates(concepts, left=todo, right=list(kg)) if check(a, b)]
    kg_needed = {b for _, _, b in cands}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cid, (xrefs, definition) in zip(kg_needed, ex.map(ca._lookup, kg_needed)):
            concepts[cid]["xrefs"], concepts[cid]["definition"] = xrefs, definition
    for s, a, b in cands:
        if a not in matches and ca._xref_says_same(concepts[a], concepts[b]):
            matches[a] = (b, "cross_reference", 1.0, "ontology cross-reference")
    pending = [(s, a, b) for s, a, b in cands if a not in matches][:ca._MAX_PAIRS]
    with ThreadPoolExecutor(max_workers=ca._WORKERS) as ex:
        verdicts = list(ex.map(lambda p: ca._verify(concepts[p[1]], concepts[p[2]]), pending))
    for (s, a, b), v in zip(pending, verdicts):
        if v["relation"] == "SAME" and a not in matches:
            matches[a] = (b, "semantic", round(s, 3), v["reason"])

    if not matches:
        return records
    for r in records:
        for side in ("subject", "object"):
            a = r.get(f"{side}_id", "")
            if a not in matches:
                continue
            b, method, score, reason = matches[a]
            link = {"from": a, "from_name": concepts[a]["label"], "to": b, "to_name": concepts[b]["label"],
                    "method": method, "score": score, "reason": reason, "scope": "kg"}
            if concepts[b]["rank"] <= concepts[a]["rank"]:     # reuse the existing node
                for f, v in concepts[b]["rec"].items():
                    if v:
                        r[f"{side}_{f}"] = v
                r[f"{side}_synonyms"] = ca._merged_synonyms(concepts[b]["rec"].get("synonyms"),
                                                            concepts[a]["rec"].get("synonyms"), sorted(concepts[a]["names"]))
                r[f"{side}_aliases"] = sorted({*r.get(f"{side}_aliases", []), a})
                r[f"{side}_alignment"] = link
            else:                                               # paper's ID is better: keep it
                r[f"{side}_kg_identity"] = {**link, "kept": a}
    logger.info("KG alignment: %d concept(s) matched existing nodes: %s",
                len(matches), {a: m[0] for a, m in matches.items()})
    return records


def _known_entities() -> set:
    """All canonical IDs in the permanent KG."""
    known: set = set()
    for db in _kg_paths():
        try:
            conn = sqlite3.connect(str(db))
            rows = conn.execute("SELECT DISTINCT subject_id FROM triples UNION SELECT DISTINCT object_id FROM triples")
            known |= {row[0] for row in rows if row[0] and row[0] != "NEEDS_REVIEW"}
            conn.close()
        except Exception as e:
            logger.warning("Could not read KG entities from %s: %s", db, e)
    return known


def align(records: list) -> list:
    """Mark whether each record's subject/object already exists as a KG node."""
    known = _known_entities()
    aligned = []
    for r in records:
        s_exists = r.get("subject_id", "") in known
        o_exists = r.get("object_id", "") in known
        action = "connect_existing" if s_exists and o_exists else ("create_one_new" if s_exists or o_exists
                                                                   else "both_new")
        aligned.append({**r, "subject_node_exists": s_exists, "object_node_exists": o_exists,
                        "alignment_action": action})
    return aligned
