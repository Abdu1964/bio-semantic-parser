"""One ID per gene/protein per paper; type names are not entities; UniProt symbols match exactly."""
from src.postextraction import entity_normalization as E


def _rec(subject, sid, obj, oid, species="Rattus norvegicus", stype="PROTEIN", otype="GENE"):
    return {"subject_name": subject, "subject_id": sid, "subject_type": stype,
            "object_name": obj, "object_id": oid, "object_type": otype, "species": species}


def test_symbol_key_forms():
    assert E._symbol_key("IL-6") == E._symbol_key("IL6 mRNA") == "il6"
    assert E._symbol_key("IL-1β") == E._symbol_key("IL1B") == "il1b"
    assert E._symbol_key("C-reactive protein") == ""


def test_same_symbol_same_organism_takes_ensembl():
    recs = [_rec("RTD-1", "mesh:C1", "IL-6", "UniProtKB:P20607"),
            _rec("RTD-1", "mesh:C1", "IL6 mRNA", "ENSEMBL:ENSRNOG00000010278")]
    E.merge_same_molecule(recs)
    assert {r["object_id"] for r in recs} == {"ENSEMBL:ENSRNOG00000010278"}


def test_other_organism_kept_apart():
    recs = [_rec("RTD-1", "mesh:C1", "IL6", "ENSEMBL:ENSRNOG00000010278"),
            _rec("RTD-1", "mesh:C1", "IL6", "ENSEMBL:ENSG00000136244", species="Homo sapiens")]
    E.merge_same_molecule(recs)
    assert [r["object_id"] for r in recs] == ["ENSEMBL:ENSRNOG00000010278", "ENSEMBL:ENSG00000136244"]


def test_unstated_organism_joins_only_organism():
    recs = [_rec("RTD-1", "mesh:C1", "CCL7", "ENSEMBL:ENSG00000108688", species="Homo sapiens"),
            _rec("RTD-1", "mesh:C1", "CCL7", "UniProtKB:Q9QXY8", species="Not stated")]
    E.merge_same_molecule(recs)
    assert {r["object_id"] for r in recs} == {"ENSEMBL:ENSG00000108688"}


def test_type_name_is_not_an_entity():
    assert E._type_name_only("disease", "DISEASE") == "generic category term"
    assert E._type_name_only("genes", "GENE") == "generic category term"
    assert E._type_name_only("arthritis", "DISEASE") == ""


def test_uniprot_symbol_needs_exact_name(monkeypatch):
    wrong = ("UniProtKB:O95256", "IL18RAP", "", "IL-1 receptor accessory protein-like; IL-1RAcPL")
    monkeypatch.setattr(E, "_uniprot_search", lambda *a, **k: wrong)
    monkeypatch.setattr(E, "_ensembl_local_gene", lambda *a, **k: None)
    for later in ("_ols4_search", "_try_core_term", "_verified_candidate", "_wikidata_search", "_ncbi_gene",
                  "_llm_standard_name"):
        monkeypatch.setattr(E, later, lambda *a, **k: None)
    monkeypatch.setattr(E, "_llm_broader_names", lambda *a, **k: [])
    res = E._normalize_entity_core("IL-1β", "PROTEIN", None, "Homo sapiens", "")
    assert res["canonical_id"] != "UniProtKB:O95256"


def test_trailing_symbol_names_the_gene():
    assert E._strip_gene_suffix("cardiac developmental transcription factors GATA4") == "GATA4"
    assert E._strip_gene_suffix("Toll-like receptor TLR3") == "TLR3"
    for name in ("protein kinase C", "vitamin D3", "cyclin D1", "type 2 diabetes"):
        assert E._strip_gene_suffix(name) is None


def test_alias_shared_by_genes_is_ambiguous(monkeypatch):
    class R:
        def json(self):
            entry = lambda acc, gene, syns: {"primaryAccession": acc, "genes": [{"geneName": {"value": gene},
                                             "synonyms": [{"value": x} for x in syns]}]}
            return {"results": [entry("Q14534", "SQLE", ["ERG1"]), entry("Q12809", "KCNH2", ["ERG", "ERG1"])]}
    monkeypatch.setattr(E, "_http_get", lambda *a, **k: R())
    assert E._uniprot_search("ERG1", "Homo sapiens") is None
    assert E._uniprot_search("SQLE", "Homo sapiens")[0] == "UniProtKB:Q14534"


def test_anatomy_ontology_scope_from_registry_and_lineage(monkeypatch):
    monkeypatch.setattr(E, "_anatomy_scope", {"fbbt": "7227", "uberon": "33208"})      # registry: fly, Metazoa
    lineages = {"homo sapiens": {"9606", "9605", "40674", "33208", "1"},
                "drosophila melanogaster": {"7227", "7215", "33208", "1"}}
    monkeypatch.setattr(E, "_lineage", lambda sp: lineages.get(sp.lower(), set()))
    assert E._organism_conflict("FBbt:00058784", "Homo sapiens")            # fly anatomy, human paper
    assert not E._organism_conflict("FBbt:00058784", "Drosophila melanogaster")
    assert not E._organism_conflict("UBERON:0002018", "Homo sapiens")        # all animals
    assert not E._organism_conflict("HP:0001369", "Rattus norvegicus")       # phenotypes: not anatomy, shared


def test_kind_conflicts():
    assert E._kind_conflict("PROTEIN", "GO:0000981")
    assert not E._kind_conflict("MACROMOLECULAR_COMPLEX", "GO:0071159")


def test_fact_organism_from_its_sentences():
    text = ("Human RA-FLS were stimulated with IL-1β, which induced IL6. "
            "In rats with arthritis, PIA elevated IL1B. Mice and rats were compared for TNF and IL1B.")
    mentions = ["Human RA-FLS", "rats", "Mice"]                             # Layer 4 organism mentions
    assert E._sentence_organism(text, "IL-1β", "IL6", mentions) == "Homo sapiens"
    assert E._sentence_organism(text, "PIA", "IL1B", mentions) == "Rattus norvegicus"
    assert E._sentence_organism(text, "TNF", "IL1B", mentions) == ""        # two organisms named: unknown


def test_organism_must_be_named_in_the_text():
    text = "Cardiac fibroblasts were accessible at GATA4 and TBX20 (Perrino and Rockman, 2006)."
    assert not E._organism_named("Mus musculus", text)
    assert E._organism_named("Mus musculus", "Enhancers were tested in transgenic mice.")
    assert E._organism_named("Homo sapiens", "Human RA-FLS were stimulated.")
