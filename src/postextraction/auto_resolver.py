"""Auto-resolution — runs before human_review.jsonl is written.
High/medium confidence fixes are committed directly to KG.
Only genuinely unresolvable items go to human review.
"""
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
_LLM_API_KEY  = os.getenv("LLM_API_KEY", "ollama")
_LLM_MODEL    = os.getenv("LLM_MODEL", "gemma2:27b")

_llm_client = None


def _get_client():
    global _llm_client
    if _llm_client is None:
        from openai import OpenAI
        _llm_client = OpenAI(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL, timeout=20.0)
    return _llm_client


def _call_llm(prompt: str, max_tokens: int = 120) -> str:
    try:
        resp = _get_client().chat.completions.create(
            model=_LLM_MODEL,
            temperature=0.0,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.debug("auto_resolver LLM call failed: %s", e)
        return ""


def _parse_json(text: str) -> Optional[dict]:
    m = re.search(r'\{[^}]+\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


def _resolve_entity_id(name: str, entity_type: str, context: str, species: str = "") -> Optional[dict]:
    """Re-run normalization with the source passage."""
    from src.postextraction.entity_normalization import normalize_entity
    result = normalize_entity(name, entity_type, existing_id=None, species=species, context=context)
    cid = result.get("canonical_id", "")
    if cid and not cid.startswith("TEXT:") and cid != "NEEDS_REVIEW":
        return result
    return None


def _plain(value) -> str:
    """Enum → its value ('RelationType.INHIBITS' → 'inhibits'); everything else → str."""
    return str(getattr(value, "value", value) or "")


def _valid_relations() -> set:
    from src.schema.taxonomy import RelationType
    return {r.value for r in RelationType}


def _as_relation(raw: str) -> str:
    """Normalise an LLM relation answer; '' unless it is a taxonomy relation."""
    rel = re.sub(r"^relationtype\.", "", str(raw or "").strip().lower()).replace(" ", "_").replace("-", "_")
    return rel if rel in _valid_relations() else ""


def _record_text(record: dict, fallback: str = "") -> str:
    """The passage this triple came from — its chunk, else its evidence sentences."""
    return (record.get("_chunk_text")
            or " ".join(x for x in (record.get("subject_evidence"), record.get("object_evidence")) if x)
            or fallback)


def _llm_fix_relation(record: dict, source_text: str) -> Optional[str]:
    """Ask the LLM for a corrected taxonomy relation; returns it, or None."""
    allowed = ", ".join(sorted(_valid_relations()))
    prompt = (
        f'A biomedical relation was extracted but failed validation.\n'
        f'Triple:\n'
        f'  Subject: {_plain(record.get("subject_name"))} (type: {_plain(record.get("subject_type"))})\n'
        f'  Relation: {_plain(record.get("relation"))}\n'
        f'  Object: {_plain(record.get("object_name"))} (type: {_plain(record.get("object_type"))})\n'
        f'Validation issue: {(record.get("validation_reasoning") or record.get("review_reason") or "")[:300]}\n'
        f'Source text: "{source_text[:1500]}"\n\n'
        f'Choose the relation that the source text actually states between these two entities.\n'
        f'Allowed relations (use one exactly): {allowed}\n'
        f'If no allowed relation fits, answer "none".\n\n'
        f'Respond ONLY with JSON: {{"relation": "<one allowed relation or none>", "confidence": "<high|medium|low>"}}'
    )
    data = _parse_json(_call_llm(prompt, max_tokens=60))
    if not data or data.get("confidence", "low") == "low":
        return None
    return _as_relation(data.get("relation")) or None


def auto_resolve_flagged(records: list, text_by_doc: dict) -> tuple:
    """
    Process flagged records before human_review.jsonl is written.
    Returns (updated_records, n_auto_resolved).
    High/medium confidence fixes: flagged_for_review cleared, auto_resolved=True.
    Low confidence or unresolvable: flagged_for_review stays True.
    """
    n_resolved = 0

    for r in records:
        if not r.get("flagged_for_review"):
            continue

        doc_id      = r.get("document_id", "")
        source_text = _record_text(r, text_by_doc.get(doc_id, ""))
        resolved    = False

        # ── Case 1: TEXT: entities — re-run normalization with source context ──
        s_id = r.get("subject_id", "")
        o_id = r.get("object_id",  "")
        s_needs = s_id.startswith("TEXT:") or s_id == "NEEDS_REVIEW"
        o_needs = o_id.startswith("TEXT:") or o_id == "NEEDS_REVIEW"

        if s_needs:
            fix = _resolve_entity_id(
                r.get("subject_name", ""),
                r.get("subject_type", "OTHER"),
                source_text,
                r.get("species", ""),
            )
            if fix and not fix.get("needs_review"):
                r["subject_id"]            = fix["canonical_id"]
                r["subject_canonical_name"]= fix.get("canonical_name", "")
                r["subject_id_source"]     = fix.get("id_source", "llm")
                r["subject_needs_review"]  = False
                r["subject_source_url"]    = fix.get("source_url", "")
                logger.info("auto_resolver: resolved subject %r → %s", r["subject_name"], fix["canonical_id"])
                resolved = True

        if o_needs:
            fix = _resolve_entity_id(
                r.get("object_name", ""),
                r.get("object_type", "OTHER"),
                source_text,
                r.get("species", ""),
            )
            if fix and not fix.get("needs_review"):
                r["object_id"]            = fix["canonical_id"]
                r["object_canonical_name"]= fix.get("canonical_name", "")
                r["object_id_source"]     = fix.get("id_source", "llm")
                r["object_needs_review"]  = False
                r["object_source_url"]    = fix.get("source_url", "")
                logger.info("auto_resolver: resolved object %r → %s", r["object_name"], fix["canonical_id"])
                resolved = True

        # After entity resolution, check if both IDs are now valid
        s_id_new = r.get("subject_id", "")
        o_id_new = r.get("object_id",  "")
        s_still_bad = s_id_new.startswith("TEXT:") or s_id_new == "NEEDS_REVIEW"
        o_still_bad = o_id_new.startswith("TEXT:") or o_id_new == "NEEDS_REVIEW"

        # ── Case 2: Semantic REVIEW/REJECT/SKIPPED — fix the relation ──────────
        verdict = r.get("validation_verdict", "")
        if verdict in ("REVIEW", "REJECT", "SKIPPED") and not (s_still_bad or o_still_bad):
            new_rel = _llm_fix_relation(r, source_text)
            if new_rel:
                # Only the relation changes; then re-validate
                candidate = {**r, "relation": new_rel}
                from src.postextraction import semantic_validation
                checked = semantic_validation.validate(candidate, source_text)
                if checked.get("validation_verdict") == "VALID":
                    r.update({k: v for k, v in checked.items() if k.startswith("validation_") or k == "relation"})
                    r["auto_resolved_relation"] = True
                    verdict = "VALID"
                    logger.info("auto_resolver: re-validated [%s] → [%s] → [%s]",
                                r.get("subject_name"), new_rel, r.get("object_name"))
                    resolved = True

        # ── Clear flag only when both entities resolved AND relation fixed ─────
        s_final = r.get("subject_id", "")
        o_final = r.get("object_id",  "")
        both_valid = (
            s_final and not s_final.startswith("TEXT:") and s_final != "NEEDS_REVIEW"
            and o_final and not o_final.startswith("TEXT:") and o_final != "NEEDS_REVIEW"
        )
        relation_ok = verdict not in ("REVIEW", "REJECT", "SKIPPED")

        if both_valid and relation_ok and resolved:
            r["flagged_for_review"] = False
            r["auto_resolved"]      = True
            r["review_reason"]      = ""
            n_resolved += 1

    return records, n_resolved
