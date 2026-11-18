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
    assert "A comparison is a directional finding" in prompt and "every specific result it cites" in prompt
    assert "never stop part-way" in prompt


def test_main_pass_negation_without_negating_text_is_lifted(monkeypatch):
    from src.extraction import extractor as X
    from src.schema.pydantic_model import ExtractionResult
    chunk = {"text": "We found EMX2 and NFIA as regulators in astrocytes. GAPDH was unaffected by IL-1β."}
    monkeypatch.setattr(X, "_NEGATION_PASS", False)
    monkeypatch.setattr(X, "_extract_main", lambda c, d=0: ExtractionResult(relations=[
        _rel("EMX2", "astrocytes", negated=True), _rel("IL-1β", "GAPDH", negated=True)]))
    out = X.extract(chunk).relations
    assert [r.negated for r in out] == [False, True]


def test_list_completed_from_the_sentence():
    from src.extraction.extractor import _complete_lists
    text = ("Regulators IL6, IL1, TNF, IL17A and signaling factors STAT3, AP1, and RELA were activated in PIA. "
            "IL6 rose in PIA, whereas TNF was unchanged. RTD-1 blocks IL6 in monocytes and suppresses TNF and IL1.")
    chunk = {"text": text, "entities": [{"text": n} for n in ("IL6", "IL1", "TNF", "IL17A", "STAT3", "AP1", "RELA", "PIA",
                                                         "RTD-1", "monocytes")]}
    out = _complete_lists([_rel("PIA", "IL6"), _rel("PIA", "TNF")], chunk)
    assert sorted(r.object_name for r in out) == ["AP1", "IL1", "IL17A", "IL6", "RELA", "STAT3", "TNF"]
    assert all(r.subject_name == "PIA" for r in out)


def test_list_needs_its_start_or_half_covered():
    from src.extraction.extractor import _complete_lists
    chunk = {"text": "In PIA, targets A1, B2, C3, D4, E5, F6 and G7 were reversed.",
             "entities": [{"text": n} for n in ("PIA", "A1", "B2", "C3", "D4", "E5", "F6", "G7")]}
    assert len(_complete_lists([_rel("PIA", "D4"), _rel("PIA", "F6")], chunk)) == 2


def test_two_names_are_not_a_list():
    from src.extraction.extractor import _complete_lists
    chunk = {"text": "IL6 rose in PIA and TNF in RA.", "entities": [{"text": n} for n in ("IL6", "TNF", "PIA", "RA")]}
    assert len(_complete_lists([_rel("PIA", "IL6")], chunk)) == 1


def test_sentences_without_a_finding_are_found():
    from src.extraction.extractor import _uncovered
    chunk = {"text": "IL6 rose in PIA. RTD-1 lowered TNF and IL1B in PIA. Samples were stored at -80 C.",
             "entities": [{"text": n} for n in ("IL6", "PIA", "RTD-1", "TNF", "IL1B")]}
    assert _uncovered(chunk, [_rel("PIA", "IL6")]) == ["RTD-1 lowered TNF and IL1B in PIA."]


def test_coverage_pass_skips_methods_and_keeps_grounded_findings(monkeypatch):
    import src.extraction.extractor as ex
    chunk = {"text": "RTD-1 lowered TNF in PIA.", "section": "results",
             "entities": [{"text": n} for n in ("PIA", "RTD-1", "TNF")]}
    monkeypatch.setattr(ex, "_second_pass", lambda user, name: [_rel("RTD-1", "TNF"), _rel("RTD-1", "MMP3")])
    assert [r.object_name for r in ex._missed_findings(chunk, [])] == ["TNF"]
    assert ex._missed_findings({**chunk, "section": "Materials and Methods"}, []) == []


def test_untagged_names_must_be_in_the_text():
    from src.extraction.extractor import _grounded
    chunk = {"text": "TSH-associated loci were enriched in thyroid follicular cells.", "entities": [{"text": "TSH"}]}
    out = _grounded([_rel("TSH", "thyroid follicular cells"), _rel("TSH", "thyrocytes")], chunk)
    assert [r.object_name for r in out] == ["thyroid follicular cells"]
