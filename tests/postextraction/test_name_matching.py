"""Name validation, concept consistency and ontology-based typing in entity normalization."""
from unittest.mock import patch

import pytest

from src.postextraction import entity_normalization as en
from src.postextraction.name_matching import match_quality, term_key, CLOSE, EXACT

EN = "src.postextraction.entity_normalization"


@pytest.mark.parametrize("query,label,synonyms", [
    ("aging", "negative regulation of age-related resistance", ""),
    ("D. melanogaster", "Drosophila melanogaster sigmavirus Derby", ""),
    ("Drosophila melangaster", "Panorpida", ""),
    ("cancer", "Castration-Resistant Prostatic Neoplasms", ""),
    ("insulin", "insulin receptor", ""),
    ("D. simulans", "Drosophila melanogaster", ""),
])
def test_rejects_wrong_matches(query, label, synonyms):
    assert match_quality(query, label, synonyms) == "none"


@pytest.mark.parametrize("query,label,synonyms", [
    ("aging", "Aging", ""),
    ("ageing", "aging", ""),
    ("D. melanogaster", "Drosophila melanogaster", ""),
    ("C. elegans", "Caenorhabditis elegans", ""),
    ("Drosophila populations", "population of Drosophila", ""),
    ("Alzheimer's disease", "Alzheimer disease", ""),
    ("mice", "mouse", ""),
    ("IL-6", "IL6", "interleukin-6"),
    ("Hutchinson-Gilford syndrome", "Progeria", "Hutchinson-Gilford syndrome"),
])
def test_accepts_same_term(query, label, synonyms):
    assert match_quality(query, label, synonyms) == "exact"


def test_typo_is_close():
    assert match_quality("Drosophila melangaster", "Drosophila melanogaster") == "close"


def test_term_key_ignores_plural_and_word_order():
    assert term_key("Drosophila populations") == term_key("population of Drosophila")


def test_ols_skips_unrelated_first_hit():
    """OLS ranks by text, not meaning — a non-matching first hit must not be returned."""
    docs = [{"obo_id": "GO:1904249", "label": "negative regulation of age-related resistance",
             "ontology_prefix": "go", "synonym": []}]
    resp = type("R", (), {"json": lambda self: {"response": {"docs": docs}}})()
    with patch(f"{EN}.requests.get", return_value=resp):
        assert en._ols4_search_uncached("aging", ["go"]) is None


def test_taxon_lookup_rejects_unrelated_name():
    with patch(f"{EN}._ncbi_taxon_candidates", return_value=[("3701063", "Panorpida", "", "clade")]), \
         patch(f"{EN}._ncbi_taxon_spell", return_value=""):
        en._taxon_search_cache.clear()
        assert en._ncbi_taxon_search("Drosophila xyzgaster") is None


def test_taxon_lookup_recovers_typo_via_spelling():
    def cands(term, **_):
        if term.lower() == "drosophila melanogaster":
            return [("7227", "Drosophila melanogaster", "fruit fly", "species")]
        return [("3701063", "Panorpida", "", "clade")]
    with patch(f"{EN}._ncbi_taxon_candidates", side_effect=cands), \
         patch(f"{EN}._ncbi_taxon_spell", return_value="drosophila melanogaster"):
        en._taxon_search_cache.clear()
        assert en._ncbi_taxon_search("Drosophila melangaster")[0] == "NCBITaxon:7227"


def test_local_taxon_handles_abbreviation_and_typo():
    assert en._local_taxon("D. melanogaster") == "7227"
    assert en._local_taxon("Drosophila melangaster") == "7227"


def test_abbreviation_expanded_from_genus_in_same_paper():
    en.reset_document_cache()
    en._remember_genus("Drosophila melanogaster")
    with patch(f"{EN}._ncbi_taxon_search", side_effect=lambda t: ("NCBITaxon:7240", t, False)
               if t == "Drosophila simulans" else None):
        assert en._expand_abbreviated_binomial("D. simulans") == "Drosophila simulans"


