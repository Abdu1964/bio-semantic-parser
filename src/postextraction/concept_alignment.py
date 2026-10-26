"""Layer 7 — concept alignment: cross-references, SapBERT candidates, LLM check; only SAME merges."""
import logging
import os
import threading
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from src.llm_client import call_llm, parse_json, MODEL as _MODEL
from src.postextraction import entity_normalization as en

logger = logging.getLogger(__name__)

_EMBED_MODEL   = os.getenv("CONCEPT_EMBEDDING_MODEL", "cambridgeltl/SapBERT-from-PubMedBERT-fulltext")
_SIM_THRESHOLD = float(os.getenv("CONCEPT_SIM_THRESHOLD", "0.75"))
_TOP_K         = int(os.getenv("CONCEPT_TOP_K", "5"))
_MAX_PAIRS     = int(os.getenv("CONCEPT_ALIGNMENT_MAX_PAIRS", "150"))
_WORKERS       = int(os.getenv("CONCEPT_ALIGNMENT_WORKERS", "4"))
_FIELDS        = ("id", "canonical_name", "id_source", "source_url", "synonyms", "type")
_OXO_URL       = "https://www.ebi.ac.uk/spot/oxo/api/search"
_PREFIX_ALIAS  = {"msh": "mesh", "nci": "ncit", "ncbi_taxon": "ncbitaxon", "wikidata": "wd", "umls_cui": "umls"}
_WD_XREF_PROPS = {"P486": "mesh", "P2892": "umls", "P5270": "mondo", "P699": "doid", "P686": "go",
                  "P3841": "hp", "P1748": "ncit", "P683": "chebi", "P685": "ncbitaxon", "P1554": "uberon"}

_xref_cache: dict = {}
_definition_cache: dict = {}
_verdict_cache: dict = {}


# ── IDs and cross-references ─────────────────────────────────────────────────

def _key(cid: str) -> str:
    """Comparable ID form ('MESH:D000375' == 'mesh:D000375')."""
    prefix, _, local = (cid or "").strip().partition(":")
    prefix = prefix.lower()
    return f"{_PREFIX_ALIAS.get(prefix, prefix)}:{local.strip()}" if local else ""


def _resolved(cid: str) -> bool:
    return bool(cid) and not cid.startswith("TEXT:") and cid != "NEEDS_REVIEW"


def _ols_term(cid: str) -> dict:
    """OLS4 term JSON for an OBO-style or MeSH ID; {} when unavailable."""
    prefix, local = cid.split(":", 1)
    p = prefix.lower()
    iri = (f"http://id.nlm.nih.gov/mesh/{local}" if p == "mesh"
           else en._OLS4_IRI_TEMPLATE.get(p, "http://purl.obolibrary.org/obo/{prefix}_{bare}")
                  .format(prefix=prefix.upper(), bare=local))
    enc = quote(quote(iri, safe=""), safe="")
    r = en._http_get(f"{en._OLS_BASE}/ontologies/{p}/terms/{enc}", timeout=en._TIMEOUT)
    return r.json() if r.ok else {}


def _wikidata(qid: str) -> dict:
    r = en._http_get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", timeout=en._TIMEOUT)
    return (r.json().get("entities") or {}).get(qid, {}) if r.ok else {}


def _lookup(cid: str) -> tuple:
    """(cross-reference keys, definition) for a concept."""
    if cid in _xref_cache:
        return _xref_cache[cid], _definition_cache.get(cid, "")
    e0, xrefs, definition = en._net_errors(), set(), ""
    try:
        if cid.upper().startswith("WD:"):
            ent = _wikidata(cid.split(":", 1)[1])
            claims = ent.get("claims") or {}
            for prop, prefix in _WD_XREF_PROPS.items():
                for c in claims.get(prop, []):
                    v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                    if isinstance(v, str):
                        xrefs.add(_key(v if ":" in v else f"{prefix}:{v}"))
            definition = ((ent.get("descriptions") or {}).get("en") or {}).get("value", "")
        else:
            term = _ols_term(cid)
            ann = term.get("annotation") or {}
            for x in (ann.get("database_cross_reference") or []) + (ann.get("has exact match") or []):
                if ":" in str(x):
                    xrefs.add(_key(str(x)))
            desc = term.get("description") or []
            definition = desc[0] if isinstance(desc, list) and desc else str(desc or "")
        r = en._http_post(_OXO_URL, json={"ids": [cid], "distance": 1}, params={"size": 100},
                          timeout=en._TIMEOUT)
        if r.ok:
            for res in (r.json().get("_embedded") or {}).get("searchResults", []):
                xrefs |= {_key(m.get("curie", "")) for m in res.get("mappingResponseList", [])}
    except Exception as e:
        logger.warning("Cross-reference lookup failed for %r: %s", cid, e)
    xrefs.discard("")
    if en._net_errors() == e0:
        _xref_cache[cid], _definition_cache[cid] = xrefs, definition
    return xrefs, definition


