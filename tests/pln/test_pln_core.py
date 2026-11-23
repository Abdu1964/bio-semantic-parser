"""PLN over papers: entities by ID, truth values from the pipeline, question forms, and inference both ways."""
import importlib.util
import sqlite3

import pytest

from pln.bridge.metta_adapter import MeTTaAdapter
from pln.query.nl_to_pln import plan_query, question_forms, suggest_questions

needs_petta = pytest.mark.skipif(importlib.util.find_spec("pettachainer") is None, reason="PeTTaChainer not installed")


def _paper(root, name, doc, facts, nodes):
    """A run folder like Layer 8 writes: MeTTa edges and nodes, and Neo4j edge CSVs with each fact's score."""
    run = root / name
    (run / "metta" / "disease").mkdir(parents=True)
    (run / "neo4j" / "disease").mkdir(parents=True)
    edges, rows = [], ["source_id|target_id|relation|confidence"]
    for rel, s, o, score in facts:
        t = f"({rel} (disease {s}) (disease {o}))"
        edges += [t, f"(negated {t} False)", f"(source {t} {doc})"]
        rows.append(f"{s.replace('_', ':', 1)}|{o.replace('_', ':', 1)}|{rel}|{score}")
    (run / "metta" / "disease" / f"edges_disease_{facts[0][0]}_disease.metta").write_text("\n".join(edges) + "\n")
    (run / "neo4j" / "disease" / f"edges_{facts[0][0]}.csv").write_text("\n".join(rows) + "\n")
    (run / "metta" / "disease" / "nodes_disease.metta").write_text("\n".join(
        f'(name (disease {i}) "{label}")\n(synonyms (disease {i}) "{syn}")' for i, label, syn in nodes) + "\n")
    return run


@pytest.fixture
def papers(tmp_path):
    p1 = _paper(tmp_path, "2026-01-01_00-00-00_P1", "P1", [("causes", "MESH_D1", "MESH_D2", 0.9)],
                [("MESH_D1", "Hypertension", "High blood pressure"), ("MESH_D2", "Heart disease", "Cardiac disease")])
    p2 = _paper(tmp_path, "2026-01-02_00-00-00_P2", "P2", [("causes", "MESH_D2", "MESH_D3", 0.8)],
                [("MESH_D2", "Heart diseases", ""), ("MESH_D3", "Heart failure", "")])
    return p1, p2


def test_same_id_is_one_entity_across_papers(papers):
    ad = MeTTaAdapter()
    atoms = ad.load_run_dir(papers[0]) + ad.load_run_dir(papers[1])
    assert "mesh_d2" in atoms[0] and "mesh_d2" in atoms[1]          # 'Heart disease' and 'Heart diseases'
    assert set(ad.entities) == {"mesh_d1", "mesh_d2", "mesh_d3"}


def test_strength_comes_from_the_pipeline_score(papers):
    ad = MeTTaAdapter()
    atom = ad.load_run_dir(papers[0], ad.csv_truth(papers[0]))[0]
    assert atom.endswith("(STV 0.900 0.500))")


def test_store_evidence_confidence_is_read(tmp_path):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.execute("create table triples (relation, subject_id, object_id, confidence, source_papers, evidence_confidence, "
              "flagged_for_review default 0, negated default 0)")
    c.execute("insert into triples values ('causes', 'MESH:D1', 'MESH:D2', 0.9, '[\"P1\",\"P2\"]', 0.6667, 0, 0)")
    c.commit()
    assert MeTTaAdapter().db_truth(db) == {("causes", "mesh_d1", "mesh_d2"): (0.9, 0.6667)}


def test_malformed_predicates_are_skipped(tmp_path):
    f = tmp_path / "edges_x.metta"
    f.write_text("(: odd (Causes a b) (STV 1 1))\n(causes (disease A) (disease B))\n")
    atoms = MeTTaAdapter()._parse_file(f)
    assert len(atoms) == 1 and "(Causes a b)" in atoms[0]


def test_question_forms_come_from_relation_words():
    assert question_forms("Causes") == ("What does {s} cause?", "What causes {o}?")
    fwd, back = question_forms("BindsTo")
    assert fwd == "What does {s} bind to?" and back == "What binds to {o}?"


def test_names_and_synonyms_resolve_to_the_entity():
    names = {"mesh_d1": {"Hypertension", "high blood pressure"}, "mesh_d2": {"Heart disease"}}
    q, _, _ = plan_query("What does high blood pressure cause?", names, ["Causes"])
    assert q == "(: $prf (Causes mesh_d1 $target) $tv)"
    q, filters, _ = plan_query("What causes heart?", {**names, "mesh_d3": {"Heart failure"}}, ["Causes"])
    assert q == "(: $prf (Causes $source $object) $tv)" and filters == {"object": {"mesh_d2", "mesh_d3"}}


def test_suggestions_are_real_questions_from_facts():
    out = suggest_questions([("Causes", "mesh_d1", "mesh_d2")], {"mesh_d1": {"Hypertension"}}, limit=3)
    assert out["questions"][0]["question"] == "What does Hypertension cause?"


@needs_petta
def test_inference_both_ways_across_papers(papers, monkeypatch):
    monkeypatch.setenv("ENABLE_PLN", "true")
    from pln import PLNModule
    pln = PLNModule.from_env()
    for p in papers:
        pln.load_paper(run_dir=p)

    forward = pln.inferred()                                        # forward: everything the rules imply
    assert any("(Causes mesh_d1 mesh_d3)" in a for a in forward)
    s, c = (float(x) for x in forward[0].rsplit("(STV", 1)[1].rstrip(")").split())
    assert (s, c) != (0.5, 0.5)                                     # computed by PeTTaChainer from the premises

    result = pln.ask("What does hypertension cause?")               # backward: proved for the question
    inferred = [a for a in result.answers if a["inferred"]]
    assert inferred and inferred[0]["object"] == "mesh_d3"
    assert set(inferred[0]["papers"]) == {p.name for p in papers}
    assert any(v["question"] == "What does hypertension cause?" for v in pln.backward_found().values())
    assert sum("(Causes mesh_d1 mesh_d3)" in a for a in pln.inferred()) == 1   # found both ways: listed once


def test_negated_facts_stay_out_and_metadata_is_read(tmp_path):
    f = tmp_path / "edges_x.metta"
    t1, t2 = "(causes (disease A) (disease B))", "(causes (disease A) (disease C))"
    f.write_text(f"{t1}\n(negated {t1} False)\n(paper_url {t1} https://example.org/p1)\n"
                 f"{t2}\n(negated {t2} True)\n")
    ad = MeTTaAdapter()
    atoms = ad._parse_file(f)
    assert len(atoms) == 1 and "(Causes a b)" in atoms[0]           # "A does not cause C" is not a fact
    assert ad._parse_file_meta(f)["causes_a_b"]["paper_url"] == "https://example.org/p1"
