"""Figure facts go in without review only when the paper's text states the same fact."""
from src.publish.validation_gate import route


def _fact(rel, obj_id="ENSEMBL:ENSG00000136244", section="results", negated=False, **kw):
    return {"subject_id": "mesh:C000713949", "subject_name": "RTD-1", "subject_type": "PROTEIN",
            "relation": rel, "object_id": obj_id, "object_name": "IL6", "object_type": "GENE",
            "negated": negated, "validation_verdict": "VALID", "section": section, **kw}


def _figure_fact(rel, **kw):
    return _fact(rel, section="figure", figure={"label": "Figure 6", "page": 7, "file": "f6.png"}, **kw)


def test_figure_fact_confirmed_by_text_is_inserted_with_provenance():
    auto, review = route([_fact("inhibits"), _figure_fact("downregulates")])   # same BioNLP event type
    fig = next(r for r in auto if r["section"] == "figure")
    assert fig["provenance"]["figure"]["label"] == "Figure 6"
    assert fig["provenance"]["confirmed_by_text"]["relation"] == "inhibits"
    assert not review


def test_opposite_direction_goes_to_review():
    auto, review = route([_fact("activates"), _figure_fact("inhibits")])
    assert [r["section"] for r in review] == ["figure"]


def test_other_species_gene_is_not_confirmation():
    rat_il6 = _fact("downregulates", obj_id="ENSEMBL:ENSRNOG00000010278")
    auto, review = route([rat_il6, _figure_fact("downregulates")])
    assert [r["section"] for r in review] == ["figure"]


def test_negation_must_match():
    auto, review = route([_fact("downregulates", negated=True), _figure_fact("downregulates")])
    assert [r["section"] for r in review] == ["figure"]


def test_figure_fact_without_text_goes_to_review():
    auto, review = route([_figure_fact("downregulates")])
    assert not auto and review[0]["section"] == "figure"
