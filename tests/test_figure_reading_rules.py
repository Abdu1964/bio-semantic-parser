"""Figure reading: names hold the entity only, a figure's '(source)' label convention, enrichment as association."""
from src.figures.vision.reader import (_association_if_enrichment, _clean_name, _tags_label_convention,
                                       _time_out_of_name)
from src.schema.pydantic_model import BiologicalRelation


def test_source_brackets_are_dropped_when_the_figure_labels_that_way():
    ocr = "Rheumatoid Arthritis (Disease)\nTNF-signaling (GO)\nRORC pathway (MSigDB)\nInflammatory response (DisGeNET)"
    assert _tags_label_convention(ocr)
    assert _clean_name("Inflammatory response (DisGeNET)", tagged=True) == "Inflammatory response"
    assert _clean_name("CD8 T cell (CD8+)", tagged=False) == "CD8 T cell (CD8+)"       # no convention: kept


def test_a_time_point_moves_from_the_name_to_the_condition():
    r = _time_out_of_name({"subject_name": "5 days RTD-1", "object_name": "TNF", "condition": ""})
    assert r["subject_name"] == "RTD-1" and r["condition"] == "5 days"
    r = _time_out_of_name({"subject_name": "IL-1B 24 h", "object_name": "day 3 cohort", "condition": "in vitro"})
    assert r["subject_name"] == "IL-1B" and r["object_name"] == "cohort" and r["condition"] == "in vitro; 24 h; day 3"
    assert _time_out_of_name({"subject_name": "IL6", "object_name": "TNF"})["subject_name"] == "IL6"


def _rel(relation, reasoning):
    return BiologicalRelation(subject_name="PIA", subject_type="DISEASE", relation=relation,
                              object_name="TNF signaling", object_type="BIOLOGICAL_PROCESS", confidence=0.8,
                              reasoning=reasoning, extraction_viable=True)


def test_enrichment_becomes_an_association():
    r = _association_if_enrichment(_rel("acts_upstream_of_positive_effect",
                                        "Panel D bar shows TNF-signaling is strongly enriched among PIA-altered genes"))
    assert r.relation.value == "associates_with" and r.evidence == "predicted"
    kept = _association_if_enrichment(_rel("acts_upstream_of_positive_effect",
                                           "The arrow in panel B goes from PIA to TNF signaling in the pathway"))
    assert kept.relation.value == "acts_upstream_of_positive_effect"
    already = _association_if_enrichment(_rel("associates_with", "Panel B shows cell adhesion regulation genes are enriched after 5 days of RTD-1"))
    assert already.relation.value == "associates_with" and already.evidence == "predicted"


def test_gene_sets_signatures_and_scores_are_not_entities():
    from src.figures.vision.reader import _not_an_entity
    for name in ("AD-GWAS", "aQTL", "AD-DEG-linked-peaks", "erosion DEGs", "core cCRE signature", "erosion score"):
        assert _not_an_entity(name), name
    assert not _not_an_entity("Gleason score", "CLINICAL_MEASUREMENT")       # a clinical measure is an entity
    assert not _not_an_entity("STAT3", "GENE")


def _typed(subject_type, relation, reasoning, object_type="GENE"):
    return BiologicalRelation(subject_name="early AD", subject_type=subject_type, relation=relation,
                              object_name="MAML2", object_type=object_type, confidence=0.8,
                              reasoning=reasoning, extraction_viable=True)


def test_a_disease_or_cell_state_has_more_or_less_it_does_not_regulate():
    from src.figures.vision.reader import _amount_if_holder
    why = "The bar for early AD is lower than control for MAML2 in the differential expression panel"
    assert _amount_if_holder(_typed("DISEASE", "downregulates", why)).relation.value == "has_decreased_amount"
    assert _amount_if_holder(_typed("CELL_TYPE", "upregulates", why)).relation.value == "has_increased_amount"
    assert _amount_if_holder(_typed("SMALL_MOLECULE", "downregulates", why)).relation.value == "downregulates"   # an agent


def test_motif_and_heritability_results_are_predicted_associations():
    motif = _association_if_enrichment(_typed("GENE", "interacts_with",
        "Module M2 shows the STAT1 motif as the top hit in the motif analysis heatmap of panel B"))
    assert motif.relation.value == "associates_with" and motif.evidence == "predicted"
    ldsc = _association_if_enrichment(_typed("CELL_TYPE", "associates_with",
        "LDSC heritability for atrial fibrillation is significant in adult atrial cardiomyocytes", "DISEASE"))
    assert ldsc.evidence == "predicted"
    ipa = _association_if_enrichment(_typed("SMALL_MOLECULE", "inhibits",
        "The upstream regulator z-score for TNF is below -2 in the RTD-1 group of panel C"))
    assert ipa.relation.value == "inhibits" and ipa.evidence == "predicted"
