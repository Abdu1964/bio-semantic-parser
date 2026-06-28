import types
from unittest.mock import MagicMock, patch

import pytest

from preextraction.doi_extractor import DOIExtractor
from preextraction.accession_detector import AccessionDetector
from preextraction.ner_tagger import NERTagger
from preextraction.negation_detector import NegationDetector, _extract_entity_clause
from preextraction.preextractor import Preextractor, _merge_entities


# DOIExtractor

class TestDOIExtractor:
    @pytest.fixture
    def extractor(self):
        return DOIExtractor()

    def test_extracts_standard_doi(self, extractor):
        text = "See doi: 10.1038/nature12345 for details."
        assert extractor.extract(text) == "10.1038/nature12345"

    def test_extracts_doi_with_slash_suffix(self, extractor):
        text = "Published in 10.1016/j.cell.2023.01.001 this year."
        result = extractor.extract(text)
        assert result == "10.1016/j.cell.2023.01.001"

    def test_strips_trailing_punctuation(self, extractor):
        text = "See 10.1234/abc-001."
        result = extractor.extract(text)
        assert not result.endswith(".")

    def test_returns_none_when_no_doi(self, extractor):
        text = "No DOI in this sentence."
        assert extractor.extract(text) is None

    def test_extracts_first_doi_only(self, extractor):
        text = "First: 10.1111/aaa.001 Second: 10.2222/bbb.002"
        result = extractor.extract(text)
        assert result == "10.1111/aaa.001"

    def test_doi_in_url_context(self, extractor):
        text = "https://doi.org/10.1000/xyz123 is the canonical URL."
        result = extractor.extract(text)
        assert result is not None
        assert "10.1000/xyz123" in result

    def test_strips_trailing_comma(self, extractor):
        text = "See 10.1234/abc-001, for details"
        result = extractor.extract(text)
        assert not result.endswith(",")

    def test_strips_trailing_semicolon(self, extractor):
        text = "see 10.1234/abc-001; more text"
        result = extractor.extract(text)
        assert not result.endswith(";")



# AccessionDetector

class TestAccessionDetector:
    @pytest.fixture
    def detector(self):
        return AccessionDetector()

    def test_detects_geo_accession(self, detector):
        text = "Data deposited in GEO under GSE123456."
        accessions = detector.extract(text)
        hits = [a for a in accessions if a["database"] == "GEO"]
        assert len(hits) == 1
        assert hits[0]["accession"] == "GSE123456"

    def test_detects_clinical_trials(self, detector):
        text = "Clinical trial NCT00000001 was registered."
        accessions = detector.extract(text)
        hits = [a for a in accessions if a["database"] == "ClinicalTrials"]
        assert any(a["accession"] == "NCT00000001" for a in hits)

    def test_detects_pubmed_pmid(self, detector):
        text = "Referenced PMID: 38000001 in the manuscript."
        accessions = detector.extract(text)
        hits = [a for a in accessions if a["database"] == "PubMed"]
        assert len(hits) >= 1

    def test_detects_bioproject(self, detector):
        text = "Submitted to BioProject: PRJNA654321."
        accessions = detector.extract(text)
        hits = [a for a in accessions if a["database"] == "BioProject"]
        assert len(hits) >= 1

    def test_detects_sra_srp(self, detector):
        text = "See SRP123456 for raw reads."
        accessions = detector.extract(text)
        hits = [a for a in accessions if a["database"] == "SRA"]
        assert len(hits) >= 1

    def test_detects_sra_srr(self, detector):
        text = "Individual run SRR9876543."
        accessions = detector.extract(text)
        hits = [a for a in accessions if a["database"] == "SRA"]
        assert len(hits) >= 1

    def test_detects_arrayexpress(self, detector):
        text = "Array data at E-MEXP-1234."
        accessions = detector.extract(text)
        hits = [a for a in accessions if a["database"] == "ArrayExpress"]
        assert len(hits) >= 1

    def test_returns_empty_when_no_accessions(self, detector):
        text = "No accession numbers mentioned in this text at all."
        assert detector.extract(text) == []

    def test_multiple_accessions_in_one_text(self, detector):
        text = "See GSE111111 and NCT00000002 for context."
        accessions = detector.extract(text)
        databases = {a["database"] for a in accessions}
        assert "GEO" in databases
        assert "ClinicalTrials" in databases



