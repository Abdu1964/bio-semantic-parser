import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.schema.taxonomy import RelationType
from src.publish.output_layer import _doc_id_slug, _run_dir
from src.publish.validation_gate import route
from src.publish.human_review import append, load_pending, count_pending
from src.publish.neo4j_writer import write as neo4j_write
from src.publish.metta_writer import write as metta_write
from src.publish.atomspace_inserter import insert_to_atomspace
from src.publish.output_verifier import _find_supporting_sentence, verify_record, verify_run


# ── _doc_id_slug ───────────────────────────────────────────────────────────────

class TestDocIdSlug:
    def test_pmc_id(self):
        assert _doc_id_slug("PMC13197831") == "PMC13197831"

    def test_pmid(self):
        assert _doc_id_slug("42281177") == "PMID_42281177"

    def test_geo(self):
        assert _doc_id_slug("GSE210986") == "GSE210986"

    def test_clinical_trial(self):
        assert _doc_id_slug("NCT04488601") == "NCT04488601"

    def test_doi(self):
        slug = _doc_id_slug("10.1101/2023.01.01")
        assert slug.startswith("DOI_10.1101_2023.01.01")

    def test_url(self):
        slug = _doc_id_slug("https://example.com/article")
        assert "URL_" in slug
        assert "example" in slug.lower()

    def test_local_pdf_path(self):
        slug = _doc_id_slug("/home/user/inbox/paper.pdf")
        assert slug == "paper" or "paper" in slug

    def test_sha256_hash(self):
        slug = _doc_id_slug("a" * 64)
        assert slug.startswith("PDF_")
        assert len(slug) <= 20

    def test_empty_string(self):
        assert _doc_id_slug("") == ""

    def test_none(self):
        assert _doc_id_slug(None) == ""
        assert _doc_id_slug("") == ""


# ── validation_gate.route ─────────────────────────────────────────────────────

class TestRoute:
    def _record(self, **overrides):
        base = {
            "subject_name": "SIRT1", "subject_id": "NCBI_GENE:23411",
            "subject_type": "GENE",
            "relation": "upregulates",
            "object_name": "FOXO3", "object_id": "NCBI_GENE:2309",
            "object_type": "GENE",
            "negated": False,
            "confidence": 0.9,
            "validation_verdict": "VALID",
            "is_contradiction": False,
            "document_id": "doc-1",
        }
        base.update(overrides)
        return base

    def test_valid_record_goes_to_auto_insert(self):
        auto, review = route([self._record()])
        assert len(auto) == 1
        assert len(review) == 0
        assert auto[0]["insert_path"] == "auto"

    def test_contradiction_goes_to_human_review(self):
        auto, review = route([self._record(is_contradiction=True)])
        assert len(auto) == 0
        assert len(review) == 1
        assert "contradiction" in review[0]["gate_reason"].lower()

    def test_reject_verdict_goes_to_human_review(self):
        auto, review = route([self._record(validation_verdict="REJECT")])
        assert len(auto) == 0
        assert len(review) == 1

    def test_review_verdict_goes_to_human_review(self):
        auto, review = route([self._record(validation_verdict="REVIEW")])
        assert len(auto) == 0
        assert len(review) == 1

    def test_skipped_verdict_goes_to_human_review(self):
        auto, review = route([self._record(validation_verdict="SKIPPED")])
        assert len(auto) == 0
        assert len(review) == 1

    def test_unresolved_subject_id_goes_to_human_review(self):
        auto, review = route([self._record(subject_id="NEEDS_REVIEW")])
        assert len(auto) == 0
        assert len(review) == 1

    def test_text_label_subject_id_goes_to_human_review(self):
        auto, review = route([self._record(subject_id="TEXT:SIRT1")])
        assert len(auto) == 0
        assert len(review) == 1

    def test_empty_subject_id_goes_to_human_review(self):
        auto, review = route([self._record(subject_id="")])
        assert len(auto) == 0
        assert len(review) == 1

    def test_multiple_records_mixed(self):
        records = [
            self._record(subject_id="NCBI_GENE:1", validation_verdict="VALID"),
            self._record(subject_id="TEXT:X", validation_verdict="VALID"),
            self._record(subject_id="NCBI_GENE:2", is_contradiction=True),
        ]
        auto, review = route(records)
        assert len(auto) == 1
        assert len(review) == 2

    def test_empty_records(self):
        auto, review = route([])
        assert auto == []
        assert review == []


# ── human_review.append ───────────────────────────────────────────────────────

