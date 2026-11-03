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