# NERTagger

def _make_fake_ent(text, label, start_char, end_char):
    """Build a minimal fake spaCy entity object."""
    ent = MagicMock()
    ent.text = text
    ent.label_ = label
    ent.start_char = start_char
    ent.end_char = end_char
    return ent


def _make_fake_doc(entities):
    doc = MagicMock()
    doc.ents = entities
    return doc


class TestNERTagger:
    def test_valid_entity_included(self):
        ent = _make_fake_ent("BRCA1", "GENE_OR_GENE_PRODUCT", 0, 5)
        doc = _make_fake_doc([ent])
        result = NERTagger.from_doc(doc)
        assert len(result) == 1
        assert result[0]["text"] == "BRCA1"

    def test_entity_dict_has_all_required_keys(self):
        ent = _make_fake_ent("insulin", "CHEMICAL", 0, 7)
        doc = _make_fake_doc([ent])
        result = NERTagger.from_doc(doc)
        keys = {"text", "normalized", "label", "start", "end", "negated", "assertion", "confidence"}
        assert keys.issubset(result[0].keys())

    def test_duplicate_entities_deduplicated(self):
        ent1 = _make_fake_ent("BRCA1", "GENE_OR_GENE_PRODUCT", 0, 5)
        ent2 = _make_fake_ent("BRCA1", "GENE_OR_GENE_PRODUCT", 10, 15)
        doc = _make_fake_doc([ent1, ent2])
        result = NERTagger.from_doc(doc)
        assert len(result) == 1

    def test_short_entity_filtered(self):
        ent = _make_fake_ent("x", "DISEASE", 0, 1)
        doc = _make_fake_doc([ent])
        result = NERTagger.from_doc(doc)
        assert result == []

    def test_bracket_entity_filtered(self):
        ent = _make_fake_ent("[CITATION]", "MISC", 0, 10)
        doc = _make_fake_doc([ent])
        result = NERTagger.from_doc(doc)
        assert result == []

    def test_numeric_entity_filtered(self):
        ent = _make_fake_ent("12345", "MISC", 0, 5)
        doc = _make_fake_doc([ent])
        result = NERTagger.from_doc(doc)
        assert result == []

    def test_entity_normalized_to_lowercase(self):
        ent = _make_fake_ent("VEGF", "GENE_OR_GENE_PRODUCT", 0, 4)
        doc = _make_fake_doc([ent])
        result = NERTagger.from_doc(doc)
        assert result[0]["normalized"] == "vegf"

    def test_default_assertion_is_present(self):
        ent = _make_fake_ent("p53", "GENE_OR_GENE_PRODUCT", 0, 3)
        doc = _make_fake_doc([ent])
        result = NERTagger.from_doc(doc)
        assert result[0]["assertion"] == "PRESENT"
        assert result[0]["negated"] is False
        assert result[0]["confidence"] == 1.0

    def test_empty_doc_returns_empty(self):
        doc = _make_fake_doc([])
        assert NERTagger.from_doc(doc) == []

    def test_is_valid_mixed_alpha_passes(self):
        # 30% alphabetic rule
        # p53a = 3/4 = 75%
        assert NERTagger._is_valid("p53a") is True

    def test_is_valid_paren_start_fails(self):
        assert NERTagger._is_valid("(group)") is False



# NegationDetector helpers

class TestExtractEntityClause:
    def test_no_contrastive_returns_full_sentence(self):
        sentence = "The protein was highly expressed."
        result = _extract_entity_clause(sentence, "protein")
        assert result == sentence

    def test_entity_in_positive_clause(self):
        sentence = "Protein X was upregulated but not protein Y."
        result = _extract_entity_clause(sentence, "Protein X")
        # Should be the clause before "but not"
        assert "protein Y" not in result.lower() or "Protein X" in result

    def test_entity_in_negative_clause(self):
        sentence = "Protein X was upregulated but not protein Y."
        result = _extract_entity_clause(sentence, "protein Y")
        assert result


