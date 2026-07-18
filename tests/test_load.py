"""
Load / throughput / soak tests.

These push the pipeline's pure-Python hot paths (no network, no ML weights) at
volumes well beyond a single paper, asserting three things load testing is meant
to catch:

  * throughput — the stage completes a large batch within a generous wall-clock
    bound (catches accidental O(N²) regressions, not micro-benchmarks);
  * correctness under volume — no records dropped, duplicated, or mis-counted
    when N is large;
  * bounded resources — caches and stores grow with *distinct* work, not with
    repeated work (no unbounded leak, no N-inflation).

Timing bounds are deliberately loose (CI runs on shared hardware); they exist to
flag catastrophic regressions, not to benchmark. conftest.py stubs tiktoken so
token == word, which keeps these deterministic and fast.
"""
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from src.fetcher.chunker import Chunker
from src.preextraction.preextractor import Preextractor
from src.preextraction.negation_detector import _CONTRASTIVE, _extract_entity_clause
from src.postextraction import confidence_scorer


# Loose ceilings — shared CI hardware. Tune only if genuinely too tight.
def _bound(seconds: float) -> float:
    return float(os.getenv("LOAD_TEST_SLACK", "1")) * seconds


# ── 1. Chunker throughput + no content loss on a large document ───────────────

class TestChunkerLoad:
    def test_large_document_chunks_within_budget(self, monkeypatch):
        chunker = Chunker()
        monkeypatch.setattr(chunker, "max_tokens", 200)
        # ~5000 sentences → a big multi-section paper.
        sentences = [f"Gene G{i} upregulates protein P{i} in tissue T{i}" for i in range(5000)]
        text = ". ".join(sentences) + "."

        start = time.perf_counter()
        chunks = chunker.chunk_section({"section": "results", "text": text})
        elapsed = time.perf_counter() - start

        assert len(chunks) > 1
        assert elapsed < _bound(5.0), f"chunking 5k sentences took {elapsed:.2f}s"
        # metadata integrity at volume
        assert all(c["total_chunks"] == len(chunks) for c in chunks)
        assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))

    def test_no_sentence_dropped_at_volume(self, monkeypatch):
        chunker = Chunker()
        monkeypatch.setattr(chunker, "max_tokens", 50)
        monkeypatch.setattr(chunker, "overlap_sentences", 1)
        markers = [f"UNIQ{i}" for i in range(300)]
        text = ". ".join(f"{m} alpha beta gamma" for m in markers) + "."
        chunks = chunker.chunk_section({"section": "discussion", "text": text})
        joined = " ".join(c["text"] for c in chunks)
        missing = [m for m in markers if m not in joined]
        assert missing == [], f"{len(missing)} sentences lost, e.g. {missing[:5]}"






# ── 2. Preextractor batch fan-out over many chunks ────────────────────────────

class TestPreextractorLoad:
    @pytest.fixture
    def fast_preextractor(self):
        with (
            patch("src.preextraction.preextractor.spacy.load", return_value=MagicMock()),
            patch("src.preextraction.negation_detector.AutoTokenizer.from_pretrained"),
            patch("src.preextraction.negation_detector.AutoModelForSequenceClassification.from_pretrained"),
        ):
            pre = Preextractor()
        pre._run_ensemble = MagicMock(return_value=MagicMock())
        pre.negation_detector.process = MagicMock(return_value={
            "entities": [], "has_negation": False, "negated_entities": []})
        return pre

    def test_batch_of_many_chunks_all_processed(self, fast_preextractor, monkeypatch):
        monkeypatch.setenv("NER_CHUNK_CONCURRENCY", "4")
        chunks = [{"text": f"chunk {i}", "document_id": f"d{i}",
                   "source_name": "biorxiv", "section": "results",
                   "chunk_index": i, "total_chunks": 200} for i in range(200)]
        with patch("src.preextraction.preextractor.NERTagger.from_doc", return_value=[]):
            start = time.perf_counter()
            out = fast_preextractor.process_batch(chunks)
            elapsed = time.perf_counter() - start

        assert len(out) == len(chunks)
        # order preserved by ThreadPoolExecutor.map
        assert [c["document_id"] for c in out] == [f"d{i}" for i in range(200)]
        assert elapsed < _bound(10.0)

    def test_concurrency_one_falls_back_to_serial(self, fast_preextractor, monkeypatch):
        monkeypatch.setenv("NER_CHUNK_CONCURRENCY", "1")
        chunks = [{"text": f"c{i}", "document_id": f"d{i}", "source_name": "biorxiv",
                   "section": "results", "chunk_index": i, "total_chunks": 20}
                  for i in range(20)]
        with patch("src.preextraction.preextractor.NERTagger.from_doc", return_value=[]):
            out = fast_preextractor.process_batch(chunks)
        assert len(out) == 20


