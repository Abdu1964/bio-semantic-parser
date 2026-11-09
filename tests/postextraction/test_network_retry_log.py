"""Lookups that fail on the network are retried, and the run log says so (brief and detailed)."""
from src.postextraction import entity_normalization as E


def test_retry_rounds_are_reported(monkeypatch):
    calls = {"n": 0}

    def fake_normalize(record, chunk):
        calls["n"] += 1
        failed = calls["n"] <= 2                                   # first pass fails, retry succeeds
        return {**record, "subject_network_degraded": failed, "object_network_degraded": False}

    monkeypatch.setattr(E, "normalize_record", fake_normalize)
    monkeypatch.setattr(E, "merge_synonym_concepts", lambda out: out)
    monkeypatch.setattr(E, "_NETWORK_RETRY_WAIT", 0)
    E._failed_requests["rest.ensembl.org"] = 4
    messages = []
    records = [{"subject_name": "TGFB1", "object_name": "RA"}, {"subject_name": "IL6", "object_name": "RA"}]
    E.normalize_records_parallel(records, lambda r: {}, workers=1, notify=messages.append)
    text = "\n".join(messages)
    assert "2 record(s) hit network failures" in text and "retry 1/3" in text
    assert "waiting: IL6, TGFB1" in text
    assert "Retry 1/3: 2 resolved, 0 still failing" in text
    assert "All lookups resolved after retry 1/3" in text


def test_local_ensembl_symbol_without_hyphen(monkeypatch):
    from src import ensembl_local
    genes = {("il6", "rattus_norvegicus"): [("ENSRNOG00000010278", "Il6")]}
    monkeypatch.setattr(ensembl_local, "has_species", lambda s: True)
    monkeypatch.setattr(ensembl_local, "symbol", lambda n, s: genes.get((n.lower(), s), []))
    assert E._ensembl_local_gene(["IL-6"], "rattus_norvegicus") == ("ENSEMBL:ENSRNOG00000010278", "Il6", "")
    assert E._ensembl_local_gene(["NF-κB"], "rattus_norvegicus") is None


def test_molecule_form_stripped_only_from_symbols():
    assert E._strip_gene_suffix("PTGS2 mRNAs") == "PTGS2"
    assert E._strip_gene_suffix("IL-1β protein") == "IL-1β"
    assert E._strip_gene_suffix("C-reactive protein") is None
