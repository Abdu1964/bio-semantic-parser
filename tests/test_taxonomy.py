import pytest
from src.schema.taxonomy import (
    RelationType,
    EntityType,
    SectionLabel,
    TAXONOMY,
    CROSS_SCHEMA_MAP,
    BIOLINK_MAPPING,
    OPPOSING_RELATIONS,
)


class TestRelationType:
    def test_all_values_are_lowercase(self):
        for member in RelationType:
            assert member.value == member.value.lower()

    def test_all_values_are_unique(self):
        values = [m.value for m in RelationType]
        assert len(values) == len(set(values))

    def test_str_roundtrip(self):
        for member in RelationType:
            assert RelationType(member.value) == member

    def test_activates_and_inhibits_are_distinct(self):
        assert RelationType.ACTIVATES != RelationType.INHIBITS

    def test_upregulates_and_downregulates_are_distinct(self):
        assert RelationType.UPREGULATES != RelationType.DOWNREGULATES


class TestEntityType:
    def test_all_values_are_uppercase(self):
        for member in EntityType:
            assert member.value == member.value.upper()

    def test_all_values_are_unique(self):
        values = [m.value for m in EntityType]
        assert len(values) == len(set(values))

    def test_str_roundtrip(self):
        for member in EntityType:
            assert EntityType(member.value) == member

    def test_gene_and_protein_exist(self):
        assert EntityType.GENE.value == "GENE"
        assert EntityType.PROTEIN.value == "PROTEIN"

    def test_other_is_catch_all(self):
        assert EntityType.OTHER.value == "OTHER"


class TestSectionLabel:
    def test_standard_sections_exist(self):
        assert SectionLabel.ABSTRACT.value == "abstract"
        assert SectionLabel.INTRODUCTION.value == "introduction"
        assert SectionLabel.METHODS.value == "methods"
        assert SectionLabel.RESULTS.value == "results"
        assert SectionLabel.DISCUSSION.value == "discussion"

    def test_supplementary_and_unknown_exist(self):
        assert SectionLabel.SUPPLEMENTARY.value == "supplementary"
        assert SectionLabel.UNKNOWN.value == "unknown"

    def test_str_roundtrip(self):
        for member in SectionLabel:
            assert SectionLabel(member.value) == member


class TestTaxonomyDict:
    def test_all_relation_types_have_taxonomy_entry(self):
        for rel in RelationType:
            assert rel in TAXONOMY, f"Missing taxonomy entry for {rel}"

    def test_every_entry_has_required_keys(self):
        for rel, entry in TAXONOMY.items():
            for key in ("definition", "example", "not_this"):
                assert key in entry, f"Missing key '{key}' in taxonomy entry for {rel}"

    def test_every_definition_is_non_empty(self):
        for rel, entry in TAXONOMY.items():
            assert entry["definition"].strip(), f"Empty definition for {rel}"

    def test_every_example_is_non_empty(self):
        for rel, entry in TAXONOMY.items():
            assert entry["example"].strip(), f"Empty example for {rel}"

    def test_every_not_this_is_non_empty(self):
        for rel, entry in TAXONOMY.items():
            assert entry["not_this"].strip(), f"Empty not_this for {rel}"


class TestCrossSchemaMap:
    def test_all_relation_types_have_cross_schema_entry(self):
        for rel in RelationType:
            assert rel in CROSS_SCHEMA_MAP, f"Missing cross-schema entry for {rel}"

    def test_every_entry_is_dict(self):
        for rel, mapping in CROSS_SCHEMA_MAP.items():
            assert isinstance(mapping, dict)

    def test_bionlp_field_present(self):
        for rel in RelationType:
            assert "bionlp" in CROSS_SCHEMA_MAP[rel]

    def test_activates_has_bionlp_entry(self):
        entry = CROSS_SCHEMA_MAP[RelationType.ACTIVATES]
        assert entry.get("bionlp") is not None


class TestBiolinkMapping:
    def test_all_relation_types_have_biolink_mapping(self):
        for rel in RelationType:
            assert rel in BIOLINK_MAPPING, f"Missing biolink mapping for {rel}"

    def test_all_mappings_start_with_biolink_prefix(self):
        for rel, mapping in BIOLINK_MAPPING.items():
            assert mapping.startswith("biolink:"), f"Bad prefix for {rel}: {mapping}"

    def test_activates_maps_to_positive_regulation(self):
        assert BIOLINK_MAPPING[RelationType.ACTIVATES] == "biolink:positively_regulates"

    def test_inhibits_maps_to_negative_regulation(self):
        assert BIOLINK_MAPPING[RelationType.INHIBITS] == "biolink:negatively_regulates"

    def test_causes_maps_to_causes(self):
        assert BIOLINK_MAPPING[RelationType.CAUSES] == "biolink:causes"

    def test_treats_maps_to_treats(self):
        assert BIOLINK_MAPPING[RelationType.TREATS] == "biolink:treats"


class TestOpposingRelations:
    def test_relation_in_opposing_map(self):
        for rel in OPPOSING_RELATIONS:
            assert rel in RelationType

    def test_opposing_is_symmetric(self):
        for rel, opp in OPPOSING_RELATIONS.items():
            assert opp in OPPOSING_RELATIONS
            assert OPPOSING_RELATIONS[opp] == rel, f"Not symmetric: {rel} ↔ {opp}"

    def test_activating_opposes_inhibiting(self):
        assert OPPOSING_RELATIONS[RelationType.ACTIVATES] == RelationType.INHIBITS
        assert OPPOSING_RELATIONS[RelationType.INHIBITS] == RelationType.ACTIVATES

    def test_upregulates_opposes_downregulates(self):
        assert OPPOSING_RELATIONS[RelationType.UPREGULATES] == RelationType.DOWNREGULATES
        assert OPPOSING_RELATIONS[RelationType.DOWNREGULATES] == RelationType.UPREGULATES

    def test_ptm_pairs_are_symmetric(self):
        pairs = [
            (RelationType.PHOSPHORYLATES, RelationType.DEPHOSPHORYLATES),
            (RelationType.METHYLATES, RelationType.DEMETHYLATES),
            (RelationType.ACETYLATES, RelationType.DEACETYLATES),
            (RelationType.UBIQUITINATES, RelationType.DEUBIQUITINATES),
            (RelationType.SUMOYLATES, RelationType.DESUMOYLATES),
            (RelationType.HYDROXYLATES, RelationType.DEHYDROXYLATES),
            (RelationType.GLYCOSYLATES, RelationType.DEGLYCOSYLATES),
            (RelationType.NEDDYLATES, RelationType.DENEDDYLATES),
        ]
        for a, b in pairs:
            assert OPPOSING_RELATIONS[a] == b
            assert OPPOSING_RELATIONS[b] == a

    def test_lifespan_pairs_are_symmetric(self):
        assert OPPOSING_RELATIONS[RelationType.EXTENDS_LIFESPAN] == RelationType.REDUCES_LIFESPAN
        assert OPPOSING_RELATIONS[RelationType.REDUCES_LIFESPAN] == RelationType.EXTENDS_LIFESPAN

    def test_treats_opposes_causes(self):
        assert OPPOSING_RELATIONS[RelationType.TREATS] == RelationType.CAUSES
        assert OPPOSING_RELATIONS[RelationType.CAUSES] == RelationType.TREATS

    def test_no_relation_opposes_itself(self):
        for rel, opp in OPPOSING_RELATIONS.items():
            assert rel != opp, f"{rel} should not oppose itself"
