"""Layer 7 step 1c: LLM checks figure facts against the paper's sentences (FIGURE_TEXT_CHECK=false to skip)."""
import os
import re
from concurrent.futures import ThreadPoolExecutor

from src.llm_client import call_llm

ENABLED = os.getenv("FIGURE_TEXT_CHECK", "true").lower() == "true"
_SENTENCES = 6
_BATCH = 5
_WORKERS = int(os.getenv("FIGURE_TEXT_CHECK_WORKERS", "4"))


def _names(record: dict, side: str) -> list:
    syns = record.get(f"{side}_synonyms") or ""
    syns = syns.split("; ") if isinstance(syns, str) else list(syns)
    names = [record.get(f"{side}_name"), record.get(f"{side}_canonical_name"), *syns[:5]]
    return [n.lower() for n in dict.fromkeys(n for n in names if n and len(n) >= 3)]


def _mentions(sentence: str, names: list) -> bool:
    low = sentence.lower()
    return any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", low) for n in names)


def _evidence(record: dict, sentences: list) -> list:
    """Up to 2 sentences naming both entities, then up to 3 per entity."""
    subj, obj = _names(record, "subject"), _names(record, "object")
    both = [x for x in sentences if _mentions(x, subj) and _mentions(x, obj)][:2]
    only_s = [x for x in sentences if x not in both and _mentions(x, subj)][:3]
    only_o = [x for x in sentences if x not in both and _mentions(x, obj)][:3]
    return list(dict.fromkeys(both + only_s + only_o))[:_SENTENCES + 2]


def _judge(items: list) -> list:
    """(verdict, sentence) per (record, evidence) item; verdict is 'SUPPORT' | 'CONTRADICT' | 'UNADDRESSED'.
    A contradiction must name the sentence that contradicts; without one it counts as unaddressed."""
    blocks = []
    for n, (r, ev) in enumerate(items, 1):
        fact = f"{r['subject_name']} —{r['relation']}→ {r['object_name']}" + (" (NOT)" if r.get("negated") else "")
        blocks.append(f"FACT {n}, read from a figure: {fact}\n" +
                      "\n".join(f"   S{k}. {x[:400]}" for k, x in enumerate(ev, 1)))
    prompt = (
        "Each fact below was read from a figure by an image model. Under it are the paper's own sentences about "
        "its entities. Decide for each fact whether the paper's text SUPPORTS it, CONTRADICTS it, or does not "
        "address it (UNADDRESSED). CONTRADICT also when the text attributes the finding to a different entity "
        "in a way that rules this one out (e.g. 'enriched in neuronal cells' against a fact about glial cells), "
        "or states the opposite direction. For CONTRADICT, give the number of the sentence that contradicts it.\n\n"
        + "\n\n".join(blocks) +
        "\n\nAnswer one line per fact, nothing else: <fact number>: SUPPORT | CONTRADICT S<sentence number> | UNADDRESSED"
    )
    out = [("UNADDRESSED", "")] * len(items)
    try:
        answer = (call_llm([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=16 * len(items) + 16,
                           json_mode=False) or "").upper()
    except Exception:
        return out                                         # model unreachable: no change
    for line in answer.splitlines():
        m = re.match(r"\s*\**\s*(?:FACT\s*)?(\d+)\s*[:.)-]\s*(SUPPORT|CONTRADICT|UNADDRESSED)(?:\W*S?(\d+))?", line)
        if not (m and 1 <= int(m.group(1)) <= len(items)):
            continue
        i, verdict, ev = int(m.group(1)) - 1, m.group(2), items[int(m.group(1)) - 1][1]
        if verdict == "CONTRADICT":
            k = int(m.group(3) or 0)
            out[i] = ("CONTRADICT", ev[k - 1]) if 1 <= k <= len(ev) else ("UNADDRESSED", "")
        else:
            out[i] = (verdict, ev[0] if verdict == "SUPPORT" else "")
    return out


def check(records: list, paper_text: str) -> dict:
    """Mark figure facts the paper's text contradicts; returns counts."""
    stats = {"checked": 0, "supported": 0, "contradicted": 0, "unaddressed": 0}
    if not ENABLED or not paper_text:
        return stats
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", paper_text) if len(x) > 20]
    items = [(r, ev) for r in records if r.get("section") == "figure" for ev in [_evidence(r, sentences)] if ev]
    if not items:
        return stats
    batches = [items[i:i + _BATCH] for i in range(0, len(items), _BATCH)]
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        verdicts = [v for b in ex.map(_judge, batches) for v in b]
    for (r, ev), (v, sentence) in zip(items, verdicts):
        stats["checked"] += 1
        stats[{"SUPPORT": "supported", "CONTRADICT": "contradicted", "UNADDRESSED": "unaddressed"}[v]] += 1
        if v == "CONTRADICT":                              # rejected at the gate: the text outranks the image reading
            r["is_contradiction"] = True
            r["text_contradiction"] = sentence[:300]
            r["review_reason"] = ((r.get("review_reason") or "") + " | the paper's text contradicts this figure "
                                  f"reading: {sentence[:160]}").strip(" |")
        elif v == "SUPPORT":
            r["text_support"] = sentence[:300]
    return stats
