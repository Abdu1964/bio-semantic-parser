import pytest
from unittest.mock import MagicMock, patch

from src.schema.pydantic_model import BiologicalRelation, ExtractionResult
from src.schema.taxonomy import EntityType, RelationType
from src.postextraction import postextractor

@pytest.fixture
def dummy_extraction_pairs():
    rel = BiologicalRelation(
        extraction_viable=True,
        subject_name="SIRT1",
        subject_type=EntityType.GENE,
        relation=RelationType.UPREGULATES,
        object_name="FOXO3",
        object_type=EntityType.GENE,
        confidence=0.9,
        reasoning="SIRT1 upregulates FOXO3. This reasoning string needs to be at least fifty characters long to pass validation.",
    )
    res = ExtractionResult(relations=[rel])
    chunk = {
        "document_id": "doc_123",
        "text": "SIRT1 upregulates FOXO3 in the liver.",
        "section": "results",
        "chunk_index": 0,
    }
    return [(res, chunk)]


def test_postextractor_empty_input():
    assert postextractor.process_batch([], []) == []


@patch("src.postextraction.entity_normalization.normalize_record")
@patch("src.postextraction.deduplication.deduplicate")
@patch("src.postextraction.contradiction_detection.check_contradiction")
@patch("src.postextraction.cross_chunk_linking.link_within_paper")
@patch("src.postextraction.two_pass_resolution.resolve")
@patch("src.postextraction.semantic_validation.validate_batch")
@patch("src.postextraction.atomspace_alignment.align")
@patch("src.postextraction.confidence_scorer.explain")
def test_postextractor_pipeline_flow(
    mock_explain,
    mock_align,
    mock_validate_batch,
    mock_resolve,
    mock_link,
    mock_contradiction,
    mock_dedup,
    mock_norm,
    dummy_extraction_pairs
):
    # Setup mocks to return unmodified records mostly
    def identity(r, *args, **kwargs): return r
    mock_norm.side_effect = lambda r, c: {**r, "subject_id": "NCBI_GENE:1", "object_id": "NCBI_GENE:2"}
    mock_dedup.side_effect = identity
    mock_contradiction.side_effect = identity
    mock_link.side_effect = lambda records: records
    mock_resolve.side_effect = lambda records, text_dict: records
    mock_validate_batch.side_effect = lambda batch: [{**r, "validation_verdict": "VALID"} for r, _ in batch]
    mock_align.side_effect = lambda records: records
    mock_explain.return_value = {"final_score": 0.8, "channels": {}}

    # Run orchestrator
    res, chunks = zip(*dummy_extraction_pairs)
    records = postextractor.process_batch(list(res), list(chunks))

    assert len(records) == 1
    rec = records[0]
    
    # Assert values
    assert rec["subject_name"] == "SIRT1"
    assert rec["relation"] == RelationType.UPREGULATES
    assert rec["object_name"] == "FOXO3"
    assert rec["validation_verdict"] == "VALID"

    # Assert mocks were called
    assert mock_norm.called
    assert mock_dedup.called
    assert mock_contradiction.called
    assert mock_link.called
    # resolve is not called if there are no NEEDS_REVIEW ids
    assert mock_validate_batch.called
    assert mock_align.called


def test_flag_non_biomedical_entities(dummy_extraction_pairs):
    # If both subject and object have no ontology ID, it should be flagged
    with patch("src.postextraction.entity_normalization.normalize_record") as mock_norm, \
         patch("src.postextraction.deduplication.deduplicate", side_effect=lambda r: r), \
         patch("src.postextraction.contradiction_detection.check_contradiction", side_effect=lambda r: r), \
         patch("src.postextraction.cross_chunk_linking.link_within_paper", side_effect=lambda rs: rs), \
         patch("src.postextraction.two_pass_resolution.resolve", side_effect=lambda rs, t: rs), \
         patch("src.postextraction.semantic_validation.validate_batch", side_effect=lambda b: [{**r, "validation_verdict": "VALID"} for r, _ in b]), \
         patch("src.postextraction.atomspace_alignment.align", side_effect=lambda rs: rs), \
         patch("src.postextraction.confidence_scorer.explain", return_value={"final_score": 0.8, "channels": {}}):
        
        # Make normalize_record return NO IDs
        mock_norm.side_effect = lambda r, c: {**r, "subject_id": "TEXT:123", "object_id": "TEXT:456"}
        
        res, chunks = zip(*dummy_extraction_pairs)
        records = postextractor.process_batch(list(res), list(chunks))
        
        assert len(records) == 1
        print("Record returned:", records[0])
        assert records[0].get("flagged_for_review") is True
        assert "non-biomedical" in records[0].get("review_reason", "").lower()
