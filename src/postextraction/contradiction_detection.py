"""Layer 7 step 3 — flag opposing relations for the same entity pair as contradictions."""
import sqlite3
from pathlib import Path

from src.schema.taxonomy import OPPOSING_RELATIONS, RelationType
from src.postextraction import staging


_DB_PATH = Path("data/triple_store.db")


def _get_conn() -> sqlite3.Connection:
    import os as _os
    path = Path(staging.path(str(_DB_PATH)))
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def check_contradiction(record: dict) -> dict:
    """Flag the record if an opposing relation, or the same relation negated, exists for the entity pair."""
    try:
        rel_enum = RelationType(record.get("relation", ""))
    except ValueError:
        return record   # unknown relation type — skip

    subject_id = record.get("subject_id", "")
    object_id  = record.get("object_id",  "")
    if not subject_id or not object_id:
        return record

    import os as _os
    db_path = Path(staging.path(str(_DB_PATH)))
    if not db_path.exists():
        return record

    negated  = 1 if record.get("negated") else 0
    opposing = OPPOSING_RELATIONS.get(rel_enum)
    checks = [(rel_enum.value, 1 - negated, f"'{rel_enum.value}' is both stated and negated")]
    if opposing is not None:
        checks.append((opposing.value, negated, f"'{rel_enum.value}' conflicts with existing '{opposing.value}'"))

    conn = _get_conn()
    hit = None
    for rel, neg, what in checks:
        row = conn.execute(
            "SELECT id, source_papers FROM triples "
            "WHERE subject_id=? AND relation=? AND object_id=? AND negated=?",
            (subject_id, rel, object_id, neg),
        ).fetchone()
        if row:
            hit = (row, what)
            break
    conn.close()

    if hit:
        row, what = hit
        reason = (f"CONTRADICTION: {what} for same entity pair "
                  f"(triple_id={row['id']} sources={row['source_papers']})")
        existing_reason = record.get("review_reason", "")
        combined = (existing_reason + " | " + reason).strip(" |")
        return {
            **record,
            "is_contradiction":   True,
            "flagged_for_review": True,
            "review_reason":      combined,
        }

    return record


def flag_existing_as_contradiction(triple_id: int, reason: str) -> None:
    """Mark an already-stored triple as a contradiction (called when new opposing arrives)."""
    import os as _os
    db_path = Path(staging.path(str(_DB_PATH)))
    if not db_path.exists():
        return
    conn = _get_conn()
    conn.execute(
        "UPDATE triples SET is_contradiction=1, flagged_for_review=1, "
        "review_reason=COALESCE(review_reason||' | '||?, ?) WHERE id=?",
        (reason, reason, triple_id),
    )
    conn.commit()
    conn.close()


def _conflict(a: dict, b: dict) -> bool:
    """Same entity pair with the opposite relation, or the same relation with the opposite negation."""
    if (a.get("subject_id"), a.get("object_id")) != (b.get("subject_id"), b.get("object_id")):
        return False
    try:
        ra, rb = RelationType(a.get("relation", "")), RelationType(b.get("relation", ""))
    except ValueError:
        return False
    same_neg = bool(a.get("negated")) == bool(b.get("negated"))
    return (ra == rb and not same_neg) or (OPPOSING_RELATIONS.get(ra) == rb and same_neg)


def prefer_text_over_figures(checked: list, before: list) -> list:
    """The paper's text outweighs a vision model's reading of a figure: a text fact whose only conflicts in
    this paper are figure facts returns to how it was; the figure facts stay contradictions."""
    out = list(checked)
    for i, r in enumerate(checked):
        if not r.get("is_contradiction") or r.get("section") == "figure":
            continue
        partners = [o for o in checked if o is not r and _conflict(r, o)]
        if partners and all(o.get("section") == "figure" for o in partners):
            out[i] = before[i]
            for j, o in enumerate(checked):
                if o in partners and not o.get("is_contradiction"):
                    out[j] = {**o, "is_contradiction": True, "flagged_for_review": True,
                              "review_reason": (o.get("review_reason", "") + " | CONTRADICTION: conflicts with "
                                                "the paper's text").strip(" |")}
    return out
