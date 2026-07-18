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

from src.fetcher.chunker import Chunker, _split_sentences
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

    def test_token_cache_bounds_encode_calls(self, monkeypatch):
        """Encode calls must scale with DISTINCT sentences, not with overlap re-carries."""
        chunker = Chunker()
        monkeypatch.setattr(chunker, "max_tokens", 20)
        monkeypatch.setattr(chunker, "overlap_sentences", 3)

        calls = {"n": 0}
        real_encode = chunker.encoder.encode

        def counting_encode(text):
            calls["n"] += 1
            return real_encode(text)

        monkeypatch.setattr(chunker.encoder, "encode", counting_encode)
        n_sent = 400
        text = ". ".join(f"sentence {i} has a few words" for i in range(n_sent)) + "."
        chunker.chunk_section({"section": "results", "text": text})
        # Without caching, overlap re-carries would push this well above 2×N.
        # The initial chunk_section encode of the whole text is the +1.
        assert calls["n"] <= n_sent + 5, f"{calls['n']} encodes for {n_sent} sentences"

    def test_split_sentences_scales_linearly(self):
        small = ". ".join(f"s{i} word" for i in range(1000)) + "."
        large = ". ".join(f"s{i} word" for i in range(4000)) + "."

        t0 = time.perf_counter(); _split_sentences(small); t_small = time.perf_counter() - t0
        t0 = time.perf_counter(); _split_sentences(large); t_large = time.perf_counter() - t0

        # 4× the input should not take more than ~12× the time (linear-ish, slack
        # for constant-factor noise on small absolute timings).
        if t_small > 0.002:  # only assert the ratio when timings are meaningful
            assert t_large < t_small * 12, f"small={t_small:.4f}s large={t_large:.4f}s"
        assert t_large < _bound(2.0)


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
    def test_contrastive_regex_over_many_sentences(self):
        contrastive = [
            "A increased but not B",
            "A rose however B fell",
            "A worked whereas B did not",
            "A helped although B hurt",
            "X went up but Y went down",
        ]
        additive = [
            "not only A but also B",
            "A and B both increased",
            "A as well as B rose",
        ]
        # scale up to catch catastrophic-backtracking style blowups
        big_contrastive = contrastive * 200
        big_additive = additive * 200

        start = time.perf_counter()
        assert all(_CONTRASTIVE.search(s) for s in big_contrastive)
        assert all(not _CONTRASTIVE.search(s) for s in big_additive)
        elapsed = time.perf_counter() - start
        assert elapsed < _bound(2.0)

    def test_clause_extraction_stable_on_long_sentence(self):
        # A pathological long sentence should not blow up clause extraction.
        sentence = ("The gene " + "TP53 " * 500
                    + "was upregulated but not the downstream target MDM2 in liver.")
        clause = _extract_entity_clause(sentence, "TP53")
        assert isinstance(clause, str) and clause
