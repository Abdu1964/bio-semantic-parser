"""A text fact is not held back by a conflicting figure reading; the figure fact is."""
from src.postextraction.contradiction_detection import prefer_text_over_figures


def _fact(relation, section, flagged=False):
    return {"subject_id": "mesh:C1", "object_id": "ENSEMBL:ENSRNOG1", "relation": relation, "negated": False,
            "section": section, "is_contradiction": flagged, "flagged_for_review": flagged}


def test_text_wins_over_figure():
    before = [_fact("activates", "results"), _fact("inhibits", "figure")]
    checked = [_fact("activates", "results", True), _fact("inhibits", "figure", True)]
    out = prefer_text_over_figures(checked, before)
    assert not out[0]["is_contradiction"] and out[1]["is_contradiction"]


def test_two_text_facts_stay_contradictions():
    before = [_fact("activates", "results"), _fact("inhibits", "discussion")]
    checked = [_fact("activates", "results", True), _fact("inhibits", "discussion", True)]
    out = prefer_text_over_figures(checked, before)
    assert out[0]["is_contradiction"] and out[1]["is_contradiction"]


def test_subtypes_sharing_a_parent_id_do_not_conflict():
    from src.postextraction.contradiction_detection import _conflict, _subgroups
    a = {**_fact("activates", "results"), "subject_name": "AD", "object_name": "Ast TPST1"}
    b = {**_fact("inhibits", "results"), "subject_name": "AD", "object_name": "Ast DPP10"}
    assert not _conflict(a, b)
    assert _conflict(a, {**b, "object_name": "Ast TPST1"})
    assert not _subgroups("IL-6", "interleukin 6") and not _subgroups("TNF", "TNF-alpha")


def test_relation_targets_follow_taxonomy_ranges():
    from src.postextraction.postextractor import fit_relation_targets
    recs = [{"relation": "upregulates", "object_type": "DISEASE"},
            {"relation": "downregulates", "object_type": "TISSUE"},
            {"relation": "promotes", "object_type": "GENE"},
            {"relation": "upregulates", "object_type": "GENE"}]
    assert fit_relation_targets(recs) == (2, 1)
    assert [r["relation"] for r in recs] == ["promotes", "downregulates", "upregulates", "upregulates"]
    assert "invalid_reason" in recs[1]