# NegationDetector.process 

def _make_fake_nlp_doc(text, sents=None):
    """Fake spaCy Doc with sentence boundaries."""
    doc = MagicMock()
    doc.text = text
    if sents:
        sent_objs = []
        for s in sents:
            sent = MagicMock()
            sent.text = s
            sent.start_char = text.find(s)
            sent.end_char = sent.start_char + len(s)
            sent_objs.append(sent)
        doc.sents = sent_objs
    else:
        sent = MagicMock()
        sent.text = text
        sent.start_char = 0
        sent.end_char = len(text)
        doc.sents = [sent]
    return doc


class TestNegationDetector:
    """All transformer model calls are patched out."""

    @pytest.fixture
    def detector(self):
        with (
            patch("preextraction.negation_detector.AutoTokenizer.from_pretrained") as mock_tok,
            patch("preextraction.negation_detector.AutoModelForSequenceClassification.from_pretrained") as mock_mdl,
        ):
            mock_tok.return_value = MagicMock()
            mock_mdl.return_value = MagicMock()
            d = NegationDetector()
        return d

    def _mock_is_negated(self, detector, is_neg, confidence):
        detector._is_negated = MagicMock(return_value=(is_neg, confidence))

    def test_process_present_entity(self, detector):
        self._mock_is_negated(detector, False, 0.10)
        entities = [
            {"text": "BRCA1", "normalized": "brca1", "label": "GENE", "start": 4, "end": 9,
             "negated": False, "assertion": "PRESENT", "confidence": 1.0}
        ]
        doc = _make_fake_nlp_doc("The BRCA1 gene was expressed.")
        result = detector.process(entities, doc)
        assert result["has_negation"] is False
        assert result["entities"][0]["assertion"] == "PRESENT"

    def test_process_negated_entity(self, detector):
        self._mock_is_negated(detector, True, 0.85)
        entities = [
            {"text": "p53", "normalized": "p53", "label": "GENE", "start": 7, "end": 10,
             "negated": False, "assertion": "PRESENT", "confidence": 1.0}
        ]
        doc = _make_fake_nlp_doc("We found p53 not expressed in cells.")
        result = detector.process(entities, doc)
        assert result["has_negation"] is True
        assert result["entities"][0]["assertion"] == "ABSENT"
        assert result["entities"][0]["negated"] is True
        assert len(result["negated_entities"]) == 1

    def test_process_empty_entities(self, detector):
        doc = _make_fake_nlp_doc("Some text.")
        result = detector.process([], doc)
        assert result["entities"] == []
        assert result["has_negation"] is False
        assert result["negated_entities"] == []

    def test_process_clause_caching(self, detector):
        """Same clause should only be classified once (via cache)."""
        call_count = {"n": 0}
        original = detector._is_negated

        def counting_is_negated(clause):
            call_count["n"] += 1
            return (False, 0.05)

        detector._is_negated = counting_is_negated

        # Two entities in the same short doc, same sentence, same clause
        entities = [
            {"text": "p53", "normalized": "p53", "label": "GENE", "start": 4, "end": 7,
             "negated": False, "assertion": "PRESENT", "confidence": 1.0},
            {"text": "VEGF", "normalized": "vegf", "label": "GENE", "start": 8, "end": 12,
             "negated": False, "assertion": "PRESENT", "confidence": 1.0},
        ]
        doc = _make_fake_nlp_doc("The p53 VEGF genes were studied.")
        detector.process(entities, doc)
        # _is_negated should only be called once for the shared clause
        assert call_count["n"] == 1



# _merge_entities