class TestHumanReviewAppend:
    def _record(self, **overrides):
        base = {
            "document_id": "doc-1", "source_name": "pubmed", "section": "results",
            "subject_name": "SIRT1", "subject_type": "GENE", "subject_id": "NCBI_GENE:23411",
            "relation": "upregulates",
            "object_name": "FOXO3", "object_type": "GENE", "object_id": "NCBI_GENE:2309",
            "negated": False, "confidence": 0.9,
            "validation_verdict": "VALID",
            "is_contradiction": False,
            "gate_reason": "test",
            "review_reason": "",
            "reasoning": "Some reasoning text here that explains the extraction.",
            "suggested_correction": "",
            "species": "", "tissue": "", "condition": "", "effect_size": "",
        }
        base.update(overrides)
        return base

    def test_appends_to_jsonl(self, tmp_path):
        run_dir = tmp_path / "run_001"
        run_dir.mkdir()
        count = append([self._record()], run_dir=run_dir)
        assert count == 1
        queue = run_dir / "human_review.jsonl"
        assert queue.exists()
        lines = queue.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["subject_name"] == "SIRT1"
        assert entry["status"] == "PENDING"

    def test_writes_csv(self, tmp_path):
        run_dir = tmp_path / "run_002"
        run_dir.mkdir()
        append([self._record()], run_dir=run_dir)
        csv_path = run_dir / "human_review.csv"
        assert csv_path.exists()

    def test_multiple_records(self, tmp_path):
        run_dir = tmp_path / "run_003"
        run_dir.mkdir()
        records = [self._record(subject_name=f"Gene{i}") for i in range(3)]
        count = append(records, run_dir=run_dir)
        assert count == 3
        queue = run_dir / "human_review.jsonl"
        lines = queue.read_text().splitlines()
        assert len(lines) == 3

    def test_empty_records_returns_zero(self):
        assert append([]) == 0


# ── neo4j_writer.write ────────────────────────────────────────────────────────

class TestNeo4jWriter:
    def _record(self, **overrides):
        base = {
            "subject_id": "NCBI_GENE:23411", "subject_name": "SIRT1", "subject_type": "GENE",
            "subject_id_source": "ncbi", "subject_needs_review": False,
            "relation": "upregulates",
            "object_id": "NCBI_GENE:2309", "object_name": "FOXO3", "object_type": "GENE",
            "object_id_source": "ncbi", "object_needs_review": False,
            "negated": False, "confidence": 0.9,
            "species": "Mus musculus", "tissue": "liver", "condition": "", "effect_size": "",
            "document_id": "doc-1", "section": "results",
            "source_name": "pubmed", "source_url": "https://example.com",
            "validation_verdict": "VALID", "alignment_action": "both_new",
            "reasoning": "Text with sufficient length to be meaningful for verification purposes.",
            "confidence_channels": "{}",
            "review_reason": "",
        }
        base.update(overrides)
        return base

    def test_writes_node_and_edge_csv(self, tmp_path):
        summary = neo4j_write([self._record()], run_dir=tmp_path)
        assert summary["node_count"] >= 2
        assert summary["edge_count"] >= 1
        # Check files exist
        node_files = summary["node_files"]
        edge_files = summary["edge_files"]
        assert len(node_files) >= 1
        assert len(edge_files) >= 1
        for f in node_files:
            assert Path(f).exists()
        for f in edge_files:
            assert Path(f).exists()

    def test_csv_contains_header_and_data(self, tmp_path):
        summary = neo4j_write([self._record()], run_dir=tmp_path)
        for path_str in summary["edge_files"]:
            content = Path(path_str).read_text()
            assert "source_id" in content
            assert "NCBI_GENE:23411" in content
            assert "NCBI_GENE:2309" in content

    def test_writes_cypher_files(self, tmp_path):
        summary = neo4j_write([self._record()], run_dir=tmp_path)
        for path_str in summary["node_files"]:
            cypher = Path(path_str).with_suffix(".cypher")
            assert cypher.exists()
            assert "CREATE CONSTRAINT" in cypher.read_text()
        for path_str in summary["edge_files"]:
            cypher = Path(path_str).with_suffix(".cypher")
            assert cypher.exists()
            assert "MATCH (source:" in cypher.read_text()

    def test_empty_records_returns_empty(self):
        summary = neo4j_write([])
        assert summary["node_count"] == 0
        assert summary["edge_count"] == 0

    def test_skips_needs_review_ids(self, tmp_path):
        record = self._record(subject_id="NEEDS_REVIEW")
        summary = neo4j_write([record], run_dir=tmp_path)
        assert summary["edge_count"] == 0


# ── metta_writer.write ────────────────────────────────────────────────────────

