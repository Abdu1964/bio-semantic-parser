"""Sentence splitting shared by all layers."""
import re

# A period after a single letter (C. elegans, E. coli, initials, e.g.) or before a number
# (Fig. 2, vs. 0.41) does not end a sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])(?<!\b[A-Za-z]\.)\s+(?![0-9])")


def split_sentences(text: str) -> list:
    """Sentences of text, each keeping its end punctuation."""
    return _SENTENCE_END.split(text)