def _xref_says_same(a: dict, b: dict) -> bool:
    """A database maps A to exactly B (or both to one UMLS concept)."""
    def points_to(x, y):
        same_onto = {k for k in x["xrefs"] if k.split(":")[0] == y["key"].split(":")[0]}
        return same_onto == {y["key"]}
    umls_a = {k for k in a["xrefs"] if k.startswith("umls:")}
    umls_b = {k for k in b["xrefs"] if k.startswith("umls:")}
    return points_to(a, b) or points_to(b, a) or (len(umls_a) == 1 and umls_a == umls_b)


# ── Concepts in this paper ───────────────────────────────────────────────────

def _collect(records: list) -> dict:
    concepts: dict = {}
    for r in records:
        for side in ("subject", "object"):
            cid = r.get(f"{side}_id", "")
            if not _resolved(cid):
                continue
            c = concepts.setdefault(cid, {
                "id": cid, "key": _key(cid), "prefix": en._ontology_prefix_of(cid),
                "rank": en._ontology_rank(en._ontology_prefix_of(cid)),
                "fam": en._PREFIX_FAMILY.get(en._ontology_prefix_of(cid))
                       or en._TYPE_FAMILY.get(str(getattr(r.get(f"{side}_type"), "value", r.get(f"{side}_type"))), "concept"),
                "label": r.get(f"{side}_canonical_name") or r.get(f"{side}_name") or "",
                "names": set(), "evidence": [], "mentions": 0,
                "rec": {f: r.get(f"{side}_{f}") for f in _FIELDS},
            })
            c["mentions"] += 1
            c["names"].add(r.get(f"{side}_name") or "")
            ev = r.get(f"{side}_evidence") or ""
            if ev and ev not in c["evidence"] and len(c["evidence"]) < 2:
                c["evidence"].append(ev)
    for c in concepts.values():
        c["names"] = {n for n in c["names"] | {c["label"]} if n.strip()}
    return concepts


# ── Meaning-based candidates (SapBERT) ───────────────────────────────────────

_model_lock = threading.Lock()
_model: dict = {}


def _embedder():
    """Lazy-load SapBERT; None if unavailable."""
    with _model_lock:
        if "m" not in _model:
            try:
                from transformers import AutoModel, AutoTokenizer
                _model["tok"] = AutoTokenizer.from_pretrained(_EMBED_MODEL)
                _model["m"] = AutoModel.from_pretrained(_EMBED_MODEL).eval()
            except Exception as e:
                logger.warning("Concept embedding model unavailable (%s) — semantic candidates skipped", e)
                _model["m"] = None
        return _model.get("m") and _model


_INDEX_PATH = os.getenv("CONCEPT_INDEX_PATH", "data/concept_index.db")
_vec_cache: dict = {}


