"""Checks whether a looked-up label really names the queried entity: exact / close / none."""
from __future__ import annotations

import difflib
import re
import unicodedata

EXACT, CLOSE, NONE = "exact", "close", "none"

_STOPWORDS = {"a", "an", "the", "of", "in", "and", "or", "to", "for", "with", "at", "on", "by"}

# British → American spellings that are common in biomedical text.
_SPELLING_VARIANTS = [
    (r"\bageing\b", "aging"),
    (r"tumour", "tumor"),
    (r"\bhaem", "hem"),
    (r"\boedema\b", "edema"),
    (r"\boestr", "estr"),
    (r"\bfoetal\b", "fetal"),
    (r"\bfoetus\b", "fetus"),
    (r"isation\b", "ization"),
    (r"\banaemi", "anemi"),
    (r"\bleukaemi", "leukemi"),
    (r"\bdiarrhoea\b", "diarrhea"),
    (r"\bbehaviour", "behavior"),
    (r"\bcolour", "color"),
]

# Plural forms that don't follow simple -s / -es rules.
_IRREGULAR_PLURALS = {
    "mice": "mouse", "children": "child", "women": "woman", "men": "man",
    "teeth": "tooth", "feet": "foot", "geese": "goose", "people": "person",
    "data": "datum", "criteria": "criterion", "phenomena": "phenomenon",
    "nuclei": "nucleus", "loci": "locus", "fungi": "fungus", "bacteria": "bacterium",
    "larvae": "larva", "pupae": "pupa", "vertebrae": "vertebra", "indices": "index",
    "matrices": "matrix", "analyses": "analysis", "syntheses": "synthesis",
}

# Words where trailing "s" is not a plural (avoid "sepsis" → "sepsi").
_NOT_PLURAL_SUFFIXES = ("ss", "us", "is", "ys", "os")


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# Greek letters spelled out, so ERα ≠ ERβ and TNF-α == TNF alpha.
_GREEK = dict(zip("αβγδεζηθικλμνξοπρστυφχψωϵς",
                  "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho "
                  "sigma tau upsilon phi chi psi omega epsilon sigma".split()))


def normalize_term(text: str) -> str:
    """Lowercase, ASCII, US spelling, punctuation stripped."""
    if not text:
        return ""
    t = _strip_accents(text).lower()
    t = "".join(f" {_GREEK[c]} " if c in _GREEK else c for c in t)
    t = t.replace("’", "'").replace("‘", "'")
    t = re.sub(r"'s\b", "", t)                 # possessive: "alzheimer's" → "alzheimer"
    for pattern, repl in _SPELLING_VARIANTS:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"[^a-z0-9.\s]", " ", t)          # keep "." for genus abbreviations
    t = re.sub(r"(?<![a-z])\.|\.(?![a-z])", " ", t)  # drop dots that aren't inside a word
    return re.sub(r"\s+", " ", t).strip()


def singularize(word: str) -> str:
    """Conservative English singularization for biomedical nouns."""
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if len(word) <= 3 or not word.endswith("s") or word.endswith(_NOT_PLURAL_SUFFIXES):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "xes", "zes", "sses")):
        return word[:-2]
    return word[:-1]


def tokens(text: str) -> list:
    """Content tokens of a term: normalized, singularized, stopwords removed."""
    out = []
    for tok in normalize_term(text).split():
        tok = tok.rstrip(".")
        if not tok or tok in _STOPWORDS:
            continue
        out.append(singularize(tok))
    return out


def term_key(text: str) -> str:
    """Order-independent key: 'Drosophila populations' == 'population of Drosophila'."""
    return " ".join(sorted(tokens(text)))


_GENUS_ABBREV = re.compile(r"^[A-Za-z]{1,3}\.$")


def _abbreviation_match(query: str, label: str) -> bool:
    """Organism binomials only: 'D. melanogaster' ↔ 'Drosophila melanogaster' (never 'T cells' ↔ 'tumor cells')."""
    raw = query.strip().split()
    q, l = normalize_term(query).split(), normalize_term(label).split()
    if len(raw) != 2 or len(q) != 2 or len(l) != 2 or not _GENUS_ABBREV.match(raw[0]):
        return False
    stem = raw[0].rstrip(".").lower()
    return q[1] == l[1] and l[0].startswith(stem) and len(l[0]) > len(stem)


def _typo_close(a: str, b: str) -> bool:
    """One- or two-character typo."""
    if min(len(a), len(b)) < 6:
        return False
    if abs(len(a) - len(b)) > 2:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.92


def _uninvert(label: str) -> str:
    """'Control, Rodent' → 'Rodent Control' (MeSH-style inverted names)."""
    parts = [p.strip() for p in label.split(",")]
    return f"{parts[1]} {parts[0]}" if len(parts) == 2 and all(parts) else label


def _single(query: str, label: str) -> str:
    if not query or not label:
        return NONE
    label = _uninvert(label)
    nq, nl = normalize_term(query), normalize_term(label)
    if not nq or not nl:
        return NONE
    if nq == nl or nq.replace(" ", "") == nl.replace(" ", ""):
        return EXACT                              # also "IL-6" == "IL6"
    tq, tl = tokens(query), tokens(label)
    if tq and tq == tl:
        return EXACT
    if tq and sorted(tq) == sorted(tl):
        # "population of Drosophila" == "Drosophila populations", but "control rodents" != "rodent control"
        linked = any(w in _STOPWORDS for w in (nq + " " + nl).split())
        return EXACT if linked else CLOSE
    if _abbreviation_match(query, label):
        return EXACT
    if _typo_close(" ".join(tq), " ".join(tl)):
        return CLOSE
    sq, sl = set(tq), set(tl)
    if sq and sl:
        if sq <= sl and len(sl - sq) == 1 and len(sq) >= 2:
            return CLOSE                          # label adds one qualifying word
        if sl <= sq and len(sq - sl) == 1 and len(sl) >= 2:
            return CLOSE                          # query adds one qualifying word
    return NONE


def match_quality(query: str, label: str, synonyms=()) -> str:
    """Best match against label and synonyms: 'exact' > 'close' > 'none'."""
    if isinstance(synonyms, str):
        synonyms = [s for s in synonyms.split(";")]
    best = _single(query, label)
    if best == EXACT:
        return EXACT
    for syn in synonyms or ():
        syn = (syn or "").strip()
        if not syn:
            continue
        q = _single(query, syn)
        if q == EXACT:
            return EXACT
        if q == CLOSE:
            best = CLOSE
    return best


def is_match(query: str, label: str, synonyms=()) -> bool:
    return match_quality(query, label, synonyms) != NONE