def test_abbreviation_never_expands_to_a_species_that_does_not_exist():
    en.reset_document_cache()
    en._remember_genus("Coelopa frigida")                 # another 'C.' genus in the same paper
    with patch(f"{EN}._ncbi_taxon_search", return_value=None):
        assert en._expand_abbreviated_binomial("C. elegans") == "Caenorhabditis elegans"
        assert en._expand_abbreviated_binomial("C. briggsae") is None


def test_ambiguous_abbreviation_is_flagged():
    en.reset_document_cache()
    hit = {"canonical_id": "NCBITaxon:2339938", "canonical_name": "Dicranomyia simulans",
           "id_source": "ols4", "ontology_prefix": "ncbitaxon", "needs_review": False}
    with patch(f"{EN}._normalize_entity_core", return_value=dict(hit)), \
         patch(f"{EN}._ncbi_taxon_search", return_value=None):          # no genus can be confirmed
        assert en.normalize_entity("D. simulans", "ORGANISM")["needs_review"] is True


def test_abbreviation_resolved_when_only_one_genus_gives_a_real_species():
    en.reset_document_cache()
    with patch(f"{EN}._ncbi_taxon_search", side_effect=lambda t: ("NCBITaxon:7240", t, False)
               if t == "Drosophila simulans" else None):
        assert en._expand_abbreviated_binomial("D. simulans") == "Drosophila simulans"


def test_same_term_gets_same_id_across_types_in_one_paper():
    en.reset_document_cache()
    first = {"canonical_id": "mesh:D000375", "canonical_name": "Aging", "id_source": "ols4",
             "ontology_prefix": "mesh", "needs_review": False}
    with patch(f"{EN}._normalize_entity_core", return_value=dict(first)) as core:
        a = en.normalize_entity("aging", "DISEASE")
        b = en.normalize_entity("Aging", "BIOLOGICAL_PROCESS")
        c = en.normalize_entity("ageing", "PHENOTYPE")
    assert a["canonical_id"] == b["canonical_id"] == c["canonical_id"] == "mesh:D000375"
    assert core.call_count == 1


def test_gene_and_protein_with_same_name_stay_separate():
    en.reset_document_cache()
    results = iter([
        {"canonical_id": "ENSEMBL:ENSG00000141510", "canonical_name": "TP53", "id_source": "ensembl",
         "ontology_prefix": "", "needs_review": False},
        {"canonical_id": "UniProtKB:P04637", "canonical_name": "TP53", "id_source": "uniprot",
         "ontology_prefix": "", "needs_review": False},
    ])
    with patch(f"{EN}._normalize_entity_core", side_effect=lambda *a, **k: next(results)):
        gene = en.normalize_entity("TP53", "GENE")
        prot = en.normalize_entity("TP53", "PROTEIN")
    assert gene["canonical_id"] != prot["canonical_id"]


def test_placeholders_are_not_reused():
    en.reset_document_cache()
    unresolved = {"canonical_id": "TEXT:foo", "canonical_name": "", "id_source": "fuzzy",
                  "ontology_prefix": "", "needs_review": True}
    with patch(f"{EN}._normalize_entity_core", return_value=dict(unresolved)) as core:
        en.normalize_entity("foo", "OTHER")
        en.normalize_entity("foo", "OTHER")
    assert core.call_count == 2


@pytest.mark.parametrize("declared,trees,expected", [
    ("DISEASE", ["G07.345.124"], "BIOLOGICAL_PROCESS"),      # MeSH Aging is a process
    ("PHENOTYPE", ["G07.345.124"], "BIOLOGICAL_PROCESS"),
    ("BIOLOGICAL_PROCESS", ["C18.452.625"], "DISEASE"),       # a C-tree term is a disease
    ("SYMPTOM", ["C23.888.592"], "SYMPTOM"),                   # compatible type kept
    ("GENE", ["G07.345.124"], "GENE"),                         # only disease-like types are corrected
])
def test_mesh_tree_corrects_contradicting_type(declared, trees, expected):
    with patch(f"{EN}._mesh_tree_numbers", return_value=trees):
        assert en._correct_type_from_ontology(declared, "mesh:D000375", "mesh") == expected