# ── 3. Confidence scorer at volume (NLI stubbed to the neutral fallback) ───────

class TestConfidenceScorerLoad:
    def test_score_many_relations_within_budget(self):
        text = ("Gene A upregulates protein B (p < 0.001, 2.3-fold). "
                "We demonstrate this association in human liver tissue.")
        start = time.perf_counter()
        with patch("src.postextraction.confidence_scorer._nli_score",
                   return_value=(0.33, 0.33, 0.33)):
            scores = [
                confidence_scorer.score(f"A{i}", f"B{i}", False, 0.85, "results", text,
                                        relation="upregulates")
                for i in range(500)
            ]
        elapsed = time.perf_counter() - start
        assert len(scores) == 500
        assert all(0.0 <= s <= 1.0 for s in scores)
        assert elapsed < _bound(15.0), f"500 confidence scores took {elapsed:.2f}s"

    def test_scores_are_deterministic_under_repetition(self):
        text = "SIRT1 upregulates FOXO3 (p < 0.01)."
        a = confidence_scorer.score("SIRT1", "FOXO3", False, 0.9, "results", text,
                                    relation="upregulates")
        b = confidence_scorer.score("SIRT1", "FOXO3", False, 0.9, "results", text,
                                    relation="upregulates")
        assert a == b


# ── 4. Dedup store soak — many triples, bounded growth, correct PLN ───────────

class TestDedupStoreLoad:
    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRIPLE_STORE_PATH", str(tmp_path / "load.db"))
        return tmp_path

    def _rec(self, i, paper):
        return {
            "subject_id": f"NCBI_GENE:{i}", "subject_name": f"S{i}", "subject_type": "GENE",
            "relation": "upregulates",
            "object_id": f"NCBI_GENE:{i + 100000}", "object_name": f"O{i}", "object_type": "GENE",
            "negated": False, "confidence": 0.8, "document_id": paper,
        }

    def test_many_distinct_triples_insert_once_each(self, store):
        from src.postextraction import deduplication
        N = 500
        start = time.perf_counter()
        results = [deduplication.deduplicate(self._rec(i, "paper1")) for i in range(N)]
        elapsed = time.perf_counter() - start

        assert all(r["is_duplicate"] is False for r in results)
        assert len({r["triple_id"] for r in results}) == N   # all unique rows
        assert len(deduplication.get_all_triples()) == N       # store grew by exactly N
        assert elapsed < _bound(20.0), f"{N} inserts took {elapsed:.2f}s"

    def test_repeated_triple_does_not_inflate_store(self, store):
        from src.postextraction import deduplication
        # Same triple seen 50 times from 50 distinct papers.
        for k in range(50):
            deduplication.deduplicate(self._rec(1, f"paper{k}"))
        rows = deduplication.get_all_triples()
        assert len(rows) == 1                                  # bounded — one row only
        row = rows[0]
        # PLN Truth_w2c(50) = 50/51
        assert row["confidence"] == pytest.approx(50 / 51, abs=1e-4)

    def test_same_paper_repeated_does_not_inflate_confidence(self, store):
        from src.postextraction import deduplication
        # Same triple, SAME paper, seen 20 times → N must stay 1 (no inflation).
        for _ in range(20):
            deduplication.deduplicate(self._rec(2, "same_paper"))
        row = next(r for r in deduplication.get_all_triples() if r["subject_id"] == "NCBI_GENE:2")
        import json
        assert json.loads(row["source_papers"]) == ["same_paper"]
        # First insert stores confidence 0.8; duplicates from the same paper keep N=1
        # → Truth_w2c(1) = 0.5 on the first dup update, and stays there.
        assert row["confidence"] == pytest.approx(0.5, abs=1e-4)


