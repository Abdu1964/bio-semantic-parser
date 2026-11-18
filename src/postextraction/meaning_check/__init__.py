"""Layer 7 step 1b — meaning check: does each resolved entity's ID mean what the authors mean?

Word matching finds an ID whose label fits the name; this step asks the LLM, with the sentence, the fact and
the candidates' definitions, whether the concept is the one meant ('erosion' in 'epigenome erosion' is not
geological erosion; 'IRF family' is not the gene TRIM63 whose alias is IRF). The ID is kept, replaced by a
better candidate, or left unresolved. Gene and protein symbols keep their database lookups.
MEANING_CHECK=false turns the step off.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

from src.postextraction import entity_normalization as en
from src.postextraction.meaning_check.definitions import candidates, describe
from src.postextraction.meaning_check.judge import judge_batch

ENABLED = os.getenv("MEANING_CHECK", "true").lower() == "true"
_WORKERS = int(os.getenv("MEANING_CHECK_WORKERS", "8"))
_ALTERNATIVES = 5
_BATCH = int(os.getenv("MEANING_CHECK_BATCH", "6"))      # concepts per LLM call


def _needs_check(record: dict, side: str) -> bool:
    cid = record.get(f"{side}_id", "")
    if not en._resolved(cid) or record.get(f"{side}_id_source") == "pubtator3":
        return False
    fam = en._TYPE_FAMILY.get(record.get(f"{side}_type"))
    if fam in ("gene", "protein") and en._symbol_key(record.get(f"{side}_name", "")):
        return False                                   # a gene symbol: the gene databases decide
    return True


def _prepare(record: dict, side: str) -> dict:
    """What the judge needs for one concept: the sentence, the fact, the current concept and alternatives."""
    name, etype, cid = record[f"{side}_name"], record.get(f"{side}_type", "OTHER"), record[f"{side}_id"]
    known = describe(cid)
    if known is None:
        return None                                    # the ontology service is unreachable: no evidence, no verdict
    current = {"id": cid, "label": known.get("label") or record.get(f"{side}_canonical_name") or name,
               "definition": known.get("definition", ""),
               "synonyms": known.get("synonyms") or [x for x in str(record.get(f"{side}_synonyms") or "").split("; ") if x]}
    alts = [c for c in candidates(name, en._TYPE_TO_ONTOLOGIES.get(etype, [])) if c["id"] != cid][:_ALTERNATIVES]
    fact = f"{record.get('subject_name')} —{record.get('relation')}→ {record.get('object_name')}"
    sentence = record.get(f"{side}_evidence") or en._extract_context_sentence(record.get("_chunk_text", ""), name) or fact
    return {"name": name, "type": etype, "role": side, "fact": fact, "sentence": sentence,
            "current": current, "alts": alts}


def _outcome(item: dict, verdict: str, index) -> tuple:
    """Only an ID that names another concept changes: to the alternative that is the concept meant, else none.
    The same or a broader concept is kept — a broader ID is still true, and a swap would risk a wrong one."""
    if verdict != "DIFFERENT":
        return "keep", item["current"]
    if index is not None:
        return "replace", item["alts"][index]
    return "none", None


def run(records: list, log=print) -> dict:
    """Check every resolved, non-symbol entity once per (name, type, ID) and apply the verdict to all mentions."""
    stats = {"checked": 0, "kept": 0, "replaced": 0, "unresolved": 0}
    if not ENABLED:
        return stats
    mentions: dict = {}
    for r in records:
        for side in ("subject", "object"):
            if _needs_check(r, side):
                key = (r[f"{side}_name"].strip().lower(), r.get(f"{side}_type"), r[f"{side}_id"])
                mentions.setdefault(key, []).append((r, side))
    if not mentions:
        return stats
    keys = list(mentions)
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        prepared = dict(zip(keys, ex.map(lambda k: _prepare(*mentions[k][0]), keys)))   # lookups, in parallel
        for rnd in range(1, en._NETWORK_RETRY_ROUNDS + 1):                     # ontology service down: wait, retry
            missing = [k for k, it in prepared.items() if it is None]
            if not missing:
                break
            log(f"  Meaning check: ontology service unreachable for {len(missing)} concept(s) — "
                f"retry {rnd}/{en._NETWORK_RETRY_ROUNDS} in {en._NETWORK_RETRY_WAIT * rnd} s")
            time.sleep(en._NETWORK_RETRY_WAIT * rnd)
            en.reset_network_state()
            prepared.update(zip(missing, ex.map(lambda k: _prepare(*mentions[k][0]), missing)))
        stats["unverified"] = sum(1 for it in prepared.values() if it is None)  # still unreachable: IDs kept
        keys = [k for k, it in prepared.items() if it is not None]
        items = [prepared[k] for k in keys]
        batches = [items[i:i + _BATCH] for i in range(0, len(items), _BATCH)]
        judged = [v for b in ex.map(judge_batch, batches) for v in b]           # several concepts per LLM call
    verdicts = {k: _outcome(it, *v) for k, it, v in zip(keys, items, judged)}
    for key, (verdict, cand) in verdicts.items():
        stats["checked"] += 1
        stats[{"keep": "kept", "replace": "replaced", "none": "unresolved"}[verdict]] += 1
        if verdict == "keep":
            continue
        for r, side in mentions[key]:
            r[f"{side}_meaning_rejected"] = r[f"{side}_id"]          # kept for audit
            if verdict == "replace":
                r[f"{side}_id"], r[f"{side}_canonical_name"] = cand["id"], cand["label"]
                r[f"{side}_synonyms"] = "; ".join(cand.get("synonyms") or [])
                r[f"{side}_id_source"] = "meaning_check"
                r[f"{side}_source_url"] = en._build_source_url(cand["id"], "ols4", cand.get("prefix", ""), "", "")
            else:
                r[f"{side}_id"] = f"TEXT:{en._slug(r[f'{side}_name'])}"
                r[f"{side}_canonical_name"], r[f"{side}_id_source"] = "", "meaning_check"
    en.logger.info("Meaning check: %s", stats)
    return stats
