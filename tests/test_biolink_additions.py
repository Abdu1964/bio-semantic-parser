"""New relations and edge qualifiers exist in the local Biolink model (nothing outside Biolink)."""
import os
from pathlib import Path

import pytest
import yaml

from src.schema.biolink_qualifiers import _ASPECT_DIRECTION, qualifiers
from src.schema.taxonomy import BIOLINK_MAPPING, OPPOSING_RELATIONS, TAXONOMY, RelationType

_CANDIDATES = [os.getenv("BIOLINK_YAML", ""), "src/schema/data/biolink-model.yaml", "tools/.schema_cache/biolink_model_v4.4.2.yaml"]
_PATH = next((Path(p) for p in _CANDIDATES if p and Path(p).exists()), None)
_NEW = ["has_increased_amount", "has_decreased_amount", "positively_correlated_with", "negatively_correlated_with",
        "associated_with_increased_likelihood_of", "associated_with_decreased_likelihood_of", "disrupts",
        "acts_upstream_of_positive_effect", "acts_upstream_of_negative_effect", "has_member", "has_nearby_variant",
        "actively_involved_in"]


def test_new_relations_are_complete():
    for name in _NEW:
        rel = RelationType(name)
        assert rel in TAXONOMY and TAXONOMY[rel]["definition"] and BIOLINK_MAPPING[rel] == f"biolink:{name}"
    assert OPPOSING_RELATIONS[RelationType.HAS_INCREASED_AMOUNT] == RelationType.HAS_DECREASED_AMOUNT


@pytest.mark.skipif(_PATH is None, reason="no local Biolink model file")
def test_everything_comes_from_biolink():
    m = yaml.safe_load(_PATH.read_text())
    slots, enums = m["slots"], m["enums"]
    for name in _NEW:
        assert name.replace("_", " ") in slots, name
    allowed = lambda e: set(enums[e]["permissible_values"])
    levels = {qualifiers({"relation": r, "evidence": e})["knowledge_level"] for r in ("associates_with", "upregulates") for e in ("measured", "predicted")}
    assert levels <= allowed("KnowledgeLevelEnum")
    assert {"image_processing_agent", "text_mining_agent"} <= allowed("AgentTypeEnum")
    assert {a for a, _ in _ASPECT_DIRECTION.values()} <= allowed("GeneOrGeneProductOrChemicalEntityAspectEnum")
    assert {d for _, d in _ASPECT_DIRECTION.values() if d} <= allowed("DirectionQualifierEnum")


def test_inverse_predicates_become_canonical():
    from src.extraction.extractor import _valid_relations
    out = _valid_relations([{"extraction_viable": True, "subject_name": "IL6", "subject_type": "GENE", "relation": "regulated by",
                             "object_name": "RTD-1", "object_type": "SMALL_MOLECULE",
                             "reasoning": "the text states that IL6 is regulated by RTD-1 treatment in this model"}])
    assert (out[0].subject_name, out[0].relation.value, out[0].object_name) == ("RTD-1", "regulates", "IL6")


def test_every_biolink_predicate_is_accounted_for():
    from src.schema.biolink_generated import COVERAGE
    assert COVERAGE and all(v == "direct" or v == "added" or v.startswith(("inverse of ", "not applicable")) for v in COVERAGE.values())