# ── 5. Negation regex robustness on many contrastive constructions ────────────

class TestNegationRegexLoad:


    def test_clause_extraction_stable_on_long_sentence(self):
        # A pathological long sentence should not blow up clause extraction.
        sentence = ("The gene " + "TP53 " * 500
                    + "was upregulated but not the downstream target MDM2 in liver.")
        clause = _extract_entity_clause(sentence, "TP53")
        assert isinstance(clause, str) and clause


# ── 6. Post-extraction Orchestrator Load ─────────────────────────────────────

class TestPostextractorOrchestratorLoad:
    @patch("src.postextraction.entity_normalization.normalize_record")
    @patch("src.postextraction.deduplication.deduplicate")
    @patch("src.postextraction.contradiction_detection.check_contradiction")
    @patch("src.postextraction.cross_chunk_linking.link_within_paper")
    @patch("src.postextraction.two_pass_resolution.resolve")
    @patch("src.postextraction.semantic_validation.validate_batch")
    @patch("src.postextraction.atomspace_alignment.align")
    @patch("src.postextraction.confidence_scorer.explain")
    def test_large_batch_orchestration_within_budget(
        self,
        mock_explain,
        mock_align,
        mock_validate_batch,
        mock_resolve,
        mock_link,
        mock_contradiction,
        mock_dedup,
        mock_norm,
    ):
        from src.schema.pydantic_model import BiologicalRelation, ExtractionResult
        from src.schema.taxonomy import EntityType, RelationType
        from src.postextraction import postextractor
        import time

        def identity(r, *args, **kwargs): return r
        mock_norm.side_effect = lambda r, c: {**r, "subject_id": "NCBI_GENE:1", "object_id": "NCBI_GENE:2"}
        mock_dedup.side_effect = identity
        mock_contradiction.side_effect = identity
        mock_link.side_effect = lambda records: records
        mock_resolve.side_effect = lambda records, text_dict: records
        mock_validate_batch.side_effect = lambda batch: [{**r, "validation_verdict": "VALID"} for r, _ in batch]
        mock_align.side_effect = lambda records: records
        mock_explain.return_value = {"final_score": 0.8, "channels": {}}

        N_CHUNKS = 100
        N_RELS_PER_CHUNK = 5

        results = []
        chunks = []
        for i in range(N_CHUNKS):
            rels = []
            for j in range(N_RELS_PER_CHUNK):
                rels.append(BiologicalRelation(
                    extraction_viable=True,
                    subject_name=f"Subj_{i}_{j}",
                    subject_type=EntityType.GENE,
                    relation=RelationType.UPREGULATES,
                    object_name=f"Obj_{i}_{j}",
                    object_type=EntityType.GENE,
                    confidence=0.8,
                    reasoning="This is a valid reason that is sufficiently long enough to pass the fifty character requirement.",
                ))
            results.append(ExtractionResult(relations=rels))
            chunks.append({
                "document_id": f"doc_{i}",
                "text": f"This is text for chunk {i}",
                "section": "results",
                "chunk_index": i,
            })

        start = time.perf_counter()
        records = postextractor.process_batch(results, chunks)
        elapsed = time.perf_counter() - start

        assert len(records) == N_CHUNKS * N_RELS_PER_CHUNK
        # Because we mocked the heavy LLM/DB operations, this orchestration
        # loop should be extremely fast (essentially just Python dictionary manipulation).
        assert elapsed < _bound(15.0), f"Processing {len(records)} relations took {elapsed:.2f}s"

