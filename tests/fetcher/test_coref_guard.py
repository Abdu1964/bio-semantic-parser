"""The coreference guard keeps pronoun resolutions and refuses rewrites of names and phrases."""
import pytest

from src.fetcher import coref_guard

TAGS = {"they": "PRP", "it": "PRP", "we": "PRP", "their": "PRP$", "our": "PRP$", "these": "DT", "this": "DT"}


def _tag(w):
    w = w.strip(".,;:()").lower()
    return "CD" if w.replace(".", "").isdigit() else TAGS.get(w, "NN")


@pytest.fixture(autouse=True)
def fake_tagger(monkeypatch):
    monkeypatch.setattr(coref_guard, "_word_tags", lambda text, words: [_tag(w) for w in words])


def test_pronoun_or_number_replacement_refused():
    for rewritten in ("Results show they matter.", "Results show 262.3 matter."):
        out, kept, _ = coref_guard.guard("Results show we matter.", rewritten)
        assert out == "Results show we matter." and kept == 0


def test_pronoun_resolution_kept():
    out, kept, refused = coref_guard.guard("RTD-1 is potent because they bind LPS.",
                                           "RTD-1 is potent because θ-defensins bind LPS.")
    assert out == "RTD-1 is potent because θ-defensins bind LPS." and (kept, refused) == (1, 0)


def test_name_rewrite_refused():
    original = "RTD-1 inhibited TNF, IL1, and IL6."
    out, kept, refused = coref_guard.guard(original, "RTD-1 inhibited TNF, IL1B, and IL6.")
    assert out == original and (kept, refused) == (0, 1)


def test_phrase_rewrite_refused_pronoun_kept():
    original = "RTD-1 inhibited each of these mRNAs in FLS cells. Later it was potent."
    rewritten = "RTD-1 inhibited each of the 107 disease genes in FLS cells. Later RTD-1 was potent."
    out, kept, refused = coref_guard.guard(original, rewritten)
    assert "these mRNAs" in out and "Later RTD-1 was potent" in out and (kept, refused) == (1, 1)


def test_pronoun_next_to_phrase_rewrite_refused_together():
    original = "RTD-1 inhibited these mRNAs. it was potent."
    out, kept, _ = coref_guard.guard(original, "RTD-1 inhibited the 107 disease genes. RTD-1 was potent.")
    assert out == original and kept == 0          # merged change: the paper's words win


def test_possessive_needs_possessive_form():
    out, _, _ = coref_guard.guard("Genes disclosed their association.", "Genes disclosed the genes association.")
    assert out == "Genes disclosed their association."
    out, kept, _ = coref_guard.guard("Genes disclosed their association.", "Genes disclosed the genes' association.")
    assert kept == 1 and "the genes' association" in out


def test_no_change_returns_original():
    assert coref_guard.guard("Same text.", "Same text.") == ("Same text.", 0, 0)
