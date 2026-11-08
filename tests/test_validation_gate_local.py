"""Local IDs for unresolved names."""
from src.postextraction.local_concepts import mark
from src.publish.validation_gate import route


def _rec(subject, subject_id, obj="microglia", object_id="CL:0000129", **kw):
    return {"subject_name": subject, "subject_id": subject_id, "object_name": obj, "object_id": object_id,
            "document_id": "Alz", "validation_verdict": "VALID", **kw}


def test_mark_needs_two_mentions_and_a_real_other_side():
    recs = [_rec("M2", "TEXT:m2"), _rec("cohesin in neurons", "TEXT:x"), _rec("In", "TEXT:in"),
            _rec("M2", "TEXT:m2", obj="tissue", object_id="TEXT:y")]
    assert mark(recs, "Module M2 is in microglia. M2 rises in AD. In AD, cohesin in neurons falls.") == 1
    assert recs[0]["subject_local_id"] == "LOCAL:alz/m2"


def test_gate_inserts_only_valid_local_facts():
    ok = _rec("M2", "TEXT:m2", subject_local_id="LOCAL:alz/m2")
    bad = _rec("M2", "TEXT:m2", subject_local_id="LOCAL:alz/m2", validation_verdict="REVIEW")
    auto, review = route([ok, bad])
    assert [r["subject_id"] for r in auto] == ["LOCAL:alz/m2"] and review[0]["subject_id"] == "TEXT:m2"


def test_unresolved_name_reuses_the_papers_id():
    from src.postextraction.local_concepts import reuse_paper_ids
    recs = [{"subject_name": "Indy", "subject_type": "GENE", "subject_id": "ENSEMBL:FBgn0036816", "object_name": "x", "object_id": "Y:1"},
            {"subject_name": "indy", "subject_type": "GENE", "subject_id": "TEXT:indy", "object_name": "x", "object_id": "Y:1"},
            {"subject_name": "genes", "subject_type": "GENE", "subject_id": "TEXT:genes", "subject_non_canonical": "generic",
             "object_name": "x", "object_id": "Y:1"}]
    assert reuse_paper_ids(recs) == 1 and recs[1]["subject_id"] == "ENSEMBL:FBgn0036816" and recs[2]["subject_id"] == "TEXT:genes"
