"""Layer 7 step 3 — flag opposing relations for the same entity pair as contradictions."""
import sqlite3
from pathlib import Path

from src.schema.taxonomy import OPPOSING_RELATIONS, RelationType


_DB_PATH = Path("data/triple_store.db")


def _get_conn() -> sqlite3.Connection:
    import os as _os
    path = Path(_os.getenv("TRIPLE_STORE_PATH", str(_DB_PATH)))
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
    db_path = Path(_os.getenv("TRIPLE_STORE_PATH", str(_DB_PATH)))
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
    db_path = Path(_os.getenv("TRIPLE_STORE_PATH", str(_DB_PATH)))
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
