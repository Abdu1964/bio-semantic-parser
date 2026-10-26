"""Layer 7 — fact alignment: one edge per claim (relation rules + LLM same-claim check)."""
import logging
import os
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from src.llm_client import call_llm, parse_json, MODEL as _MODEL
from src.schema.taxonomy import OPPOSING_RELATIONS, TAXONOMY, RelationType

logger = logging.getLogger(__name__)

_MAX_PAIRS = int(os.getenv("FACT_ALIGNMENT_MAX_PAIRS", "120"))
_WORKERS   = int(os.getenv("FACT_ALIGNMENT_WORKERS", "4"))

# Inverse relations: key is rewritten as value, subject/object swapped.
_INVERSE = {"has_part": "part_of", "derives_from": "converts_to",
            "transcribed_from": "transcribes_to", "translation_of": "translates_to"}
# Symmetric predicates (Biolink): A–B and B–A are the same fact.
_SYMMETRIC = {"interacts_with", "binds_to", "correlates_with", "associates_with", "coexpressed_with",
              "colocalizes_with", "genetically_interacts_with", "pharmacologically_interacts_with",
              "in_complex_with", "paralog_of", "ortholog_of", "overlaps", "resembles",
              "in_linkage_disequilibrium_with"}

_verdict_cache: dict = {}


def _rel(r: dict) -> str:
    return str(getattr(r.get("relation"), "value", r.get("relation")) or "")


def _resolved(cid: str) -> bool:
    return bool(cid) and not str(cid).startswith("TEXT:") and cid != "NEEDS_REVIEW"


def _swap(r: dict) -> None:
    """Exchange every subject_* field with its object_* counterpart."""
    for k in [k for k in r if k.startswith("subject_")]:
        o = "object_" + k[len("subject_"):]
        r[k], r[o] = r.get(o), r[k]


def _canonical(r: dict) -> tuple:
    """(subject, relation, object, negated) after inverse/symmetric rules."""
    s, rel, o = r.get("subject_id", ""), _rel(r), r.get("object_id", "")
    if rel in _INVERSE:
        s, rel, o = o, _INVERSE[rel], s
    if rel in _SYMMETRIC and str(s) > str(o):
        s, o = o, s
    return s, rel, o, bool(r.get("negated"))


def _apply_rules(r: dict) -> bool:
    """Rewrite r into its canonical direction; True if it changed."""
    s, rel, o, _ = _canonical(r)
    if (s, rel, o) == (r.get("subject_id"), _rel(r), r.get("object_id")):
        return False
    if s != r.get("subject_id"):
        _swap(r)
    r["relation"] = rel
    return True


def _sentence(r: dict) -> str:
    """Source sentence quoted in the extraction reasoning."""
    return (r.get("reasoning") or r.get("subject_evidence") or r.get("object_evidence") or "")[:400]


def _definition(rel: str) -> str:
    try:
        return TAXONOMY.get(RelationType(rel), {}).get("definition", "")[:220]
    except ValueError:
        return ""


def _opposing(a: str, b: str) -> bool:
    try:
        ra, rb = RelationType(a), RelationType(b)
    except ValueError:
        return False
    return OPPOSING_RELATIONS.get(ra) == rb or OPPOSING_RELATIONS.get(rb) == ra


def _describe(tag: str, f: dict) -> str:
    rel = _rel(f)
    return (f'{tag}: {f.get("subject_name")} —{rel}→ {f.get("object_name")}\n'
            f'   "{rel}" means: {_definition(rel)}\n'
            f'   Source sentence: "{_sentence(f)}"')


def _same_claim(a: dict, b: dict) -> dict:
    """LLM verdict on both sentences: SAME, DIFFERENT or CONTRADICTION."""
    key = (_canonical(a), _canonical(b), _sentence(a)[:120], _sentence(b)[:120])
    if key in _verdict_cache:
        return _verdict_cache[key]
    prompt = (
        "Two facts were extracted about the same two entities.\n\n"
        f"{_describe('Fact A', a)}\n\n{_describe('Fact B', b)}\n\n"
        "Do the two sentences make the SAME claim about these entities (a paraphrase or restatement, even if "
        "worded differently)? Answer:\n"
        "  SAME          — one claim; merging them into one fact loses nothing\n"
        "  DIFFERENT     — different claims, or one is stronger/weaker/broader than the other\n"
        "  CONTRADICTION — they disagree\n"
        'If SAME, also say which fact\'s relation ("A" or "B") best states what BOTH sentences say.\n'
        'Return ONLY JSON: {"verdict": "SAME|DIFFERENT|CONTRADICTION", "keep": "A|B", "reason": "<one sentence>"}'
    )
    out = {"verdict": "DIFFERENT", "keep": "A", "reason": "check failed"}
    try:
        data = parse_json(call_llm([{"role": "user", "content": prompt}], model=_MODEL, temperature=0.0))
        v = str(data.get("verdict", "")).strip().upper()
        if v in ("SAME", "DIFFERENT", "CONTRADICTION"):
            out = {"verdict": v, "keep": "B" if str(data.get("keep", "A")).strip().upper() == "B" else "A",
                   "reason": str(data.get("reason", ""))[:300]}
            _verdict_cache[key] = out
    except Exception as e:
        logger.warning("Fact check failed: %s", e)
    return out


