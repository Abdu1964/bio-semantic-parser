"""Figure facts go in without review only when the paper's text states the same fact."""
from src.publish.validation_gate import route


def _fact(rel, obj_id="ENSEMBL:ENSG00000136244", section="results", negated=False, **kw):
    return {"subject_id": "mesh:C000713949", "subject_name": "RTD-1", "subject_type": "PROTEIN",
            "relation": rel, "object_id": obj_id, "object_name": kw.pop("object_name", "IL6"), "object_type": "GENE",
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


def test_other_species_gene_neither_confirms_nor_contradicts():
    rat_il6 = _fact("upregulates", obj_id="ENSEMBL:ENSRNOG00000010278", species="Rattus norvegicus")
    auto, review = route([rat_il6, _figure_fact("downregulates", species="Homo sapiens")])
    fig = next(r for r in auto if r["section"] == "figure")
    assert fig["provenance"]["confirmed_by_text"] is None and not review


def test_negation_must_match():
    auto, review = route([_fact("downregulates", negated=True), _figure_fact("downregulates")])
    assert [r["section"] for r in review] == ["figure"]


def test_valid_figure_fact_without_text_is_inserted():
    auto, review = route([_figure_fact("downregulates")])
    assert not review and auto[0]["provenance"]["figure"]["label"] == "Figure 6"


def test_uncorroborated_mode_still_available(monkeypatch):
    monkeypatch.setenv("FIGURE_FACTS_REVIEW", "uncorroborated")
    auto, review = route([_figure_fact("downregulates")])
    assert not auto and review[0]["section"] == "figure"


def test_gene_and_protein_ids_of_one_molecule_match_in_same_species():
    text = _fact("upregulates", obj_id="UniProtKB:P80098", species="Homo sapiens",
                 object_name="CCL7", object_canonical_name="C-C motif chemokine 7", object_synonyms="CCL7; MCP3")
    fig = _figure_fact("upregulates", obj_id="ENSEMBL:ENSG00000108688", species="Homo sapiens",
                       object_name="CCL7", object_canonical_name="CCL7")
    auto, review = route([text, fig])
    assert not review


def test_same_name_other_species_is_not_a_contradiction():
    text = _fact("downregulates", obj_id="UniProtKB:Q9QXY8", species="Rattus norvegicus", object_name="CCL7")
    fig = _figure_fact("upregulates", obj_id="ENSEMBL:ENSG00000108688", species="Homo sapiens", object_name="CCL7")
    auto, review = route([text, fig])
    assert not review
