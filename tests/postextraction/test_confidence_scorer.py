"""Confidence: definite negative findings score like definite positive ones; hedges score lower."""
import pytest

from src.postextraction import certainty, confidence_scorer as cs
from src.schema.taxonomy import RelationType

REAL_LEVEL = certainty.level

PAIRS = [   # (positive, negative) with the same certainty
    ("SIRT1 overexpression upregulated FOXO3 in HEK293 cells.",
     "SIRT1 overexpression did not upregulate FOXO3 in HEK293 cells."),
    ("daf-2 loss extended lifespan in worms.",
     "daf-2 loss failed to extend lifespan in worms."),
    ("We found evidence that IL6 increases CRP.",
     "We found no evidence that IL6 increases CRP."),
]


@pytest.fixture(autouse=True)
def judged(monkeypatch):
    """Certainty as the LLM would judge these test sentences (the scorer only reads the judged level)."""
    from src.postextraction import certainty
    label = lambda s: "DOUBTFUL" if "unlikely" in s else "POSSIBLE" if " may " in f" {s} " else "FACT"
    monkeypatch.setattr(certainty, "level", lambda s: certainty._VALUE[label(s or "")])


@pytest.fixture
def nli_supports(monkeypatch):
    """NLI stub: the premise supports the hypothesis (same answer for either polarity)."""
    monkeypatch.setattr(cs, "_nli_score", lambda premise, hypothesis: (0.02, 0.90, 0.08))


@pytest.mark.parametrize("positive,negative", PAIRS)
def test_negation_is_not_doubt(positive, negative):
    assert cs._channel_factuality(negative, negative.lower()) == cs._channel_factuality(positive, positive.lower())


def test_hedge_lowers_certainty_for_both_polarities():
    def c2(s):
        return cs._channel_factuality(s, s.lower())
    assert c2("IL6 may not increase CRP.") < c2("IL6 did not increase CRP.")
    assert c2("IL6 may increase CRP.") < c2("IL6 increased CRP, as we showed.")


def test_hypothesis_is_plain_words():
    assert cs._hypothesis("SIRT1", RelationType.UPREGULATES, "FOXO3") == "SIRT1 upregulates FOXO3"
    assert cs._hypothesis("TP53", "associates_with", "survival") == "TP53 associates with survival"
    assert cs._hypothesis("SIRT1", "upregulates", "FOXO3", negated=True) == \
        "It is not true that SIRT1 upregulates FOXO3."


def test_premise_matches_fact_polarity(nli_supports):
    pos, neg = PAIRS[0]
    chunk = f"{pos} {neg}"
    hyp = cs._hypothesis("SIRT1", "upregulates", "FOXO3")
    assert cs._best_sentence(chunk, "sirt1", "foxo3", relation_hypothesis=hyp) == pos
    assert cs._best_sentence(chunk, "sirt1", "foxo3", relation_hypothesis=hyp, negated=True) == neg


def test_negative_without_listed_word_is_kept(monkeypatch):
    """"unaffected" is not a listed negation word; the sentence must still reach NLI ranking."""
    neg = "GAPDH was unaffected by IL-1β stimulation."
    chunk = f"We tested whether GAPDH is affected by IL-1β. {neg}"
    monkeypatch.setattr(cs, "_nli_score",
                        lambda p, h: (0.02, 0.90, 0.08) if "unaffected" in p else (0.30, 0.01, 0.69))
    hyp = cs._hypothesis("IL-1β", "upregulates", "GAPDH", negated=True)
    assert cs._best_sentence(chunk, "il-1β", "gapdh", relation_hypothesis=hyp, negated=True) == neg


def test_species_abbreviation_does_not_end_sentence():
    text = "daf-2 loss extended lifespan in C. elegans. Insulin signalling was reduced."
    assert cs._split_sentences(text) == ["daf-2 loss extended lifespan in C. elegans.",
                                         "Insulin signalling was reduced."]


def test_hedged_sentence_limits_c5(nli_supports):
    hedged = cs.explain("IL6", "CRP", True, 0.9, "results", "IL6 may not increase CRP.", relation="increases")
    definite = cs.explain("IL6", "CRP", True, 0.9, "results", "IL6 did not increase CRP.", relation="increases")
    assert hedged["channels"]["C5_nli_entailment"] <= 0.50 < definite["channels"]["C5_nli_entailment"]


@pytest.mark.parametrize("sentence,limit", [("IL6 may increase CRP (p < 0.01).", 0.50),
                                            ("It is unlikely that IL6 increases CRP (p < 0.01).", 0.20)])
def test_hedged_fact_final_score_capped(nli_supports, sentence, limit):
    hedged = cs.explain("IL6", "CRP", False, 0.9, "results", sentence, relation="increases")
    definite = cs.explain("IL6", "CRP", False, 0.9, "results", "IL6 increased CRP (p < 0.01).", relation="increases")
    assert hedged["final_score"] <= limit < definite["final_score"]
    assert cs.score("IL6", "CRP", False, 0.9, "results", sentence, relation="increases") <= limit


@pytest.mark.parametrize("positive,negative", PAIRS)
def test_negative_finding_scores_like_positive(nli_supports, positive, negative):
    subj, obj = ("SIRT1", "FOXO3") if "SIRT1" in positive else \
                ("daf-2 loss", "lifespan") if "daf-2" in positive else ("IL6", "CRP")
    pos = cs.score(subj, obj, False, 0.9, "results", positive, relation="increases")
    neg = cs.score(subj, obj, True, 0.9, "results", negative, relation="increases")
    assert neg > 0.65                                        # no cap on negated facts
    assert abs(pos - neg) <= 0.02


def test_certainty_judged_in_batches_and_neutral_otherwise(monkeypatch):
    monkeypatch.setattr(certainty, "level", REAL_LEVEL)
    monkeypatch.setattr(certainty, "_cache", {})
    monkeypatch.setattr(certainty, "call_llm", lambda msgs, **k: "1: POSSIBLE\n2: FACT")
    assert certainty.prefetch(["IL6 may increase CRP.", "IL6 increased CRP.", ""]) == 2
    assert certainty.level("IL6 may increase CRP.") == 0.50 and certainty.level("IL6 increased CRP.") == 0.95
    assert certainty.level("Never judged.") == certainty.NEUTRAL


def test_unreachable_llm_caps_nothing(monkeypatch):
    monkeypatch.setattr(certainty, "level", REAL_LEVEL)
    monkeypatch.setattr(certainty, "_cache", {})
    def down(*a, **k):
        raise ConnectionError("LLM down")
    monkeypatch.setattr(certainty, "call_llm", down)
    assert certainty.prefetch(["IL6 may increase CRP."]) == 0
    assert certainty.level("IL6 may increase CRP.") == certainty.NEUTRAL
