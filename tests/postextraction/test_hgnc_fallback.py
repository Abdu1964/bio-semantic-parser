"""HGNC fallback: the first field that knows a symbol decides; ambiguity gives no ID."""
from src.postextraction import entity_normalization as en


class _Resp:
    def __init__(self, docs):
        self._docs = docs

    def json(self):
        return {"response": {"docs": self._docs}}


def _fake(answers):
    def get(url, **kw):
        field = url.split("/fetch/")[1].split("/")[0]
        return _Resp(answers.get(field, []))
    return get


def test_ambiguous_previous_symbol_is_not_resolved_by_alias(monkeypatch):
    monkeypatch.setattr(en, "_http_get", _fake({
        "prev_symbol": [{"symbol": "A1", "ensembl_gene_id": "ENSG1"}, {"symbol": "A2", "ensembl_gene_id": "ENSG2"}],
        "alias_symbol": [{"symbol": "A3", "ensembl_gene_id": "ENSG3"}]}))
    assert en._hgnc_gene("OLD1") is None


def test_single_match_resolves(monkeypatch):
    monkeypatch.setattr(en, "_http_get", _fake({"alias_symbol": [{"symbol": "TP53", "ensembl_gene_id": "ENSG00000141510"}]}))
    assert en._hgnc_gene("p53") == ("ENSEMBL:ENSG00000141510", "TP53", "")
