"""Layer 7 — concept alignment: merges entities that normalized to different IDs
across sentences but refer to the same real-world concept in this paper.
Uses pairwise LLM judgment with a lexical pre-filter; defaults to "different" when uncertain.
"""
import logging
import re
from collections import defaultdict

from src.llm_client import call_llm, parse_json, MODEL as _MODEL, TEMPERATURE as _TEMPERATURE

logger = logging.getLogger(__name__)

_STOPWORDS = {"a", "an", "the", "of", "in", "and", "or", "to", "for", "is", "are", "with", "at"}
_MAX_PAIRS_PER_PAPER = 20   # safety cap on LLM calls for a pathological paper


def _tokens(text: str) -> set:
    text = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return {t for t in text.split() if t not in _STOPWORDS and len(t) > 1}


def _unresolved(entity_id: str) -> bool:
    return not entity_id or entity_id == "NEEDS_REVIEW"


class _UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _judge_same_concept(anchor_name: str, relation: str, a: dict, b: dict) -> dict:
    """Ask LLM: are `a` and `b` the same real-world concept in this paper, or distinct? Returns same_concept/prefer/reasoning."""
    prompt = (
        f"Two differently-worded entities were both extracted as {relation.upper()} "
        f"the same target, \"{anchor_name}\", from the same paper:\n\n"
        f"ENTITY A: extracted text \"{a['name']}\", resolved by an ontology lookup to "
        f"\"{a['canonical_name'] or a['id']}\" (type={a['type']})\n"
        f"  Evidence sentence: \"{a['evidence'][:300]}\"\n\n"
        f"ENTITY B: extracted text \"{b['name']}\", resolved by an ontology lookup to "
        f"\"{b['canonical_name'] or b['id']}\" (type={b['type']})\n"
        f"  Evidence sentence: \"{b['evidence'][:300]}\"\n\n"
        "Question: is the paper using A and B to make ONE restated point (the "
        "same underlying real-world concept, described with different words in "
        "two sentences), or are they genuinely DIFFERENT concepts that each "
        "happen to relate to the same target (e.g. two distinct risk factors "
        "for the same disease)?\n\n"
        "Default to different unless you are confident they are the same "
        "finding restated — being overly eager to merge is worse than leaving "
        "two separate nodes.\n\n"
        "If they are the same concept, also judge which RESOLVED name (not "
        "just which extracted text) most accurately and specifically describes "
        "what the evidence sentences actually mean. An automated ontology "
        "lookup can occasionally attach an overly-specific or flatly wrong "
        "match to a short/generic extracted word (e.g. matching the bare word "
        "\"sodium\" to an unrelated specific drug compound rather than the "
        "general dietary/element concept) — if one resolved name looks "
        "inconsistent with its own evidence sentence while the other's clearly "
        "matches, prefer the one that actually fits, even if it came from the "
        "less-common surface wording.\n\n"
        "Return ONLY JSON: "
        '{"same_concept": true|false, "prefer": "a"|"b", "reasoning": "<one sentence>"}\n'
        '"prefer" is which entity\'s RESOLVED NAME is the better, more precise, '
        "more accurate canonical form to keep — ignored if same_concept is false."
    )
    try:
        raw = call_llm([{"role": "user", "content": prompt}], model=_MODEL, temperature=_TEMPERATURE)
        data = parse_json(raw)
        return {
            "same_concept": bool(data.get("same_concept", False)),
            "prefer": "b" if str(data.get("prefer", "a")).strip().lower() == "b" else "a",
            "reasoning": data.get("reasoning", ""),
        }
    except Exception as e:
        logger.warning("Concept-alignment judgment failed for %r vs %r: %s", a["name"], b["name"], e)
        return {"same_concept": False, "prefer": "a", "reasoning": ""}


