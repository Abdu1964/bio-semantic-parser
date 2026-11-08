"""Local IDs for paper-defined names with no ontology ID (LOCAL_CONCEPTS=false turns this off)."""
import os
import re

ENABLED = os.getenv("LOCAL_CONCEPTS", "true").lower() == "true"
_MIN_MENTIONS = int(os.getenv("LOCAL_CONCEPT_MIN_MENTIONS", "2"))
_FUNCTION_WORDS = {"a", "an", "the", "in", "on", "at", "of", "to", "for", "by", "with", "as", "and", "or",
                   "is", "are", "was", "were", "be", "this", "that", "these", "those", "it", "its", "from"}


def unresolved(cid) -> bool:
    return not cid or cid == "NEEDS_REVIEW" or str(cid).startswith("TEXT:")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _stem(word: str) -> str:
    """Drop a plural, adjective or final -e ending: 'epigenomic' and 'epigenome' → 'epigenom'."""
    for end in ("ic", "s", "e"):
        if len(word) > 4 and word.endswith(end) and not word.endswith("ss"):
            word = word[: -len(end)]
    return word


def local_id(document_id: str, name: str) -> str:
    """Same ID for word-ending variants of a name ('SPI1 binding site' / 'SPI1 binding sites')."""
    return f"LOCAL:{_slug(document_id)}/" + "-".join(_stem(w) for w in _slug(name).split("-") if w)


_ID_FIELDS = ("id", "canonical_name", "id_source", "source_url", "synonyms")


def reuse_paper_ids(records: list) -> int:
    """An unresolved name takes the real ID the same name (case, spacing, punctuation aside) has in the paper."""
    key = lambda n: re.sub(r"[\W_]+", "", (n or "").lower())
    known = {}
    for r in records:
        for side in ("subject", "object"):
            if (not unresolved(r.get(f"{side}_id")) and not str(r.get(f"{side}_id")).startswith("LOCAL:")
                    and not r.get(f"{side}_non_canonical")):
                known.setdefault((key(r.get(f"{side}_name")), r.get(f"{side}_type")),
                                 {f: r.get(f"{side}_{f}") for f in _ID_FIELDS})
    n = 0
    for r in records:
        for side in ("subject", "object"):
            hit = (known.get((key(r.get(f"{side}_name")), r.get(f"{side}_type")))
                   if unresolved(r.get(f"{side}_id")) and not r.get(f"{side}_non_canonical") else None)
            if hit:
                r.update({f"{side}_{f}": v for f, v in hit.items()})
                n += 1
    return n


def mark(records: list, paper_text: str) -> int:
    """Set {side}_local_id on unresolved names the paper uses 2+ times, when the other side has a real ID."""
    if not ENABLED or not paper_text:
        return 0
    reuse_paper_ids(records)
    low, counts, marked = paper_text.lower(), {}, 0
    for r in records:
        for side, other in (("subject", "object"), ("object", "subject")):
            name = (r.get(f"{side}_name") or "").strip()
            if (not unresolved(r.get(f"{side}_id")) or unresolved(r.get(f"{other}_id"))
                    or str(r.get(f"{other}_id")).startswith("LOCAL:") or r.get(f"{side}_network_degraded")
                    or not re.search(r"[a-z]", name.lower()) or name.lower() in _FUNCTION_WORDS):
                continue
            if name.lower() not in counts:
                counts[name.lower()] = len(re.findall(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", low))
            if counts[name.lower()] >= _MIN_MENTIONS:
                r[f"{side}_local_id"] = local_id(r.get("document_id", ""), name)
                marked += 1
    return marked
