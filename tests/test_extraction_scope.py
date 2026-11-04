"""Numbered lists become members; cited/predicted values; validator sees a fact's scope."""
from src.extraction.extractor import _expand_lists, _members
from src.postextraction.semantic_validation import _scope
from src.schema.pydantic_model import BiologicalRelation


def _rel(subject, obj, **kw):
    return BiologicalRelation(extraction_viable=True, subject_name=subject, relation="upregulates",
                              object_name=obj, reasoning="the text states this change explicitly in the results section here", **kw)


def test_members():
    assert _members("MMPs 3, 8, 9, 10, and 13") == ["MMP3", "MMP8", "MMP9", "MMP10", "MMP13"]
    assert _members("TLR 3 and 9") == ["TLR3", "TLR9"]
    for name in ("COVID 19", "Figure 5", "stages 1 and 2", "HLA-B 27"):
        assert _members(name) == [name]


def test_list_becomes_one_relation_per_member():
    out = _expand_lists([_rel("PIA", "MMPs 3 and 13"), _rel("PIA", "IL6")])
    assert [r.object_name for r in out] == ["MMP3", "MMP13", "IL6"]


def test_source_and_evidence_values():
    r = _rel("RTD-1", "TNF", source="<cited — only for earlier work>", evidence="predicted")
    assert (r.source, r.evidence) == ("cited", "predicted")
    assert (_rel("RTD-1", "TNF").source, _rel("RTD-1", "TNF").evidence) == ("study", "measured")


def test_scope_shown_to_validator():
    assert _scope({"condition": "1 day", "tissue": "synovium", "source": "cited"}) == \
        "1 day; synovium; cited from earlier work"
    assert _scope({}) == "none stated"


def test_negation_pass_needs_a_negating_sentence():
    from src.extraction.extractor import _text_negates
    chunk = {"text": "EMX2 was enriched in astrocytes. GAPDH was unaffected by IL-1β stimulation."}
    assert not _text_negates(_rel("EMX2", "astrocytes", negated=True), chunk)
    assert _text_negates(_rel("IL-1β", "GAPDH", negated=True), chunk)
    assert _text_negates(_rel("TREM2", "microglia", negated=True), chunk)   # no sentence names both: kept


def test_slash_lists():
    assert _members("NFKB1/2") == ["NFKB1", "NFKB2"]
    assert _members("SOX9/13") == ["SOX9", "SOX13"]
    for name in ("SREBP1/SREBF1", "PI3K/AKT"):
        assert _members(name) == [name]


def test_comparison_and_background_rules_in_prompt():
    from src.extraction.extractor import _system_prompt
    prompt = _system_prompt()
    assert "A comparison is a directional finding" in prompt and "Skip textbook" in prompt


def test_main_pass_negation_without_negating_text_is_lifted(monkeypatch):
    from src.extraction import extractor as X
    from src.schema.pydantic_model import ExtractionResult
    chunk = {"text": "We found EMX2 and NFIA as regulators in astrocytes. GAPDH was unaffected by IL-1β."}
    monkeypatch.setattr(X, "_NEGATION_PASS", False)
    monkeypatch.setattr(X, "_extract_main", lambda c, d=0: ExtractionResult(relations=[
        _rel("EMX2", "astrocytes", negated=True), _rel("IL-1β", "GAPDH", negated=True)]))
    out = X.extract(chunk).relations
    assert [r.negated for r in out] == [False, True]
