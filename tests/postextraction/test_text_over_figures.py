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
