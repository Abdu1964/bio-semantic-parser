"""
End-to-end integration tests that wire multiple pipeline layers together and
assert data flows correctly across the seams — not just that each unit works in
isolation.

Boundaries that touch the network or heavy ML models (spaCy load, NLI weights,
the extraction / validation LLMs, OLS4 / NCBI normalization APIs) are mocked at
their edge; everything between the seams runs for real:

  * Layer 4 Preextractor  → negation propagation, ensemble merge, batch fan-out
  * Layer 5 extract_batch → schema-validated ExtractionResult objects
  * Layer 7 postextractor → confidence scoring (REAL), dedup (REAL sqlite),
                            contradiction detection (REAL sqlite),
                            cross-chunk linking, atomspace alignment

conftest.py already stubs tiktoken (token == word), torch, transformers, etc.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.fetcher.chunker import Chunker
from src.preextraction.preextractor import Preextractor
from src.schema.pydantic_model import BiologicalRelation, ExtractionResult
from src.schema.taxonomy import EntityType, RelationType
from src.postextraction import confidence_scorer


# ── Shared builders ───────────────────────────────────────────────────────────

def _relation(subj, rel, obj, *, negated=False, conf=0.9,
              subj_type=EntityType.GENE, obj_type=EntityType.GENE):
    return BiologicalRelation(
        extraction_viable=True,
        subject_name=subj,
        subject_type=subj_type,
        relation=rel,
        object_name=obj,
        object_type=obj_type,
        negated=negated,
        confidence=conf,
        reasoning=(
            f"The source text states that {subj} {rel.value} {obj}. This is an "
            f"explicit finding reported directly in the results section of the paper."
        ),
    )


def _chunk(text, *, doc_id="doc-1", section="results", idx=0, total=1,
           source="biorxiv", entities=None):
    return {
        "text": text,
        "document_id": doc_id,
        "source_name": source,
        "source_url": f"https://example/{doc_id}",
        "section": section,
        "chunk_index": idx,
        "total_chunks": total,
        "entities": entities or [],
    }


@pytest.fixture
def temp_triple_store(tmp_path, monkeypatch):
    """Point every Layer-7 sqlite store at an isolated temp DB."""
    db = tmp_path / "triple_store.db"
    monkeypatch.setenv("TRIPLE_STORE_PATH", str(db))
    return db


@pytest.fixture
def preextractor_no_models():
    """A Preextractor whose spaCy + NLI loads are stubbed out."""
    with (
        patch("src.preextraction.preextractor.spacy.load", return_value=MagicMock()),
        patch("src.preextraction.negation_detector.AutoTokenizer.from_pretrained"),
        patch("src.preextraction.negation_detector.AutoModelForSequenceClassification.from_pretrained"),
    ):
        pre = Preextractor()
    return pre


# ── 1. Layer 4 → 5 seam: preextraction feeds schema extraction ────────────────

class TestPreextractToExtract:
    def test_entities_and_negation_flow_into_chunks(self, preextractor_no_models):
        pre = preextractor_no_models
        # Deterministic NER + negation instead of real models.
        pre._run_ensemble = MagicMock(return_value=MagicMock())
        pre.negation_detector.process = MagicMock(return_value={
            "entities": [
                {"text": "TP53", "label": "GENE", "negated": False, "assertion": "PRESENT"},
                {"text": "MDM2", "label": "GENE", "negated": True, "assertion": "ABSENT"},
            ],
            "has_negation": True,
            "negated_entities": [{"text": "MDM2", "label": "GENE", "negated": True}],
        })
        with patch("src.preextraction.preextractor.NERTagger.from_doc", return_value=[]):
            out = pre.process(_chunk("TP53 regulates MDM2 but not in liver.", source="biorxiv"))

        assert out["has_negation"] is True
        assert {e["text"] for e in out["entities"]} == {"TP53", "MDM2"}
        # metadata survives the layer untouched
        assert out["document_id"] == "doc-1"
        assert out["section"] == "results"

    def test_batch_preserves_order_and_count(self, preextractor_no_models):
        pre = preextractor_no_models
        pre._run_ensemble = MagicMock(return_value=MagicMock())
        pre.negation_detector.process = MagicMock(return_value={
            "entities": [], "has_negation": False, "negated_entities": []})
        with patch("src.preextraction.preextractor.NERTagger.from_doc", return_value=[]):
            chunks = [_chunk(f"sentence {i}", doc_id=f"d{i}", idx=i) for i in range(6)]
            out = pre.process_batch(chunks)
        assert len(out) == len(chunks)
        assert [c["document_id"] for c in out] == [f"d{i}" for i in range(6)]

    def test_extract_batch_yields_schema_objects(self):
        from src.schema.instructor_retry import extract_batch
        chunks = [_chunk("SIRT1 upregulates FOXO3.", doc_id="d0"),
                  _chunk("BRCA1 downregulates CCND1.", doc_id="d1")]
        canned = {
            "d0": ExtractionResult(relations=[_relation("SIRT1", RelationType.UPREGULATES, "FOXO3")]),
            "d1": ExtractionResult(relations=[_relation("BRCA1", RelationType.DOWNREGULATES, "CCND1")]),
        }

        def fake_create(*args, **kwargs):
            # Route by the chunk text in the USER message only — the system prompt
            # embeds taxonomy examples that themselves mention SIRT1/FOXO3.
            user = "".join(m.get("content", "") for m in kwargs.get("messages", [])
                           if m.get("role") == "user")
            return canned["d0"] if "SIRT1" in user else canned["d1"]

        with patch("src.schema.instructor_retry._client") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = fake_create
            results = extract_batch(chunks)

        assert len(results) == 2
        assert all(isinstance(r, ExtractionResult) for r in results)
        names = {r.relations[0].subject_name for r in results}
        assert names == {"SIRT1", "BRCA1"}


# ── 2. Confidence scorer runs for real end-to-end ─────────────────────────────

class TestConfidenceScoringReal:
    def test_results_section_scores_above_methods(self):
        """Open-Targets section weighting must make results > methods, all else equal."""
        text = ("SIRT1 upregulates FOXO3 (p < 0.001, 2.5-fold increase). "
                "We demonstrate this effect clearly.")
        hi = confidence_scorer.score("SIRT1", "FOXO3", False, 0.9, "results", text,
                                     relation="upregulates")
        lo = confidence_scorer.score("SIRT1", "FOXO3", False, 0.9, "methods", text,
                                     relation="upregulates")
        assert 0.0 <= lo <= hi <= 1.0
        assert hi > lo

    def test_negation_caps_confidence(self):
        text = "SIRT1 does not upregulate FOXO3 in these cells."
        capped = confidence_scorer.score("SIRT1", "FOXO3", True, 0.95, "results", text,
                                         relation="upregulates")
        assert capped <= 0.65

    def test_pvalue_boosts_quantitative_channel(self):
        strong = confidence_scorer.explain("A", "B", False, 0.8, "results",
                                           "A activates B (p < 0.001).", relation="activates")
        weak = confidence_scorer.explain("A", "B", False, 0.8, "results",
                                         "A activates B in some contexts.", relation="activates")
        assert strong["channels"]["C3_quantitative_evidence"] > weak["channels"]["C3_quantitative_evidence"]


# ── 3. Layer 7 dedup + PLN confidence revision on a real sqlite store ──────────

class TestDeduplicationStore:
    def test_second_occurrence_is_duplicate(self, temp_triple_store):
        from src.postextraction import deduplication
        rec = {
            "subject_id": "NCBI_GENE:23411", "subject_name": "SIRT1", "subject_type": "GENE",
            "relation": "upregulates",
            "object_id": "NCBI_GENE:2309", "object_name": "FOXO3", "object_type": "GENE",
            "negated": False, "confidence": 0.9, "document_id": "paperA",
        }
        first = deduplication.deduplicate(dict(rec))
        assert first["is_duplicate"] is False
        # same triple, different paper → duplicate, N grows, PLN confidence = N/(N+1)
        second = deduplication.deduplicate({**rec, "document_id": "paperB"})
        assert second["is_duplicate"] is True
        assert second["triple_id"] == first["triple_id"]

    def test_pln_confidence_grows_with_independent_papers(self, temp_triple_store):
        from src.postextraction import deduplication
        base = {
            "subject_id": "NCBI_GENE:1", "subject_name": "A", "subject_type": "GENE",
            "relation": "upregulates",
            "object_id": "NCBI_GENE:2", "object_name": "B", "object_type": "GENE",
            "negated": False, "confidence": 0.5,
        }
        for paper in ["p1", "p2", "p3"]:
            deduplication.deduplicate({**base, "document_id": paper})
        rows = deduplication.get_all_triples()
        row = next(r for r in rows if r["subject_id"] == "NCBI_GENE:1")
        # 3 independent papers → Truth_w2c(3) = 3/4 = 0.75
        assert row["confidence"] == pytest.approx(0.75, abs=1e-4)
        assert row["source_papers"].count("p") == 3

    def test_negated_and_positive_are_distinct_triples(self, temp_triple_store):
        from src.postextraction import deduplication
        base = {
            "subject_id": "NCBI_GENE:9", "subject_name": "X", "subject_type": "GENE",
            "relation": "upregulates",
            "object_id": "NCBI_GENE:10", "object_name": "Y", "object_type": "GENE",
            "confidence": 0.8, "document_id": "p1",
        }
        pos = deduplication.deduplicate({**base, "negated": False})
        neg = deduplication.deduplicate({**base, "negated": True})
        assert pos["triple_id"] != neg["triple_id"]
        assert len(deduplication.get_all_triples()) == 2


# ── 4. Contradiction detection reads the same store dedup wrote ────────────────

class TestContradictionDetection:
    def test_opposing_relation_flagged(self, temp_triple_store):
        from src.postextraction import deduplication, contradiction_detection
        up = {
            "subject_id": "NCBI_GENE:100", "subject_name": "P", "subject_type": "GENE",
            "relation": "upregulates",
            "object_id": "NCBI_GENE:200", "object_name": "Q", "object_type": "GENE",
            "negated": False, "confidence": 0.9, "document_id": "p1",
        }
        deduplication.deduplicate(dict(up))
        # A later paper reports the OPPOSING relation for the same pair.
        down = {**up, "relation": "downregulates", "document_id": "p2"}
        checked = contradiction_detection.check_contradiction(down)
        assert checked["is_contradiction"] is True
        assert checked.get("review_reason")

    def test_same_relation_not_flagged(self, temp_triple_store):
        from src.postextraction import deduplication, contradiction_detection
        up = {
            "subject_id": "NCBI_GENE:300", "subject_name": "R", "subject_type": "GENE",
            "relation": "activates",
            "object_id": "NCBI_GENE:400", "object_name": "S", "object_type": "GENE",
            "negated": False, "confidence": 0.9, "document_id": "p1",
        }
        deduplication.deduplicate(dict(up))
        again = {**up, "document_id": "p2"}
        checked = contradiction_detection.check_contradiction(again)
        assert checked.get("is_contradiction", False) is False


# ── 5. Full Layer 7 orchestrator over multiple chunks (real scoring/dedup) ─────

class TestPostExtractorOrchestration:
    def _patch_layer7_externals(self):
        """Patch only the network/LLM-bound Layer-7 steps; leave scoring+dedup real."""
        # entity_normalization → deterministic ontology IDs so triples aren't flagged
        # non-biomedical; two_pass + semantic_validation are LLM-bound → stub.
        # IDs are derived from each entity NAME so distinct entities get distinct
        # IDs (identical IDs would make unrelated relations look like the same pair).
        _ids = {}

        def _fake_norm(rec, chunk):
            def _id(name):
                return _ids.setdefault(name, f"NCBI_GENE:{len(_ids) + 1}")
            return {
                **rec,
                "subject_id": _id(rec["subject_name"]), "subject_id_source": "test",
                "subject_needs_review": False,
                "object_id": _id(rec["object_name"]), "object_id_source": "test",
                "object_needs_review": False,
                "review_reason": "",
            }

        norm = patch(
            "src.postextraction.postextractor.entity_normalization.normalize_record",
            side_effect=_fake_norm,
        )
        # semantic validation runs in a thread pool inside postextractor → stub the
        # batch fn to mark everything VALID without an LLM call.
        def _fake_vbatch(pairs):
            return [{**rec, "validation_verdict": "VALID",
                     "validation_reasoning": "stub", "is_semantically_valid": True}
                    for rec, _ in pairs]
        vbatch = patch("src.postextraction.semantic_validation.validate_batch",
                       side_effect=_fake_vbatch)
        return norm, vbatch

    def test_two_chunk_paper_produces_scored_records(self, temp_triple_store):
        from src.postextraction import postextractor
        results = [
            ExtractionResult(relations=[_relation("SIRT1", RelationType.UPREGULATES, "FOXO3")]),
            ExtractionResult(relations=[_relation("BRCA1", RelationType.DOWNREGULATES, "CCND1")]),
        ]
        chunks = [
            _chunk("SIRT1 upregulates FOXO3 (p < 0.001).", doc_id="paperX", idx=0, total=2),
            _chunk("BRCA1 downregulates CCND1 significantly.", doc_id="paperX", idx=1, total=2),
        ]
        norm, vbatch = self._patch_layer7_externals()
        with norm, vbatch:
            records = postextractor.process_batch(results, chunks)

        assert len(records) == 2
        for r in records:
            assert 0.0 <= r["confidence"] <= 1.0
            assert r["validation_verdict"] == "VALID"
            assert r["triple_id"] is not None            # persisted to store
            assert r["alignment_action"] in {"both_new", "connect_existing", "create_one_new"}

    def test_negated_relation_confidence_capped_through_orchestrator(self, temp_triple_store):
        from src.postextraction import postextractor
        results = [ExtractionResult(relations=[
            _relation("SIRT1", RelationType.UPREGULATES, "FOXO3", negated=True)])]
        chunks = [_chunk("SIRT1 does not upregulate FOXO3.", doc_id="pZ")]
        norm, vbatch = self._patch_layer7_externals()
        with norm, vbatch:
            records = postextractor.process_batch(results, chunks)
        assert records[0]["confidence"] <= 0.65
        assert records[0]["negated"] is True

    def test_non_viable_relations_dropped(self, temp_triple_store):
        from src.postextraction import postextractor
        nonviable = BiologicalRelation(
            extraction_viable=False,
            reasoning=("No extractable biological relation — the text is purely "
                       "methodological background with no named entity pair."),
        )
        results = [ExtractionResult(relations=[nonviable])]
        chunks = [_chunk("Background text with no extractable relation.", doc_id="pE")]
        norm, vbatch = self._patch_layer7_externals()
        with norm, vbatch:
            records = postextractor.process_batch(results, chunks)
        assert records == []


# ── 6. Fetcher → chunker seam with coref offline (graceful degradation) ────────

class TestFetchChunkSeam:
    def test_long_text_chunks_carry_consistent_metadata(self, monkeypatch):
        chunker = Chunker()
        monkeypatch.setattr(chunker, "max_tokens", 8)
        text = ". ".join([f"sentence number {i} has several tokens here" for i in range(10)])
        chunks = chunker.chunk_section({"section": "results", "text": text})
        assert len(chunks) > 1
        assert all(c["total_chunks"] == len(chunks) for c in chunks)
        assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
        # no sentence content lost across the split
        joined = " ".join(c["text"] for c in chunks)
        assert "sentence number 0" in joined and "sentence number 9" in joined
