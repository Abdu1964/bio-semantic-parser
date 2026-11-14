"""Certainty of a sentence's main claim (Kilicoglu et al. 2017 factuality levels, PMC5497973), judged by the
pipeline LLM in batches before confidence scoring. CERTAINTY_LLM=false, or an unreachable LLM, leaves a
sentence at the neutral level: no fact is capped without a judgement.

On 207 sentences from 9 papers labelled blind, the LLM gets 78% of levels right and wrongly caps 4 definite
or claim-free sentences; the hedge word lists it replaces got 50% and wrongly capped 20.
"""
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from src.llm_client import call_llm

ENABLED = os.getenv("CERTAINTY_LLM", "true").lower() == "true"
_BATCH = int(os.getenv("CERTAINTY_BATCH", "15"))
_WORKERS = int(os.getenv("CERTAINTY_WORKERS", "4"))

# Kilicoglu levels as confidence values
_VALUE = {"FACT": 0.95, "PROBABLE": 0.75, "POSSIBLE": 0.50, "DOUBTFUL": 0.20, "NONE": 0.65}
NEUTRAL = _VALUE["NONE"]

_ASK = ("For each numbered sentence from a scientific paper, give the certainty of its main claim: "
        "FACT = stated as established or observed (a definite negative is FACT); PROBABLE = presented as likely "
        "(suggests, indicates, consistent with); POSSIBLE = only possible, a hypothesis or speculation; "
        "DOUBTFUL = presented as unlikely or disputed; NONE = no claim (method, aim, outline, definition).\n"
        "Answer one line per sentence, nothing else: <number>: <LABEL>\n\n")

_cache: dict = {}
_lock = threading.Lock()


def _judge(batch: list) -> dict:
    """{sentence: label} for one batch; {} when the LLM cannot be reached."""
    try:
        answer = call_llm([{"role": "user", "content": _ASK + "\n".join(f"{i}. {s}" for i, s in enumerate(batch, 1))}],
                          temperature=0.0, max_tokens=12 * len(batch) + 20, json_mode=False) or ""
    except Exception:
        return {}
    out = {}
    for line in answer.upper().splitlines():
        m = re.match(r"\s*\**\s*(\d+)\s*[:.)-]\s*\**\s*(FACT|PROBABLE|POSSIBLE|DOUBTFUL|NONE)", line)
        if m and 1 <= int(m.group(1)) <= len(batch):
            out[batch[int(m.group(1)) - 1]] = m.group(2)
    return out


def prefetch(sentences) -> int:
    """Judge every sentence not judged yet, in batches; returns how many were judged."""
    todo = [s for s in dict.fromkeys((x or "").strip() for x in sentences) if s and s not in _cache]
    if not ENABLED or not todo:
        return 0
    batches = [todo[i:i + _BATCH] for i in range(0, len(todo), _BATCH)]
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        results = list(ex.map(_judge, batches))
    with _lock:
        for r in results:
            _cache.update(r)
    return sum(len(r) for r in results)


def level(sentence: str) -> float:
    """Confidence value of the sentence's certainty; neutral when it was not judged."""
    return _VALUE.get(_cache.get((sentence or "").strip()), NEUTRAL)