def test_prefix_types():
    assert en._correct_type_from_ontology("CELL_TYPE", "NCBITaxon:7227") == "ORGANISM"
    assert en._correct_type_from_ontology("VIRUS", "NCBITaxon:11676") == "VIRUS"
    assert en._correct_type_from_ontology("BIOLOGICAL_PROCESS", "MONDO:0005015") == "DISEASE"
    assert en._correct_type_from_ontology("DISEASE", "TEXT:aging") == "DISEASE"


def test_mesh_single_tree_number_string_is_parsed():
    resp = type("R", (), {"ok": True, "json": lambda self: {"treeNumber": "http://id.nlm.nih.gov/mesh/G07.345.124"}})()
    en._mesh_tree_cache.pop("D999999", None)
    with patch(f"{EN}.requests.get", return_value=resp):
        assert en._mesh_tree_numbers("D999999") == ["G07.345.124"]


@pytest.mark.parametrize("declared,cid,info,expected", [
    ("CELL_TYPE", "FBcv:0006003", ("flybase miscellaneous cv", "population of Drosophila"), "POPULATION"),
    ("PHENOTYPE", "FBcv:0001234", ("flybase miscellaneous cv", "experimental tool"), "OTHER"),   # non-phenotype FBcv
    ("CELL_TYPE", "FBcv:0000351", ("phenotypic_class", "lethal"), "PHENOTYPE"),
    ("PHENOTYPE", "GO:0008340", ("biological_process", "determination of adult lifespan"), "BIOLOGICAL_PROCESS"),
    ("PATHWAY", "GO:0008340", ("biological_process", "determination of adult lifespan"), "PATHWAY"),
    ("ANATOMY", "GO:0005739", ("cellular_component", "mitochondrion"), "CELLULAR_COMPONENT"),
    ("CELL_TYPE", "FBcv:0006003", ("", ""), "CELL_TYPE"),                                          # lookup failed → unchanged
])
def test_namespace_decides_type_for_mixed_ontologies(declared, cid, info, expected):
    with patch(f"{EN}._ols_term_info", return_value=info):
        assert en._correct_type_from_ontology(declared, cid) == expected


def test_failed_term_lookup_is_not_cached():
    en._ols_term_cache.pop("FBcv:0000001", None)
    with patch(f"{EN}.requests.get", side_effect=TimeoutError("down")):
        assert en._ols_term_info("FBcv:0000001") == ("", "")
    assert "FBcv:0000001" not in en._ols_term_cache


def test_swapped_head_noun_is_only_close():
    assert match_quality("control rodents", "Rodent Control", []) == CLOSE
    assert match_quality("population of Drosophila", "Drosophila populations", []) == EXACT


def test_inverted_synonym_does_not_hide_swapped_meaning():
    assert match_quality("control rodents", "Rodent Control", ["Control, Rodent"]) == CLOSE
    assert match_quality("type 2 diabetes", "Diabetes, Type 2", []) == EXACT


ABBREV_TEXT = ("Antagonistic pleiotropy (AP) is central. Children with Hutchinson-Gilford progeria syndrome (HGPS) "
               "age fast. The insulin/IGF-1 signaling (IIS) pathway. Values were high (p < 0.05), see (Fig. 2) and "
               "(Rose, 1984). Flies (Drosophila) were used.")


def test_paper_defined_abbreviations_are_found():
    assert en.find_abbreviations(ABBREV_TEXT) == {
        "AP": "Antagonistic pleiotropy", "HGPS": "Hutchinson-Gilford progeria syndrome",
        "IIS": "insulin/IGF-1 signaling"}


def test_paper_abbreviation_expanded_before_lookup():
    en.reset_document_cache()
    en._document_abbrevs.update(en.find_abbreviations(ABBREV_TEXT))
    assert en._expand_document_abbreviations("HGPS patients") == "Hutchinson-Gilford progeria syndrome patients"
    assert en._expand_document_abbreviations("APOE") == "APOE"
    seen = []
    with patch(f"{EN}._normalize_entity_core", side_effect=lambda t, *a, **k: seen.append(t) or {
            "canonical_id": "WD:Q288377", "canonical_name": t, "id_source": "wikidata", "needs_review": False}):
        en.normalize_entity("AP", "BIOLOGICAL_PROCESS")
    assert seen == ["Antagonistic pleiotropy"]
    en.reset_document_cache()