class TestMettaWriter:
    def _record(self, **overrides):
        base = {
            "subject_id": "NCBI_GENE:23411", "subject_name": "SIRT1", "subject_type": "GENE",
            "subject_id_source": "ncbi", "subject_needs_review": False,
            "relation": "upregulates",
            "object_id": "NCBI_GENE:2309", "object_name": "FOXO3", "object_type": "GENE",
            "object_id_source": "ncbi", "object_needs_review": False,
            "negated": False, "confidence": 0.9,
            "species": "Mus musculus", "tissue": "liver", "condition": "", "effect_size": "",
            "document_id": "doc-1", "section": "results",
            "source_name": "pubmed", "source_url": "https://example.com",
            "reasoning": "Text with sufficient length to be meaningful for verification purposes.",
        }
        base.update(overrides)
        return base

    def test_writes_node_and_edge_metta(self, tmp_path):
        summary = metta_write([self._record()], run_dir=tmp_path)
        assert summary["node_count"] >= 2
        assert summary["edge_count"] >= 1
        for f in summary["node_files"]:
            assert Path(f).exists()
        for f in summary["edge_files"]:
            assert Path(f).exists()

    def test_metta_contains_relations(self, tmp_path):
        summary = metta_write([self._record()], run_dir=tmp_path)
        for path_str in summary["edge_files"]:
            content = Path(path_str).read_text()
            assert "upregulates" in content
            assert "NCBI_GENE_23411" in content
            assert "NCBI_GENE_2309" in content

    def test_metta_id_format(self, tmp_path):
        summary = metta_write([self._record(subject_id="MESH:D020123")], run_dir=tmp_path)
        for path_str in summary["edge_files"]:
            content = Path(path_str).read_text()
            assert "MESH_D020123" in content

    def test_empty_records(self):
        summary = metta_write([])
        assert summary["node_count"] == 0
        assert summary["edge_count"] == 0


# ── atomspace_inserter.insert_to_atomspace ────────────────────────────────────

class TestAtomspaceInserter:
    def _record(self):
        return {
            "subject_id": "NCBI_GENE:1", "subject_name": "A", "subject_type": "GENE",
            "subject_id_source": "ncbi", "subject_needs_review": False,
            "relation": "upregulates",
            "object_id": "NCBI_GENE:2", "object_name": "B", "object_type": "GENE",
            "object_id_source": "ncbi", "object_needs_review": False,
            "negated": False, "confidence": 0.9,
            "document_id": "doc-1", "section": "results",
            "source_name": "pubmed", "source_url": "",
            "reasoning": "Text with sufficient length to be meaningful for verification purposes.",
        }

    def test_inserts_neo4j_and_metta(self, tmp_path):
        result = insert_to_atomspace([self._record()], run_dir=tmp_path, formats="both")
        assert result["records"] == 1
        assert result["neo4j"]["node_count"] >= 2
        assert result["metta"]["node_count"] >= 2

    def test_neo4j_only_format(self, tmp_path):
        result = insert_to_atomspace([self._record()], run_dir=tmp_path, formats="neo4j")
        assert result["neo4j"]["node_count"] >= 1
        assert result["metta"] == {}

    def test_metta_only_format(self, tmp_path):
        result = insert_to_atomspace([self._record()], run_dir=tmp_path, formats="metta")
        assert result["metta"]["node_count"] >= 1
        assert result["neo4j"] == {}

    def test_empty_records(self):
        result = insert_to_atomspace([], run_dir=Path("/tmp"))
        assert result["records"] == 0


# ── output_verifier ───────────────────────────────────────────────────────────

class TestFindSupportingSentence:
    def test_finds_sentence_with_both_entities(self):
        text = "SIRT1 upregulates FOXO3 in the liver. This was a key finding."
        result = _find_supporting_sentence(text, "SIRT1", "FOXO3")
        assert "SIRT1" in result
        assert "FOXO3" in result

    def test_fallback_to_subject_only(self):
        text = "SIRT1 was studied extensively. FOXO3 was mentioned elsewhere."
        result = _find_supporting_sentence(text, "SIRT1", "FOXO3")
        assert "SIRT1" in result

    def test_case_insensitive_match(self):
        text = "sirt1 upregulates foxo3."
        result = _find_supporting_sentence(text, "SIRT1", "FOXO3")
        assert result

    def test_empty_text_returns_empty(self):
        assert _find_supporting_sentence("", "SIRT1", "FOXO3") == ""


