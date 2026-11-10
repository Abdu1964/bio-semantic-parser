"""Sentence splitting shared by all layers."""
import re
import string

# End punctuation, with any citation numbers glued to it ("worldwide.1–5 Despite"), then space.
_END = re.compile(r"[.!?](?:\d+(?:[,–-]\d+)*(?=\s+[A-Z]))?\s+")


def split_sentences(text: str) -> list:
    """Sentences of text, each keeping its end punctuation and citation numbers.

    A period after a single letter (C. elegans, E. coli, initials, e.g.) or before a number
    (Fig. 2, vs. 0.41) does not end a sentence; nor does a decimal point (0.05 Then).
    """
    sentences, start = [], 0
    for m in _END.finditer(text):
        i = m.start()
        if m.end() < len(text) and text[m.end()].isdigit():
            continue
        if text[i] == "." and i and text[i - 1] in string.ascii_letters and (i < 2 or not text[i - 2].isalnum()):
            continue
        if m.group(0).rstrip()[1:] and not (i and (text[i - 1].isalpha() or text[i - 1] == ")")):
            continue
        sentences.append(text[start:m.start() + len(m.group(0).rstrip())])
        start = m.end()
    sentences.append(text[start:])
    return sentences
