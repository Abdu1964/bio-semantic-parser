"""Direction of findings the text calls reversed/restored/normalized, from the change the paper states."""
import re

from src.schema.taxonomy import OPPOSING_RELATIONS, RelationType

_REVERSAL = re.compile(r"\b(revers(?:ed|es|al|ing)|restor(?:ed|es|ing)|normali[sz](?:ed|es|ation)|rescu(?:ed|es)|reverted)\b", re.I)


def _rel(value):
    try:
        return RelationType(value)
    except ValueError:
        return None


def _key(r: dict, side: str) -> str:
    cid = str(r.get(f"{side}_id") or "")
    return cid if cid and not cid.startswith("TEXT:") else (r.get(f"{side}_name") or "").lower()


def check(records: list, chunk_text: dict) -> int:
    """chunk_text: 'section|index' (chunk numbers restart in each section) → text."""
    """Flip a reversal finding to the opposite of the stated change; 'regulates' for listed items with none."""
    stated = {}                                   # object → [(record, relation as extracted)]
    for r in records:
        if _rel(r.get("relation")) in OPPOSING_RELATIONS and not r.get("negated"):
            stated.setdefault(_key(r, "object"), []).append((r, _rel(r["relation"])))
    changed = 0
    for r in records:
        rel = _rel(r.get("relation"))
        if rel not in OPPOSING_RELATIONS or r.get("negated"):
            continue
        obj, subj = (r.get("object_name") or "").lower(), (r.get("subject_name") or "").lower()
        text = chunk_text.get(f"{r.get('section')}|{r.get('chunk_index')}") or chunk_text.get(str(r.get("chunk_index")), "")
        sents = [s.lower() for s in re.split(r"(?<=[.!?])\s+", text)
                 if _REVERSAL.search(s) and obj in s.lower() and subj in s.lower()]
        if not sents:
            continue
        before = [(o, orel) for o, orel in stated.get(_key(r, "object"), []) if o is not r
                  and _key(o, "subject") != _key(r, "subject")
                  and orel in (rel, OPPOSING_RELATIONS[rel])
                  and any((o.get("subject_name") or "").lower() in s for s in sents)]
        listed = any(re.search(rf"(,\s*(?:and\s+|or\s+)?{re.escape(obj)}\b|\b{re.escape(obj)}\s*,)", x) for x in sents)
        if not before and not listed:                # a single named entity: its direction is in the sentence
            continue
        new = OPPOSING_RELATIONS[before[0][1]] if before else RelationType.REGULATES
        if new != rel:
            r["relation"] = new.value
            r["direction_note"] = (f"reversal of '{before[0][0]['subject_name']} {before[0][1].value}'"
                                   if before else "reversal with no stated direction")
            changed += 1
    return changed
