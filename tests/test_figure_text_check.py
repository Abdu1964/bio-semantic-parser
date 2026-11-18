"""Figure facts are checked against the paper's own sentences about their entities."""
from src.figures.vision import text_check as T


def _fig(subject, obj):
    return {"section": "figure", "subject_name": subject, "relation": "associates_with", "object_name": obj}


def test_evidence_takes_sentences_about_either_entity():
    sentences = ["Schizophrenia and bipolar disorder were enriched in neuronal epigenomes.",
                 "Microglia were enriched for immune-related traits.", "Unrelated text about methods."]
    assert T._evidence(_fig("Schizophrenia", "Microglia"), sentences) == sentences[:2]


def test_contradicted_figure_fact_is_held_back(monkeypatch):
    monkeypatch.setattr(T, "ENABLED", True)
    monkeypatch.setattr(T, "call_llm", lambda *a, **k: "1: CONTRADICT\n2: SUPPORT")
    recs = [_fig("Schizophrenia", "Microglia"), _fig("SPI1", "Microglia")]
    text = "Schizophrenia was enriched in neuronal epigenomes. SPI1 is a microglial regulator in microglia."
    st = T.check(recs, text)
    assert st["contradicted"] == 1 and recs[0]["is_contradiction"] and not recs[1].get("is_contradiction")


def test_text_facts_and_unrelated_figures_untouched(monkeypatch):
    monkeypatch.setattr(T, "ENABLED", True)
    recs = [{"section": "results", "subject_name": "A", "relation": "x", "object_name": "B"}, _fig("Q", "Z")]
    assert T.check(recs, "Nothing about these entities here.")["checked"] == 0
