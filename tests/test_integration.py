import os
import pytest
from unittest.mock import MagicMock, patch

from src.registry.registry import SourceRegistry
from src.scheduler.scheduler import Scheduler
from src.fetcher.fetcher import Fetcher
from src.preextraction.preextractor import Preextractor
from src.schema.instructor_retry import extract_batch
from src.schema.pydantic_model import BiologicalRelation, ExtractionResult
from src.schema.taxonomy import EntityType, RelationType


@pytest.fixture
def mock_openai_client():
    with patch("src.schema.instructor_retry._client") as mock_client:
        mock_completion = MagicMock()
        mock_completion.chat.completions.create.return_value = ExtractionResult(
            relations=[
                BiologicalRelation(
                    extraction_viable=True,
                    subject_name="SIRT1",
                    subject_type=EntityType.GENE,
                    relation=RelationType.UPREGULATES,
                    object_name="FOXO3",
                    object_type=EntityType.GENE,
                    confidence=0.9,
                    reasoning="SIRT1 upregulates FOXO3 expression directly as explicitly stated in the text. This is a very clear finding in the given chunk.",
                )
            ]
        )
        mock_client.return_value = mock_completion
        yield mock_client


def test_full_pipeline_integration(tmp_path, mock_openai_client):
    # Setup Registry with a dummy file source
    config_path = tmp_path / "sources.yaml"
    watch_dir = tmp_path / "inbox"
    watch_dir.mkdir()
    
    # Create a dummy PDF file
    dummy_pdf = watch_dir / "paper.pdf"
    dummy_pdf.write_text("dummy")

    config_path.write_text(f"""
sources:
  - name: test_local
    type: file
    format: pdf
    watch_dir: {str(watch_dir)}
""")
    registry = SourceRegistry(str(config_path))

    # Setup Scheduler
    db_path = tmp_path / "scheduler.db"
    scheduler = Scheduler(registry, str(db_path))

    # Setup Fetcher and Mock its coref logic if needed
    fetcher = Fetcher(coref_url="http://202.181.159.222:8081")
    # Patch coref client to just return the text
    fetcher.coref_client.health_check = MagicMock(return_value=False)
    
    # capture the fetched chunks
    captured_chunks = []
    original_fetch = fetcher.fetch

    def mock_fetch(url, source, doc_id, verbose=False):
        chunks = original_fetch(url, source, doc_id, verbose)
        captured_chunks.extend(chunks)
        return chunks

    fetcher.fetch = mock_fetch

    # Run scheduler (Layer 2 & 3)
    results = scheduler.run(fetcher)
    assert results["processed"] == 1
    assert len(captured_chunks) > 0

    # 4. Run Preextractor (Layer 4)
    preextractor = Preextractor()
    # Mock NLI and PubTator to avoid heavy model loading
    preextractor.negation_detector.process = MagicMock(return_value={
        "entities": [],
        "has_negation": False,
        "negated_entities": []
    })
    preextractor._run_ensemble = MagicMock()
    
    # Mock NERTagger to return some dummy entities
    with patch("src.preextraction.preextractor.NERTagger.from_doc", return_value=[
        {"text": "SIRT1", "label": "GENE"},
        {"text": "FOXO3", "label": "GENE"}
    ]):
        preextracted_chunks = preextractor.process_batch(captured_chunks)

    assert len(preextracted_chunks) == len(captured_chunks)
    assert "entities" in preextracted_chunks[0]

    # Run Schema Extraction (Layer 5)
    extracted_results = extract_batch(preextracted_chunks)

    assert len(extracted_results) == len(preextracted_chunks)
    first_result = extracted_results[0]
    assert isinstance(first_result, ExtractionResult)
    assert len(first_result.relations) == 1
    assert first_result.relations[0].subject_name == "SIRT1"
    assert first_result.relations[0].relation == RelationType.UPREGULATES
