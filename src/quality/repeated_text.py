"""How much of a paper's text is repeated — repeated text is extracted twice."""
import os
import re
from collections import Counter

MAX_REPEATED = float(os.getenv("MAX_REPEATED_TEXT", "0.05"))
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_MIN_LEN = 80                                   # short sentences repeat legitimately


def repeated_share(text: str) -> float:
    """Share of the text made of long sentences that occur more than once."""
    counts = Counter(s for s in _SENTENCE.split(text) if len(s) >= _MIN_LEN)
    return sum(len(s) * (n - 1) for s, n in counts.items() if n > 1) / max(1, len(text))


def repeated_line(text: str) -> str:
    """Log line for Layer 3; warns when the reader repeated text."""
    share = repeated_share(text)
    if share <= MAX_REPEATED:
        return f"           ✓ Repeated text: {share:.1%}"
    return f"           ⚠ Repeated text: {share:.1%} of the text appears twice — it will be extracted twice"
