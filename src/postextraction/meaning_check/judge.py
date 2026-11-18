"""LLM judgement, several concepts per call: is each current ID's concept what the authors mean — judged by
meaning in the sentence, not by wording — and if it is another concept, which alternative is."""
import re
import string

from src.llm_client import call_llm

_LETTERS = string.ascii_uppercase


def _describe(c: dict) -> str:
    line = c["label"]
    if c.get("definition"):
        line += f" — {c['definition']}"
    if c.get("synonyms"):
        line += f" (also: {'; '.join(c['synonyms'][:4])})"
    return line


def _block(n: int, item: dict) -> str:
    others = "\n".join(f"   {_LETTERS[i]}. {_describe(c)}" for i, c in enumerate(item["alts"])) or "   (none)"
    return (f'TERM {n}: "{item["name"]}" — the {item["role"]} of the fact, type {item["type"]}\n'
            f'   Sentence: "{item["sentence"][:400]}"\n'
            f"   Fact: {item['fact']}\n"
            f"   Current concept: {_describe(item['current'])}\n"
            f"   Other candidates:\n{others}")


def judge_batch(items: list) -> list:
    """[(verdict, index)] per item: verdict SAME | BROADER | DIFFERENT for the current concept; index of the
    alternative that is exactly the concept meant, or None. Unanswered items keep their ID."""
    prompt = (
        "For each term, decide whether its current concept is what the authors mean in that sentence. Judge by "
        "meaning, not wording. SAME = the same concept (a standard name or synonym counts, e.g. GABAergic neuron "
        "for inhibitory neuron); BROADER = correct but more general; DIFFERENT = another concept (another field's "
        "term, a narrower or related concept, a single member for a family, another organism's term). Unless SAME, "
        "also give the letter of the other candidate that is exactly the concept meant, or NONE.\n\n"
        + "\n\n".join(_block(n, it) for n, it in enumerate(items, 1))
        + "\n\nAnswer one line per term, nothing else: <term number>: SAME | BROADER <letter|NONE> | DIFFERENT <letter|NONE>"
    )
    out = [("SAME", None)] * len(items)
    try:
        answer = (call_llm([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=16 * len(items) + 16,
                           json_mode=False) or "").upper()
    except Exception:
        return out                                          # unreachable model: keep every current ID
    for line in answer.splitlines():
        m = re.match(r"\s*\**\s*(?:TERM\s*)?(\d+)\s*[:.)-]\s*(SAME|BROADER|DIFFERENT)\b\s*([A-Z])?\b", line)
        if not m or not 1 <= int(m.group(1)) <= len(items):
            continue
        k, verdict, letter = int(m.group(1)) - 1, m.group(2), m.group(3)
        index = _LETTERS.index(letter) if letter and letter in _LETTERS[:len(items[k]["alts"])] else None
        out[k] = (verdict, index if verdict != "SAME" else None)
    return out