class TestMergeEntities:
    def test_pubtator_entities_take_priority(self):
        ensemble = [
            {"text": "BRCA1", "normalized": "brca1", "label": "GENE"},
            {"text": "insulin", "normalized": "insulin", "label": "CHEMICAL"},
        ]
        pubtator = [
            {"text": "BRCA1", "normalized": "brca1", "label": "Gene", "identifier": "672"},
        ]
        result = _merge_entities(ensemble, pubtator)
        # pubtator entity with identifier should be in result
        pt_hits = [e for e in result if e.get("identifier") == "672"]
        assert len(pt_hits) == 1

    def test_ensemble_fills_gaps(self):
        ensemble = [
            {"text": "BRCA1", "normalized": "brca1", "label": "GENE"},
            {"text": "insulin", "normalized": "insulin", "label": "CHEMICAL"},
        ]
        pubtator = [
            {"text": "BRCA1", "normalized": "brca1", "label": "Gene", "identifier": "672"},
        ]
        result = _merge_entities(ensemble, pubtator)
        # "insulin" not in pubtator, comes from ensemble
        gap_fillers = [e for e in result if e["normalized"] == "insulin"]
        assert len(gap_fillers) == 1

    def test_no_duplicates_from_overlap(self):
        ensemble = [{"text": "p53", "normalized": "p53", "label": "GENE"}]
        pubtator = [{"text": "p53", "normalized": "p53", "label": "Gene", "identifier": "7157"}]
        result = _merge_entities(ensemble, pubtator)
        hits = [e for e in result if e["normalized"] == "p53"]
        assert len(hits) == 1

    def test_empty_pubtator_returns_all_ensemble(self):
        ensemble = [
            {"text": "BRCA1", "normalized": "brca1"},
            {"text": "VEGF", "normalized": "vegf"},
        ]
        result = _merge_entities(ensemble, [])
        assert len(result) == 2

    def test_empty_ensemble_returns_all_pubtator(self):
        pubtator = [{"text": "p53", "normalized": "p53", "identifier": "7157"}]
        result = _merge_entities([], pubtator)
        assert len(result) == 1



# Preextractor.process

def _make_fake_spacy_doc_for_preextractor(text):
    """Minimal fake spaCy doc usable by NERTagger.from_doc and NegationDetector."""
    doc = MagicMock()
    doc.text = text
    doc.ents = []
    doc.sents = [MagicMock(text=text, start_char=0, end_char=len(text))]
    return doc


