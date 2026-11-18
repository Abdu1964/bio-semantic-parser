"""How much of a paper's cleaned text reaches the chunks Layer 6 reads."""
import os
import re
from collections import Counter

MIN_COVERAGE = float(os.getenv("MIN_TEXT_COVERAGE", "0.98"))
_WORD = re.compile(r"\w+")


def text_coverage(text: str, chunks: list) -> float:
    """Share of the text's words found in the chunks (overlapping chunks count once)."""
    want = Counter(_WORD.findall(text.lower()))
    if not want:
        return 1.0
    got = Counter(_WORD.findall(" ".join(c.get("text", "") for c in chunks).lower()))
    return sum(min(n, got[w]) for w, n in want.items()) / sum(want.values())


def coverage_line(text: str, chunks: list) -> str:
    """Log line for Layer 3; warns when text was lost between cleaning and chunking."""
    cov = text_coverage(text, chunks)
    if cov >= MIN_COVERAGE:
        return f"           ✓ Coverage: {cov:.1%} of the text reached the chunks"
    return (f"           ⚠ Coverage: only {cov:.1%} of the text reached the chunks — "
            f"{1 - cov:.1%} was lost before extraction")