def _adopt(target: dict, model: dict) -> None:
    """Rewrite target to state model's relation in model's direction."""
    if target.get("subject_id") != model.get("subject_id"):
        _swap(target)
    target["relation"] = model.get("relation")


def _pair_key(r: dict) -> tuple:
    return frozenset((r.get("subject_id"), r.get("object_id"))), bool(r.get("negated"))


def _candidate_pairs(group: list) -> list:
    """Pairs with different, non-opposing relations."""
    return [(i, j) for i, a in enumerate(group) for j, b in enumerate(group[i + 1:], i + 1)
            if _canonical(a)[:3] != _canonical(b)[:3] and not _opposing(_rel(a), _rel(b))]


def align_facts(records: list) -> list:
    """Merge facts that make the same claim within this paper; drop the merged duplicates."""
    n_rules = sum(_apply_rules(r) for r in records if _resolved(r.get("subject_id")) and _resolved(r.get("object_id")))

    groups: dict = defaultdict(list)
    for idx, r in enumerate(records):
        if _resolved(r.get("subject_id")) and _resolved(r.get("object_id")) and not r.get("is_contradiction"):
            groups[_pair_key(r)].append(idx)

    todo = []
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        facts = [records[i] for i in idxs]
        todo += [(idxs[i], idxs[j]) for i, j in _candidate_pairs(facts)]
    todo = todo[:_MAX_PAIRS]
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        verdicts = list(ex.map(lambda p: _same_claim(records[p[0]], records[p[1]]), todo))

    merged_into: dict = {}
    for (i, j), v in zip(todo, verdicts):
        if v["verdict"] != "SAME":
            continue
        keep, drop = (i, j) if v["keep"] == "A" else (j, i)
        while keep in merged_into:
            keep = merged_into[keep]
        if drop in merged_into or keep == drop:
            continue
        k, d = records[keep], records[drop]
        note = {"relation": _rel(d), "subject": d.get("subject_name"), "object": d.get("object_name"),
                "sentence": _sentence(d), "reason": v["reason"], "scope": "paper"}
        k.setdefault("merged_facts", []).extend([*d.get("merged_facts", []), note])      # keep earlier merges too
        k.setdefault("supporting_evidence", []).extend([*d.get("supporting_evidence", []), _sentence(d)])
        merged_into[drop] = keep

    kept = [r for i, r in enumerate(records) if i not in merged_into]
    logger.info("Fact alignment: %d rewritten by relation rules, %d merged as the same claim (%d pairs checked)",
                n_rules, len(merged_into), len(todo))
    return kept


# ── Against the knowledge graph ──────────────────────────────────────────────

def _kg_facts() -> list:
    from src.postextraction.atomspace_alignment import _kg_paths
    facts, seen = [], set()
    for path in _kg_paths():
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT * FROM triples WHERE flagged_for_review = 0"):
                r = dict(row)
                r["negated"] = bool(r.get("negated"))
                key = (r["subject_id"], r["relation"], r["object_id"], r["negated"])
                if key not in seen:
                    seen.add(key)
                    facts.append(r)
            conn.close()
        except Exception as e:
            logger.warning("Could not read KG facts from %s: %s", path, e)
    return facts


def align_facts_with_kg(records: list) -> list:
    """Restate a paper fact as the KG states the same claim."""
    kg = _kg_facts()
    if not kg:
        return records
    by_pair: dict = defaultdict(list)
    for f in kg:
        by_pair[_pair_key(f)].append(f)

    todo, n_rule = [], 0
    for r in records:
        if not (_resolved(r.get("subject_id")) and _resolved(r.get("object_id"))) or r.get("is_contradiction"):
            continue
        existing = by_pair.get(_pair_key(r), [])
        same_rule = [f for f in existing if _canonical(f)[:3] == _canonical(r)[:3]]
        if same_rule:
            f = same_rule[0]
            if (f["subject_id"], f["relation"]) != (r.get("subject_id"), _rel(r)):
                _adopt(r, f)                                   # same fact, other direction
                n_rule += 1
            continue
        todo += [(r, f) for f in existing if not _opposing(_rel(r), f["relation"])]
    todo = todo[:_MAX_PAIRS]
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        verdicts = list(ex.map(lambda p: _same_claim(p[0], p[1]), todo))

    adopted = set()
    for (r, f), v in zip(todo, verdicts):
        if v["verdict"] == "SAME" and v["keep"] == "B" and id(r) not in adopted:
            old = _rel(r)
            _adopt(r, f)
            r.setdefault("merged_facts", []).append({"relation": old, "subject": r.get("subject_name"),
                                                     "object": r.get("object_name"), "sentence": _sentence(r),
                                                     "reason": v["reason"], "scope": "kg", "kg_relation": f["relation"]})
            adopted.add(id(r))
    logger.info("KG fact alignment: %d restated by relation rules, %d matched an existing claim (%d pairs checked)",
                n_rule, len(adopted), len(todo))
    return records
