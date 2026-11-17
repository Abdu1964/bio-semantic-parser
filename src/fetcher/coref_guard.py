"""Layer 3 — Coreference guard: keep only the coreference rewrites that are safe.

The coreference service returns the whole text rewritten. Replacing a pronoun with what it
refers to ("they" → "θ-defensins") helps extraction; rewriting names or phrases ("IL1" → "IL1B",
"these mRNAs" → "the 107 disease genes") changes facts. Each change is kept only when the original
word is a personal pronoun (POS tag PRP), or a possessive (PRP$) replaced by a possessive form;
everything else keeps the paper's own words.
"""
import difflib
import logging
import os
import threading

logger = logging.getLogger(__name__)

_MODEL = os.getenv("COREF_GUARD_MODEL", "en_core_sci_lg")
_nlp = None
_nlp_lock = threading.Lock()


def _tagger():
    global _nlp
    with _nlp_lock:
        if _nlp is None:
            import spacy
            _nlp = spacy.load(_MODEL, disable=["ner", "parser", "lemmatizer"])
            _nlp.max_length = max(_nlp.max_length, 5_000_000)
        return _nlp


def _word_tags(text: str, words: list) -> list:
    """POS tag of each whitespace-separated word (the tag of its first alphabetic token)."""
    doc = _tagger()(text)
    by_start = {t.idx: t.tag_ for t in doc}
    tags, pos = [], 0
    for w in words:
        start = text.index(w, pos)
        pos = start + len(w)
        lead = next((i for i, c in enumerate(w) if c.isalnum()), 0)   # skip "(" or quotes
        tags.append(by_start.get(start + lead, ""))
    return tags


_NOT_A_REFERENT = {"PRP", "PRP$", "CD"}        # a pronoun or a number names nothing new


def _safe(original: list, tags: list, replacement: list, new_tags: list) -> bool:
    """One pronoun replaced by a noun phrase; possessives only by a possessive form."""
    if len(original) != 1 or not replacement or all(t in _NOT_A_REFERENT for t in new_tags):
        return False
    tag, new = tags[0], " ".join(replacement)
    if tag == "PRP":
        return True
    if tag == "PRP$":
        return new.rstrip(".,;:)").endswith(("'s", "’s", "s'", "s’"))
    return False


def guard(original: str, rewritten: str) -> tuple:
    """(text with only safe rewrites applied, accepted count, rejected count)."""
    if not rewritten or rewritten == original:
        return original, 0, 0
    a, b = original.split(), rewritten.split()
    try:
        tags, new_tags = _word_tags(original, a), _word_tags(rewritten, b)
    except Exception as e:                       # no tagger: keep the paper's words
        logger.warning("Coreference guard unavailable (%s) — keeping the original text", e)
        return original, 0, 0
    out, accepted, rejected = [], 0, 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out += a[i1:i2]
        elif op == "replace" and _safe(a[i1:i2], tags[i1:i2], b[j1:j2], new_tags[j1:j2]):
            out += b[j1:j2]
            accepted += 1
        else:
            out += a[i1:i2]
            rejected += 1
    return (" ".join(out) if accepted else original), accepted, rejected   # cleaned text is single-spaced
