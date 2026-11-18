"""Local copies answer like the services: OLS exact search, HGNC fetch, NCBI Gene by symbol or synonym."""
import gzip

import pytest

from src import local_sources
from src.local_sources import common, hgnc, ncbi_gene, ontology

_OBO = """format-version: 1.2

[Term]
id: MONDO:0005578
name: arthritis
synonym: "arthritides" EXACT []
synonym: "joint inflammation" RELATED []

[Term]
id: MONDO:0000001
name: old term
is_obsolete: true
"""

_GENE_INFO = ("#tax_id\tGeneID\tSymbol\tLocusTag\tSynonyms\tdbXrefs\tchromosome\tmap_location\tdescription\t"
              "type_of_gene\tSymbol_from_nomenclature_authority\tFull_name_from_nomenclature_authority\t"
              "Nomenclature_status\tOther_designations\tModification_date\tFeature_type\n"
              "10116\t24835\tTnf\t-\tTNF-alpha|TNFA\tRGD:3876|Ensembl:ENSRNOG00000055156\t20\t-\t"
              "tumor necrosis factor\tprotein-coding\t-\t-\t-\tcachectin\t20250101\t-\n")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "ROOT", str(tmp_path))
    monkeypatch.setattr(ontology, "_ols_prefix", lambda name: name.upper())
    monkeypatch.setattr(ncbi_gene, "_DB", common.path("ncbi_gene", "genes.db"))
    monkeypatch.setattr(hgnc, "_DB", common.path("hgnc", "genes.db"))
    return tmp_path


def test_ontology_exact_search_like_ols(root, monkeypatch):
    def fake_download(url, dest):
        (root / "ontology" / "mondo").mkdir(parents=True, exist_ok=True)
        open(dest, "w").write(_OBO)
        return dest
    monkeypatch.setattr(ontology, "download", fake_download)
    assert ontology.build("mondo", log=lambda m: None) == 1          # obsolete term left out
    resp = local_sources.answer("GET", "https://www.ebi.ac.uk/ols4/api/search",
                                params={"q": "Arthritides", "exact": "true", "ontology": "mondo"})
    doc = resp.json()["response"]["docs"][0]
    assert (doc["obo_id"], doc["label"], doc["ontology_prefix"]) == ("MONDO:0005578", "arthritis", "MONDO")
    assert doc["iri"] == "http://purl.obolibrary.org/obo/MONDO_0005578"
    assert local_sources.answer("GET", "https://www.ebi.ac.uk/ols4/api/search",
                                params={"q": "arthritis", "exact": "false", "ontology": "mondo"}) is None


def test_unbuilt_copy_defers_to_online(root):
    assert local_sources.answer("GET", "https://www.ebi.ac.uk/ols4/api/search",
                                params={"q": "x", "exact": "true", "ontology": "go"}) is None


def test_ncbi_gene_symbol_and_synonym(root, monkeypatch):
    def fake_download(url, dest):
        (root / "ncbi_gene").mkdir(parents=True, exist_ok=True)
        with gzip.open(dest, "wt") as f:
            f.write(_GENE_INFO)
        return dest
    monkeypatch.setattr(ncbi_gene, "download", fake_download)
    monkeypatch.setattr(ncbi_gene, "GROUPS", ["Mammalia"])
    ncbi_gene.build(log=lambda m: None)
    assert ncbi_gene.find("TNF-alpha", "10116")["ensembl"] == "ENSRNOG00000055156"
    assert ncbi_gene.find("Tnf", "10116")["symbol"] == "Tnf"
    assert ncbi_gene.find("Tnf", "9606") is None