class TestVerifyRecord:
    def test_verified_when_both_found(self):
        text_by_doc = {"doc-1": "SIRT1 upregulates FOXO3 in mouse liver."}
        record = {"document_id": "doc-1", "subject_name": "SIRT1", "object_name": "FOXO3", "relation": "upregulates"}
        result = verify_record(record, text_by_doc)
        assert result["verified"] is True
        assert result["supporting_text"]

    def test_not_verified_when_subject_missing(self):
        text_by_doc = {"doc-1": "Nothing about the subject here."}
        record = {"document_id": "doc-1", "subject_name": "SIRT1", "object_name": "FOXO3", "relation": "upregulates"}
        result = verify_record(record, text_by_doc)
        assert result["verified"] is False

    def test_not_verified_when_object_missing(self):
        text_by_doc = {"doc-1": "SIRT1 is mentioned but the target gene is not named."}
        record = {"document_id": "doc-1", "subject_name": "SIRT1", "object_name": "FOXO3", "relation": "upregulates"}
        result = verify_record(record, text_by_doc)
        assert result["verified"] is False

    def test_not_verified_when_no_source_text(self):
        result = verify_record({"document_id": "unknown"}, {})
        assert result["verified"] is False
        assert "not available" in result["mismatch_reason"].lower()

    def test_uses_source_paper_as_fallback(self):
        text_by_doc = {"doc-1": "SIRT1 upregulates FOXO3."}
        record = {"source_paper": "doc-1", "subject_name": "SIRT1", "object_name": "FOXO3", "relation": "upregulates"}
        result = verify_record(record, text_by_doc)
        assert result["verified"] is True


class TestVerifyRun:
    def test_no_neo4j_dir_returns_empty(self, tmp_path):
        result = verify_run(tmp_path, {"doc-1": "text"})
        assert result["total"] == 0

    def test_verifies_edges_from_csv(self, tmp_path):
        text_by_doc = {"doc-1": "SIRT1 upregulates FOXO3 in the liver."}
        record = {
            "subject_id": "NCBI_GENE:1", "subject_name": "SIRT1", "subject_type": "GENE",
            "subject_id_source": "ncbi", "subject_needs_review": False,
            "relation": "upregulates",
            "object_id": "NCBI_GENE:2", "object_name": "FOXO3", "object_type": "GENE",
            "object_id_source": "ncbi", "object_needs_review": False,
            "negated": False, "confidence": 0.9,
            "document_id": "doc-1", "section": "results",
            "source_name": "pubmed", "source_url": "",
            "validation_verdict": "VALID", "alignment_action": "both_new",
            "reasoning": "Text with sufficient length to be meaningful for verification purposes.",
            "confidence_channels": "{}", "review_reason": "",
        }
        neo4j_write([record], run_dir=tmp_path)
        result = verify_run(tmp_path, text_by_doc)
        assert result["total"] >= 1
        assert result["verified"] >= 1


# ── output_layer.process ──────────────────────────────────────────────────────

class TestOutputLayer:
    def _record(self, **overrides):
        base = {
            "subject_id": "NCBI_GENE:23411", "subject_name": "SIRT1", "subject_type": "GENE",
            "subject_id_source": "ncbi", "subject_needs_review": False,
            "relation": "upregulates",
            "object_id": "NCBI_GENE:2309", "object_name": "FOXO3", "object_type": "GENE",
            "object_id_source": "ncbi", "object_needs_review": False,
            "negated": False, "confidence": 0.9,
            "species": "Mus musculus", "tissue": "liver", "condition": "", "effect_size": "",
            "document_id": "doc-1", "section": "results",
            "source_name": "pubmed", "source_url": "https://example.com",
            "validation_verdict": "VALID",
            "is_contradiction": False,
            "reasoning": "Text with sufficient length to be meaningful for verification purposes.",
            "confidence_channels": {},
            "review_reason": "",
            "_chunk_text": "SIRT1 upregulates FOXO3 in mouse liver.",
        }
        base.update(overrides)
        return base

    def test_no_records_returns_empty(self):
        from src.publish.output_layer import process
        summary = process([])
        assert summary["auto_insert"] == 0
        assert summary["human_review"] == 0

    def test_valid_record_goes_through_pipeline(self, tmp_path, monkeypatch):
        from src.publish.output_layer import process
        monkeypatch.setenv("KG_OUTPUT_ROOT", str(tmp_path))
        summary = process(
            [self._record()],
            text_by_doc={"doc-1": "SIRT1 upregulates FOXO3 in mouse liver."},
        )
        assert summary["auto_insert"] >= 1
        assert summary["human_review"] == 0
        assert summary["run_dir"]

    def test_contradiction_goes_to_human_review(self, tmp_path, monkeypatch):
        from src.publish.output_layer import process
        monkeypatch.setenv("KG_OUTPUT_ROOT", str(tmp_path))
        summary = process(
            [self._record(is_contradiction=True, validation_verdict="REJECT")],
            text_by_doc={"doc-1": "SIRT1 upregulates FOXO3."},
        )
        assert summary["auto_insert"] == 0
        assert summary["human_review"] >= 1

    def test_text_by_doc_built_from_chunk_text(self, tmp_path, monkeypatch):
        from src.publish.output_layer import process
        monkeypatch.setenv("KG_OUTPUT_ROOT", str(tmp_path))
        record = self._record(_chunk_text="SIRT1 upregulates FOXO3 in mouse liver tissue.")
        summary = process([record])
        assert summary["auto_insert"] >= 1
