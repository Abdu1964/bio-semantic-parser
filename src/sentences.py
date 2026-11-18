"""Sentence splitting shared by all layers."""
import re
import string

# End punctuation, with any citation numbers glued to it ("worldwide.1–5 Despite"), then space.
_END = re.compile(r"[.!?](?:\d+(?:[,–-]\d+)*(?=\s+[A-Z]))?\s+")


def split_sentences(text: str) -> list:
    """Sentences of text, each keeping its end punctuation and citation numbers.

    A period after a single letter does not end a sentence before a lowercase word (C. elegans, E. coli)
    in an abbreviation (e.g., i.e.) or a run of initials (M. E. Selsted), but does before a capital ('vitamin D. FOXO3'); nor does a period before a number
    (Fig. 2, vs. 0.41) does not end a sentence; nor does a decimal point (0.05 Then).
    """
    sentences, start = [], 0
    for m in _END.finditer(text):
        i = m.start()
        if m.end() < len(text) and text[m.end()].isdigit():
            continue
        if text[i] == "." and i and text[i - 1] in string.ascii_letters and (i < 2 or not text[i - 2].isalnum()):
            initials = re.match(r"[A-Z]\.\s", text[m.end():m.end() + 3]) or re.search(r"(?:^|\s)[A-Z]\.\s*$", text[:i - 1])
            if (i >= 2 and text[i - 2] == ".") or initials or not text[m.end():m.end() + 1].isupper():
                continue                         # 'e.g.', 'M. E. Selsted', 'C. elegans'; 'vitamin D. FOXO3' ends one
        if m.group(0).rstrip()[1:] and not (i and (text[i - 1].isalpha() or text[i - 1] == ")")):
            continue
        sentences.append(text[start:m.start() + len(m.group(0).rstrip())])
        start = m.end()
    sentences.append(text[start:])
    return sentences
