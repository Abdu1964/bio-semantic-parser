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
    res = E._normalize_entity_core("IL-1β", "PROTEIN", None, "Homo sapiens", "")
    assert res["canonical_id"] != "UniProtKB:O95256"
