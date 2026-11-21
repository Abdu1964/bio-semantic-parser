"""Each triple keeps its 5-channel score and counts the sources that state it."""
import json
import sqlite3

from src.postextraction import deduplication, staging


def _rec(doc, conf):
    return {"subject_name": "a", "subject_id": "X:1", "relation": "regulates", "object_name": "b",
            "object_id": "Y:1", "confidence": conf, "document_id": doc}


def _row(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    return dict(c.execute("select confidence, evidence_count, evidence_confidence from triples").fetchone())


def test_same_paper_again_keeps_score_and_count(tmp_path):
    staging.use(tmp_path / "s.db")
    deduplication.deduplicate(_rec("P1", 0.8))
    deduplication.deduplicate(_rec("P1", 0.7))              # a second mention in the same paper
    staging.use("")
    assert _row(tmp_path / "s.db") == {"confidence": 0.8, "evidence_count": 1, "evidence_confidence": 0.5}


def test_new_source_revises_score_and_raises_evidence(tmp_path):
    staging.use(tmp_path / "s.db")
    deduplication.deduplicate(_rec("P1", 0.8))
    deduplication.deduplicate(_rec("P2", 0.6))
    deduplication.deduplicate(_rec("P3", 0.7))
    staging.use("")
    assert _row(tmp_path / "s.db") == {"confidence": 0.7, "evidence_count": 3, "evidence_confidence": 0.75}


def test_commit_counts_papers_across_stores(tmp_path, monkeypatch):
    stores = {f: tmp_path / f"main_{f}.db" for f in ("neo4j", "metta")}
    monkeypatch.setattr(deduplication, "db_path_for_format", lambda f: stores[f])
    monkeypatch.setattr(deduplication, "_DB_PATH", stores["neo4j"])
    for doc, conf in (("P1", 0.9), ("P2", 0.5)):
        staging.use(tmp_path / f"{doc}.db")
        deduplication.deduplicate(_rec(doc, conf))
        staging.use("")
        deduplication.commit_staging_to_main(str(tmp_path / f"{doc}.db"), formats="both")
    for db in stores.values():
        assert _row(db) == {"confidence": 0.7, "evidence_count": 2, "evidence_confidence": 0.6667}


def test_old_store_gets_counts_from_its_sources(tmp_path):
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("create table triples (id integer primary key, subject_id text, relation text, object_id text, "
              "negated integer, confidence real, source_papers text)")
    c.execute("insert into triples values (1, 'X:1', 'r', 'Y:1', 0, 0.8, ?)", (json.dumps(["A", "B", "C"]),))
    c.commit()
    deduplication._ensure_schema(c)
    assert c.execute("select evidence_count, evidence_confidence from triples").fetchone() == (3, 0.75)