class TestPreextractor:
    """
    Preextractor.__init__ loads three heavy spaCy models. patchED spacy.load
    to return a lightweight fake callable so tests are instant.
    """

    @pytest.fixture
    def preextractor(self):
        fake_doc = _make_fake_spacy_doc_for_preextractor("test")

        def fake_spacy_load(model_name):
            nlp = MagicMock()
            nlp.return_value = fake_doc
            return nlp

        with (
            patch("preextraction.preextractor.spacy.load", side_effect=fake_spacy_load),
            patch("preextraction.negation_detector.AutoTokenizer.from_pretrained") as mock_tok,
            patch(
                "preextraction.negation_detector.AutoModelForSequenceClassification.from_pretrained"
            ) as mock_mdl,
        ):
            mock_tok.return_value = MagicMock()
            mock_mdl.return_value = MagicMock()
            p = Preextractor()
        return p

    def _stub_negation(self, preextractor, is_neg=False, confidence=0.05):
        preextractor.negation_detector._is_negated = MagicMock(return_value=(is_neg, confidence))

    def test_process_returns_dict_with_required_keys(self, preextractor):
        self._stub_negation(preextractor)
        chunk = {
            "text": "BRCA1 gene was expressed in cancer cells.",
            "document_id": "doc-001",
            "source_name": "pubmed",
            "section": "abstract",
            "chunk_index": 0,
            "total_chunks": 1,
        }
        result = preextractor.process(chunk)
        required = {"entities", "has_negation", "negated_entities", "doi", "accession_numbers"}
        assert required.issubset(result.keys())

    def test_process_preserves_original_chunk_fields(self, preextractor):
        self._stub_negation(preextractor)
        chunk = {
            "text": "Short bio text.",
            "document_id": "doc-001",
            "source_name": "biorxiv",
            "section": "methods",
            "chunk_index": 1,
            "total_chunks": 3,
        }
        result = preextractor.process(chunk)
        assert result["section"] == "methods"
        assert result["chunk_index"] == 1
        assert result["total_chunks"] == 3

    def test_process_extracts_doi_if_present(self, preextractor):
        self._stub_negation(preextractor)
        chunk = {
            "text": "See 10.1038/s41586-024-00001-1 for reference.",
            "document_id": "sha256abc",
            "source_name": "pmc",
            "section": "abstract",
            "chunk_index": 0,
            "total_chunks": 1,
        }
        result = preextractor.process(chunk)
        assert result["doi"] == "10.1038/s41586-024-00001-1"

    def test_process_pdf_doc_id_replaced_by_doi(self, preextractor):
        self._stub_negation(preextractor)
        sha = "a" * 64  # simulate a SHA256 hash document_id
        chunk = {
            "text": "Published as 10.1000/xyz999.",
            "document_id": sha,
            "source_name": "local_pdf",
            "section": "abstract",
            "chunk_index": 0,
            "total_chunks": 1,
        }
        result = preextractor.process(chunk)
        assert result["document_id"] == "10.1000/xyz999"
        assert result["original_pdf_hash"] == sha

    def test_process_non_pdf_doc_id_not_replaced(self, preextractor):
        self._stub_negation(preextractor)
        chunk = {
            "text": "Has doi 10.1000/xyz999 embedded.",
            "document_id": "PMID12345",
            "source_name": "pubmed",
            "section": "abstract",
            "chunk_index": 0,
            "total_chunks": 1,
        }
        result = preextractor.process(chunk)
        # non-sha256 doc_id should not be replaced
        assert result["document_id"] == "PMID12345"

    def test_process_extracts_accession_numbers(self, preextractor):
        self._stub_negation(preextractor)
        chunk = {
            "text": "Data deposited in GEO: GSE999999. Trial NCT00000042 registered.",
            "document_id": "doc-002",
            "source_name": "pubmed",
            "section": "methods",
            "chunk_index": 0,
            "total_chunks": 1,
        }
        result = preextractor.process(chunk)
        databases = {a["database"] for a in result["accession_numbers"]}
        assert "GEO" in databases
        assert "ClinicalTrials" in databases

    def test_process_batch_returns_list(self, preextractor):
        self._stub_negation(preextractor)
        chunks = [
            {"text": "Chunk one.", "document_id": "d1", "source_name": "pubmed",
             "section": "abstract", "chunk_index": 0, "total_chunks": 2},
            {"text": "Chunk two.", "document_id": "d1", "source_name": "pubmed",
             "section": "abstract", "chunk_index": 1, "total_chunks": 2},
        ]
        results = preextractor.process_batch(chunks)
        assert len(results) == 2
        assert all("entities" in r for r in results)

    def test_process_pubmed_with_numeric_doc_id_calls_pubtator(self, preextractor):
        """When source_name=='pubmed' and doc_id is ≤10 chars, PubTator3 should be queried."""
        self._stub_negation(preextractor)

        with patch("preextraction.preextractor.fetch_pubtator_entities", return_value=[]) as mock_pt:
            chunk = {
                "text": "Short text.",
                "document_id": "38000001",  # ≤ 10 chars → should trigger pubtator
                "source_name": "pubmed",
                "section": "abstract",
                "chunk_index": 0,
                "total_chunks": 1,
            }
            preextractor.process(chunk)

        mock_pt.assert_called_once_with("38000001")

    def test_process_non_pubmed_does_not_call_pubtator(self, preextractor):
        self._stub_negation(preextractor)

        with patch("preextraction.preextractor.fetch_pubtator_entities", return_value=[]) as mock_pt:
            chunk = {
                "text": "BioRxiv preprint.",
                "document_id": "10.1101/2024.01.01",
                "source_name": "biorxiv",
                "section": "abstract",
                "chunk_index": 0,
                "total_chunks": 1,
            }
            preextractor.process(chunk)

        mock_pt.assert_not_called()
