"""The splitter must never drop text: every word of the input ends up in a section."""
import re

from src.fetcher.section_splitter import SectionSplitter

HEADERS = {"abstract", "introduction", "background", "methods", "method", "results", "result",
           "discussion", "conclusion", "conclusions", "supplementary", "supplemental", "findings",
           "experimental", "materials", "and"}


def _words(text):
    return [w for w in re.findall(r"\w+", text.lower()) if w not in HEADERS]


def _assert_lossless(text):
    sections = SectionSplitter().split(text)
    assert _words(" ".join(s["text"] for s in sections)) == _words(text)
    return [s["section"] for s in sections]


def test_unlabelled_abstract_and_inline_caps_header_kept():
    # PDF text flattened to one line; abstract has no header; INTRODUCTION follows a keyword line
    text = ("RTD-1 normalizes synovial gene signatures. Smith J, Lee K. RTD-1 is therapeutic in PIA. "
            "defensin; arthritis; RNA-Seq INTRODUCTION Rheumatoid arthritis is an autoimmune disease. "
            "METHODS Rats were treated with RTD-1. RESULTS RTD-1 suppressed IL6. "
            "DISCUSSION RTD-1 activates PPARG.")
    assert _assert_lossless(text) == ["abstract", "introduction", "methods", "results", "discussion"]


def test_caps_header_wins_over_sentence_starting_with_section_word():
    text = ("Aging is complex. Results from earlier work vary. METHODS Mice were treated. "
            "RESULTS Lifespan increased.")
    sections = SectionSplitter().split(text)
    results = next(s for s in sections if s["section"] == "results")
    assert results["text"] == "Lifespan increased."
    _assert_lossless(text)


def test_text_before_first_header_line_kept():
    text = "A study of mTOR\nJ. Smith\nRapamycin extends lifespan.\nIntroduction\nAging is complex.\nMethods\nMice were treated."
    assert _assert_lossless(text) == ["abstract", "introduction", "methods"]


def test_text_before_abstract_header_joins_abstract():
    text = "A study of mTOR\nAbstract\nRapamycin extends lifespan.\nMethods\nMice were treated."
    assert _assert_lossless(text) == ["abstract", "methods"]


def test_no_headers_is_one_abstract():
    assert _assert_lossless("Rapamycin inhibits mTOR. It extends lifespan in mice.") == ["abstract"]
