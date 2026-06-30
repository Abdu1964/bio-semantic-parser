import pytest
from pydantic import ValidationError
from src.schema.pydantic_model import BiologicalRelation, ExtractionResult
from src.schema.taxonomy import EntityType, RelationType


def test_biological_relation_viable():
    # Valid viable relation
    relation = BiologicalRelation(
        extraction_viable=True,
        subject_name="FOXO3",
        subject_type=EntityType.GENE,
        relation=RelationType.UPREGULATES,
        object_name="SIRT1",
        object_type=EntityType.GENE,
        confidence=0.8,
        reasoning="The text clearly states that FOXO3 upregulates SIRT1 expression in mouse models. This is a direct regulatory relationship.",
    )
    assert relation.extraction_viable is True
    assert relation.subject_name == "FOXO3"
    assert relation.relation == RelationType.UPREGULATES
    assert relation.object_name == "SIRT1"


def test_biological_relation_not_viable():
    # Valid non-viable relation
    relation = BiologicalRelation(
        extraction_viable=False,
        reasoning="The text only mentions an association without specifying any clear biological relation between two named entities.",
    )
    assert relation.extraction_viable is False
    assert relation.subject_name == ""
    assert relation.relation is None
    assert relation.object_name == ""


def test_biological_relation_missing_core_fields_when_viable():
    with pytest.raises(ValidationError) as exc_info:
        BiologicalRelation(
            extraction_viable=True,
            subject_name="",  # Missing subject
            relation=RelationType.UPREGULATES,
            object_name="SIRT1",
            confidence=0.8,
            reasoning="The text clearly states that FOXO3 upregulates SIRT1 expression in mouse models. This is a direct regulatory relationship.",
        )
    assert "required fields are empty" in str(exc_info.value)


def test_biological_relation_invalid_confidence():
    with pytest.raises(ValidationError) as exc_info:
        BiologicalRelation(
            extraction_viable=True,
            subject_name="FOXO3",
            relation=RelationType.UPREGULATES,
            object_name="SIRT1",
            confidence=1.5,  # Invalid confidence > 1.0
            reasoning="The text clearly states that FOXO3 upregulates SIRT1 expression in mouse models. This is a direct regulatory relationship.",
        )
    assert "Input should be less than or equal to 1" in str(exc_info.value)


def test_biological_relation_short_reasoning():
    with pytest.raises(ValidationError) as exc_info:
        BiologicalRelation(
            extraction_viable=False,
            reasoning="Too short",  # < 50 chars
        )
    assert "String should have at least 50 characters" in str(exc_info.value)


def test_extraction_result():
    relation = BiologicalRelation(
        extraction_viable=False,
        reasoning="The text only mentions an association without specifying any clear biological relation between two named entities.",
    )
    result = ExtractionResult(relations=[relation])
    assert len(result.relations) == 1
    assert result.relations[0].extraction_viable is False
    assert result.rejected is False
    assert result.rejection_reason is None
