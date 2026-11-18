"""Layer 7 step 1b: an ID is kept, replaced or left unresolved by meaning, once per concept."""
from src.postextraction import meaning_check as M


def _rec(name, etype, cid):
    return {"subject_name": "AD", "subject_type": "DISEASE", "subject_id": "TEXT:ad", "relation": "causes",
            "object_name": name, "object_type": etype, "object_id": cid, "object_evidence": f"AD causes {name}."}


def _patch(monkeypatch, verdict):
    monkeypatch.setattr(M, "ENABLED", True)
    monkeypatch.setattr(M, "describe", lambda cid: {"label": "erosion", "definition": "removal of soil by water"})
    monkeypatch.setattr(M, "candidates", lambda name, onts: [{"id": "GO:0000001", "label": "chromatin change",
                                                              "definition": "", "synonyms": []}])
    monkeypatch.setattr(M, "judge_batch", lambda items: [verdict] * len(items))


def test_wrong_meaning_left_unresolved(monkeypatch):
    _patch(monkeypatch, ("DIFFERENT", None))
    recs = [_rec("erosion", "BIOLOGICAL_PROCESS", "ENVO:01001346"), _rec("erosion", "BIOLOGICAL_PROCESS", "ENVO:01001346")]
    st = M.run(recs, log=lambda m: None)
    assert st == {"checked": 1, "kept": 0, "replaced": 0, "unresolved": 1, "unverified": 0}   # one concept, both mentions
    assert all(r["object_id"].startswith("TEXT:") and r["object_meaning_rejected"] == "ENVO:01001346" for r in recs)


def test_better_candidate_replaces(monkeypatch):
    _patch(monkeypatch, ("DIFFERENT", 0))
    recs = [_rec("erosion", "BIOLOGICAL_PROCESS", "ENVO:01001346")]
    assert M.run(recs, log=lambda m: None)["replaced"] == 1 and recs[0]["object_id"] == "GO:0000001"


def test_gene_symbols_keep_their_lookup(monkeypatch):
    _patch(monkeypatch, ("DIFFERENT", None))
    recs = [_rec("TREM2", "GENE", "ENSEMBL:ENSG00000095970"), _rec("IRF family", "PROTEIN", "ENSEMBL:ENSG00000158022")]
    st = M.run(recs, log=lambda m: None)
    assert st["checked"] == 1 and recs[0]["object_id"] == "ENSEMBL:ENSG00000095970"
    assert recs[1]["object_id"].startswith("TEXT:")


def test_broader_without_better_candidate_is_kept(monkeypatch):
    _patch(monkeypatch, ("BROADER", None))
    recs = [_rec("excitatory neurons", "CELL_TYPE", "mesh:D009474")]
    assert M.run(recs, log=lambda m: None)["kept"] == 1 and recs[0]["object_id"] == "mesh:D009474"


def test_batch_answer_parsing(monkeypatch):
    from src.postextraction.meaning_check import judge as J
    monkeypatch.setattr(J, "call_llm", lambda *a, **k: "1: SAME\n2: DIFFERENT B\n3: broader none\n4: DIFFERENT NONE")
    item = {"name": "x", "type": "T", "role": "object", "fact": "f", "sentence": "s",
            "current": {"label": "c"}, "alts": [{"label": "a"}, {"label": "b"}]}
    assert J.judge_batch([item] * 5) == [("SAME", None), ("DIFFERENT", 1), ("BROADER", None), ("DIFFERENT", None),
                                         ("SAME", None)]


def test_unreachable_ontology_service_keeps_the_id(monkeypatch):
    _patch(monkeypatch, ("DIFFERENT", None))
    monkeypatch.setattr(M, "describe", lambda cid: None)                  # OLS could not be asked
    monkeypatch.setattr(M.time, "sleep", lambda s: None)                  # retry rounds, without waiting
    recs = [_rec("RTD-1", "SMALL_MOLECULE", "mesh:C000713949")]
    st = M.run(recs, log=lambda m: None)
    assert st["checked"] == 0 and st["unverified"] == 1 and recs[0]["object_id"] == "mesh:C000713949"


def test_unreachable_service_is_retried(monkeypatch):
    import src.postextraction.meaning_check as mc
    from src.postextraction import entity_normalization as en
    calls = {"n": 0}

    def prepare(record, side):
        calls["n"] += 1
        return None if calls["n"] == 1 else {"name": "x", "current": {"id": "GO:1"}, "alts": []}
    monkeypatch.setattr(mc, "_prepare", prepare)
    monkeypatch.setattr(mc, "_needs_check", lambda r, s: s == "subject")
    monkeypatch.setattr(mc, "judge_batch", lambda b: [("SAME", None)] * len(b))
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    monkeypatch.setattr(en, "reset_network_state", lambda: None)
    st = mc.run([{"subject_name": "x", "subject_type": "BIOLOGICAL_PROCESS", "subject_id": "GO:1"}], log=lambda *a: None)
    assert calls["n"] == 2 and st["checked"] == 1 and st["unverified"] == 0
