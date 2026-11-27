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
