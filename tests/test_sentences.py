import pytest

from src.sentences import split_sentences


@pytest.mark.parametrize("text", [
    "daf-2 loss extended lifespan in C. elegans and E. coli fed worms.",
    "Correspondence: M. E. Selsted, Department of Pathology.",
    "Cytokines, e.g. IL6, were measured.",
    "IL-6 was reduced (Fig. 5A) in treated rats.",
    "Mean PCC was higher (0.422 vs. 0.414) for MSGR.",
])
def test_no_split_inside_a_sentence(text):
    assert split_sentences(text) == [text]


def test_splits_real_sentence_ends():
    text = "GAPDH was unaffected. TNF was elevated! Why? (E) Enriched motifs are shown."
    assert split_sentences(text) == ["GAPDH was unaffected.", "TNF was elevated!", "Why?",
                                     "(E) Enriched motifs are shown."]


def test_citation_numbers_stay_with_their_sentence():
    text = "AD affects millions worldwide.1–5 Despite this, risk genes are unknown.8,11 We profiled cells."
    assert split_sentences(text) == ["AD affects millions worldwide.1–5", "Despite this, risk genes are unknown.8,11",
                                     "We profiled cells."]


def test_decimal_is_not_a_citation():
    assert split_sentences("Expression rose at p < 0.05 Then it fell.") == ["Expression rose at p < 0.05 Then it fell."]


def test_chunker_keeps_text_and_species_names(monkeypatch):
    from src.fetcher.chunker import Chunker
    chunker = Chunker()
    chunker.max_tokens, chunker.overlap_sentences = 12, 0
    text = "Lifespan was extended in C. elegans mutants. Insulin signalling was reduced. Fat stores rose."
    chunks = [c["text"] for c in chunker.chunk_section({"section": "results", "text": text})]
    assert len(chunks) > 1
    assert " ".join(chunks) == text
    assert any("C. elegans mutants." in c for c in chunks)


def test_split_chunk_halves_keep_text():
    from src.extraction.extractor import _split_chunk
    text = "One in C. elegans. Two. Three. Four."
    halves = _split_chunk({"text": text, "section": "results"})
    assert [h["text"] for h in halves] == ["One in C. elegans. Two.", "Three. Four."]
