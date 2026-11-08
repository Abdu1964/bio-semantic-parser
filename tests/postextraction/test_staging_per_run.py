"""Parallel runs write to their own staging DB."""
import sqlite3
import threading

from src.postextraction import deduplication, staging


def _rec(doc, n):
    return {"subject_name": f"{doc}-s{n}", "subject_id": f"X:{doc}{n}", "relation": "regulates",
            "object_name": f"{doc}-o{n}", "object_id": f"Y:{doc}{n}", "confidence": 0.9, "document_id": doc}


def test_parallel_runs_do_not_mix(tmp_path):
    barrier = threading.Barrier(3)

    def run(doc):
        staging.use(tmp_path / f"{doc}.db")
        barrier.wait()                      # all runs set their path before any writes
        for n in range(20):
            deduplication.deduplicate(_rec(doc, n))
        staging.use("")

    threads = [threading.Thread(target=run, args=(d,)) for d in ("A", "B", "C")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    for doc in ("A", "B", "C"):
        names = {r[0] for r in sqlite3.connect(tmp_path / f"{doc}.db").execute("select subject_name from triples")}
        assert len(names) == 20 and all(n.startswith(doc + "-") for n in names)


def test_new_thread_has_no_staging_path(tmp_path, monkeypatch):
    monkeypatch.delenv("TRIPLE_STORE_PATH", raising=False)
    staging.use(tmp_path / "x.db")
    seen = []
    t = threading.Thread(target=lambda: seen.append(staging.path("main.db")))
    t.start(); t.join()
    staging.use("")
    assert seen == ["main.db"]


def test_approval_creates_full_schema(tmp_path):
    from src.publish.human_review import approve_records
    db = tmp_path / "new_both.db"
    rec = {**_rec("A", 1), "document_id": "A"}
    assert approve_records([rec], str(db), tmp_path / "q.jsonl") == 1
    cols = {r[1] for r in sqlite3.connect(db).execute("pragma table_info(triples)")}
    assert {"review_reason", "provenance", "subject_needs_review"} <= cols


def test_approval_export_never_shrinks_the_kg(tmp_path):
    from src.publish.output_layer import export_csvs_from_db
    run = tmp_path / "run"; (run / "neo4j" / "gene").mkdir(parents=True)
    edges = run / "neo4j" / "gene" / "edges_gene_regulates_gene.csv"
    edges.write_text("h\n" + "\n".join(f"row{i}" for i in range(50)) + "\n")
    db = tmp_path / "s.db"
    staging.use(db)
    deduplication.deduplicate(_rec("A", 1))                 # staging holds 1 fact, the run has 50
    staging.use("")
    export_csvs_from_db(str(db), str(run), formats="both", allow_shrink=False)
    assert len(edges.read_text().splitlines()) == 51