def _index_conn():
    import sqlite3
    Path(_INDEX_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_INDEX_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS name_vectors (model TEXT, name TEXT, vec BLOB, PRIMARY KEY (model, name))")
    return conn


def _embed(names: list):
    """Normalised SapBERT vectors, cached in memory and on disk."""
    import torch
    todo = [n for n in dict.fromkeys(names) if n not in _vec_cache]
    if todo:
        try:
            with _index_conn() as conn:
                for i in range(0, len(todo), 500):
                    part = todo[i:i + 500]
                    q = f"SELECT name, vec FROM name_vectors WHERE model=? AND name IN ({','.join('?' * len(part))})"
                    for name, vec in conn.execute(q, [_EMBED_MODEL, *part]):
                        _vec_cache[name] = torch.frombuffer(bytearray(vec), dtype=torch.float32)
        except Exception as e:
            logger.warning("Concept index unavailable: %s", e)
        todo = [n for n in todo if n not in _vec_cache]
    if todo:
        mdl = _embedder()
        with torch.no_grad():
            for i in range(0, len(todo), 64):
                part = todo[i:i + 64]
                batch = mdl["tok"](part, padding=True, truncation=True, max_length=25, return_tensors="pt")
                vecs = torch.nn.functional.normalize(mdl["m"](**batch).last_hidden_state[:, 0], dim=1)
                for n, v in zip(part, vecs):
                    _vec_cache[n] = v.clone()
        try:
            with _index_conn() as conn:
                conn.executemany("INSERT OR REPLACE INTO name_vectors VALUES (?, ?, ?)",
                                 [(_EMBED_MODEL, n, _vec_cache[n].numpy().tobytes()) for n in todo])
        except Exception as e:
            logger.warning("Could not save concept vectors: %s", e)
    return torch.stack([_vec_cache[n] for n in names])


def _semantic_candidates(concepts: dict, left: list = None, right: list = None) -> list:
    """[(score, a, b)] same-kind pairs close in meaning (top-k per concept)."""
    if not _embedder() or len(concepts) < 2:
        return []
    import torch
    left = left if left is not None else list(concepts)
    right = right if right is not None else list(concepts)
    if not left or not right:
        return []

    def stack(ids):
        owners, names = [], []
        for i, cid in enumerate(ids):
            for n in concepts[cid]["names"]:
                owners.append(i); names.append(n)
        return torch.tensor(owners), _embed(names)

    lo, le = stack(left)
    ro, re_ = stack(right)
    sim = le @ re_.T
    best = torch.full((len(left), len(right)), -1.0)
    for i in range(len(left)):
        rows = sim[lo == i]
        for j in range(len(right)):
            if left[i] != right[j]:
                best[i, j] = rows[:, ro == j].max()
    pairs = {}
    for i, a in enumerate(left):
        for j in torch.topk(best[i], min(_TOP_K, len(right))).indices.tolist():
            s, b = float(best[i, j]), right[j]
            if a != b and s >= _SIM_THRESHOLD and concepts[a]["fam"] == concepts[b]["fam"]:
                key = tuple(sorted((a, b))) if left == right else (a, b)
                pairs[key] = max(s, pairs.get(key, 0))
    return sorted(((s, a, b) for (a, b), s in pairs.items()), reverse=True)


# ── Verification ─────────────────────────────────────────────────────────────

def _blocked(a: dict, b: dict) -> str:
    """Rule-based reasons two concepts can never be the same."""
    if a["fam"] != b["fam"]:
        return "different kinds"
    if a["prefix"] == b["prefix"]:
        return "two terms of the same ontology"
    for x, y in ((a, b), (b, a)):
        if any(en._drops_negation(n, m) for n in x["names"] for m in y["names"]):
            return "one negates the other"
        if en._wrong_organism(x["label"], _organism_of(y["label"])):
            return "different organisms"
    return ""


def _organism_of(label: str) -> str:
    import re
    m = re.search(r"\(([^()]+)\)\s*$", label or "")
    genus = en._LABEL_ORGANISMS.get(m.group(1).strip().lower()) if m else ""
    return genus or ""


def _describe(c: dict) -> str:
    names = ", ".join(sorted(c["names"]))[:200]
    ev = " | ".join(e[:250] for e in c["evidence"])
    return (f'"{c["label"]}" ({c["id"]})\n  Definition: {c["definition"] or "(none)"}\n'
            f'  Written in the paper as: {names}\n  Sentence(s): {ev or "(none)"}')


def _verify(a: dict, b: dict) -> dict:
    """LLM classifies the relation between two concepts; only SAME is merged."""
    key = tuple(sorted((a["id"], b["id"])))
    if key in _verdict_cache:
        return _verdict_cache[key]
    prompt = (
        "Two concepts were found in the same paper. Using their ontology definitions and how the paper uses "
        "them, how is A related to B?\n\n"
        f"A: {_describe(a)}\n\nB: {_describe(b)}\n\n"
        "Answer with one of:\n"
        "  SAME      — the same concept (synonyms, spelling or wording variants)\n"
        "  BROADER   — A is a more general concept than B\n"
        "  NARROWER  — A is a more specific concept than B (e.g. cellular senescence vs senescence)\n"
        "  RELATED   — associated but different (e.g. a process and its regulation, a disease and its symptom)\n"
        "  OPPOSITE  — opposite meaning (e.g. aging vs nonaging)\n"
        "  DIFFERENT — unrelated\n"
        "Say SAME only if merging them into one node would lose no meaning. If unsure, say DIFFERENT.\n"
        'Return ONLY JSON: {"relation": "<one label>", "reason": "<one sentence>"}'
    )
    verdict = {"relation": "DIFFERENT", "reason": "check failed"}
    try:
        data = parse_json(call_llm([{"role": "user", "content": prompt}], model=_MODEL, temperature=0.0))
        rel = str(data.get("relation", "")).strip().upper()
        if rel in ("SAME", "BROADER", "NARROWER", "RELATED", "OPPOSITE", "DIFFERENT"):
            verdict = {"relation": rel, "reason": str(data.get("reason", ""))[:300]}
            _verdict_cache[key] = verdict
    except Exception as e:
        logger.warning("Concept check failed for %r vs %r: %s", a["id"], b["id"], e)
    return verdict


# ── Merge ────────────────────────────────────────────────────────────────────

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
        self.parent[self.find(a)] = self.find(b)


def _merged_synonyms(*values) -> str:
    seen: dict = {}
    for v in values:
        for s in (v.split("; ") if isinstance(v, str) else list(v or [])):
            if s and s.lower() not in seen:
                seen[s.lower()] = s
    return "; ".join(seen.values())


def align_concepts(records: list) -> list:
    """Merge same-meaning entities into one node per concept."""
    concepts = _collect(records)
    if len(concepts) < 2:
        return records

    with ThreadPoolExecutor(max_workers=8) as ex:
        for cid, (xrefs, definition) in zip(concepts, ex.map(_lookup, concepts)):
            concepts[cid]["xrefs"], concepts[cid]["definition"] = xrefs, definition

    uf, decisions = _UnionFind(), {}
    by_fam: dict = defaultdict(list)
    for c in concepts.values():
        by_fam[c["fam"]].append(c)

    # 1. Cross-references stated by the databases.
    for group in by_fam.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if not _blocked(a, b) and _xref_says_same(a, b):
                    uf.union(a["id"], b["id"])
                    decisions[(a["id"], b["id"])] = {"method": "cross_reference", "score": 1.0,
                                                     "reason": "ontology cross-reference"}

    # 2–3. Meaning-based candidates, each confirmed by the LLM.
    todo = [(s, a, b) for s, a, b in _semantic_candidates(concepts)
            if a in concepts and b in concepts and uf.find(a) != uf.find(b)
            and not _blocked(concepts[a], concepts[b])][:_MAX_PAIRS]
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        verdicts = list(ex.map(lambda p: _verify(concepts[p[1]], concepts[p[2]]), todo))
    for (s, a, b), v in zip(todo, verdicts):
        if v["relation"] == "SAME":
            uf.union(a, b)
            decisions[(a, b)] = {"method": "semantic", "score": round(s, 3), "reason": v["reason"]}
        logger.info("Concept check %r vs %r (%.2f): %s", concepts[a]["label"], concepts[b]["label"], s, v["relation"])

    groups: dict = defaultdict(list)
    for cid in concepts:
        groups[uf.find(cid)].append(cid)
    target: dict = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = min(members, key=lambda m: (concepts[m]["rank"], -concepts[m]["mentions"], m))
        for m in members:
            if m != keep:
                target[m] = keep
    if not target:
        return records

    reason_for = {}
    for (a, b), d in decisions.items():
        for x in (a, b):
            reason_for.setdefault(x, d)
    for r in records:
        for side in ("subject", "object"):
            old = r.get(f"{side}_id", "")
            keep = target.get(old)
            if not keep:
                continue
            winner = concepts[keep]["rec"]
            for f in _FIELDS:
                r[f"{side}_{f}"] = winner[f]
            r[f"{side}_synonyms"] = _merged_synonyms(winner.get("synonyms"), concepts[old]["rec"].get("synonyms"),
                                                     sorted(concepts[old]["names"]))
            r[f"{side}_aliases"] = sorted({*r.get(f"{side}_aliases", []), old})
            r[f"{side}_alignment"] = {"from": old, "from_name": concepts[old]["label"], "to": keep,
                                      "to_name": concepts[keep]["label"], **reason_for.get(old, {})}
    logger.info("Concept alignment merged %d concept(s): %s", len(target), target)
    return records