def _resolve_side(records: list, id_key: str, canon_key: str, type_key: str,
                   name_key: str, ev_key: str, review_key: str,
                   url_key: str, syn_key: str, src_key: str,
                   group_id_key: str, anchor_name_key: str,
                   pairs_used: list) -> None:
    """Merge same-concept entities on one side (subject or object) within (relation, group_id) clusters. Mutates `records`."""
    clusters: dict = defaultdict(dict)
    anchor_names: dict = {}

    for r in records:
        eid = r.get(id_key, "")
        if _unresolved(eid):
            continue
        # Normalize relation casing — extraction path uses lowercase, human-review may store uppercase.
        rel = (r.get("relation", "") or "")
        rel = rel.value if hasattr(rel, "value") else rel
        gkey = (str(rel).lower(), r.get(group_id_key, ""))
        if not gkey[1]:
            continue
        entry = clusters[gkey].setdefault(eid, {
            "id": eid, "name": r.get(name_key, "") or "",
            "canonical_name": r.get(canon_key, "") or "",
            "type": r.get(type_key, "") or "",
            "evidence": r.get(ev_key, "") or "",
            "source_url": "", "synonyms": "", "id_source": "",
        })
        # Keep first non-empty value for url/synonyms/id_source across mentions.
        if not entry["source_url"]:
            entry["source_url"] = r.get(url_key, "") or ""
        if not entry["synonyms"]:
            entry["synonyms"] = r.get(syn_key, "") or ""
        if not entry["id_source"]:
            entry["id_source"] = r.get(src_key, "") or ""
        anchor_names.setdefault(gkey, r.get(anchor_name_key, "") or "")

    uf = _UnionFind()
    entities: dict = {}       # id -> representative dict
    prefer_votes: dict = {}   # id -> count of pairwise judgments preferring it as canonical

    for gkey, members in clusters.items():
        if len(members) < 2:
            continue
        entities.update(members)
        ids = list(members.keys())
        relation, _ = gkey
        anchor_name = anchor_names.get(gkey, "")
        capped = False
        for i in range(len(ids)):
            if capped:
                break
            for j in range(i + 1, len(ids)):
                if len(pairs_used) >= _MAX_PAIRS_PER_PAPER:
                    logger.warning("Concept alignment: %d-pair cap reached, remaining pairs deferred.", _MAX_PAIRS_PER_PAPER)
                    capped = True
                    break
                a_id, b_id = ids[i], ids[j]
                a, b = members[a_id], members[b_id]
                tok_a = _tokens(a["name"]) | _tokens(a["canonical_name"])
                tok_b = _tokens(b["name"]) | _tokens(b["canonical_name"])
                if not (tok_a & tok_b):
                    continue   # no lexical overlap — not a plausible merge candidate
                pairs_used.append((a_id, b_id))
                verdict = _judge_same_concept(anchor_name, relation, a, b)
                if verdict["same_concept"]:
                    uf.union(a_id, b_id)
                    preferred_id = a_id if verdict["prefer"] == "a" else b_id
                    prefer_votes[preferred_id] = prefer_votes.get(preferred_id, 0) + 1
                    logger.info("Concept alignment: merging %r + %r (%s)",
                                a["name"], b["name"], verdict["reasoning"])

    # Group ids by their union-find root; only components with >1 member are real merges.
    components: dict = defaultdict(list)
    for eid in entities:
        components[uf.find(eid)].append(eid)

    canonical_for: dict = {}   # losing_id -> winning entity dict
    for members_ids in components.values():
        if len(members_ids) < 2:
            continue
        # Prefer a confidently-resolved (non-needs_review) member as canonical.
        confident_ids = [
            eid for eid in members_ids
            if (lambda r: r is not None and not r.get(review_key))(
                next((r for r in records if r.get(id_key) == eid), None))
        ]
        if len(confident_ids) == 1:
            winner_id = confident_ids[0]
        else:
            # Tie: prefer the id the LLM voted for most; final tie-break is first encountered.
            candidates = confident_ids or members_ids
            winner_id = max(candidates, key=lambda eid: (prefer_votes.get(eid, 0), -members_ids.index(eid)))
        winner = entities[winner_id]
        for eid in members_ids:
            if eid != winner_id:
                canonical_for[eid] = winner

    if not canonical_for:
        return

    for r in records:
        eid = r.get(id_key, "")
        if eid in canonical_for:
            winner = canonical_for[eid]
            r[id_key]    = winner["id"]
            r[canon_key] = winner["canonical_name"]
            r[type_key]  = winner["type"]
            r[url_key] = winner["source_url"]
            r[syn_key] = winner["synonyms"]
            r[src_key] = winner["id_source"]
            # name_key/ev_key kept as-is — node_naming.py aggregates all mentions' names/evidence later.


def align_concepts(records: list) -> list:
    """Merge same-concept entities across sentences for one paper's records."""
    if not records:
        return records

    pairs_used: list = []

    _resolve_side(
        records, "subject_id", "subject_canonical_name", "subject_type",
        "subject_name", "subject_evidence", "subject_needs_review",
        "subject_source_url", "subject_synonyms", "subject_id_source",
        group_id_key="object_id", anchor_name_key="object_name",
        pairs_used=pairs_used,
    )
    _resolve_side(
        records, "object_id", "object_canonical_name", "object_type",
        "object_name", "object_evidence", "object_needs_review",
        "object_source_url", "object_synonyms", "object_id_source",
        group_id_key="subject_id", anchor_name_key="subject_name",
        pairs_used=pairs_used,
    )

    return records
