"""Local IDs for paper-defined names with no ontology ID (LOCAL_CONCEPTS=false turns this off)."""
import os
import re
import unicodedata

ENABLED = os.getenv("LOCAL_CONCEPTS", "true").lower() == "true"
_MIN_MENTIONS = int(os.getenv("LOCAL_CONCEPT_MIN_MENTIONS", "2"))
# Both sides local: 3+ mentions each. 6 papers: keeps central claims ("epigenomic erosion promotes cell
# identity loss"), drops one-off figure labels ("2L part_of Drosophila genome").
_BOTH_MIN_MENTIONS = int(os.getenv("LOCAL_CONCEPT_BOTH_MIN_MENTIONS", "3"))
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


def mark(records: list, paper_text: str, body_text: str = None) -> int:
    """Set {side}_local_id on unresolved names the paper uses 2+ times, when the other side has a real ID;
    when neither side has one, both names must be central to the paper (3+ mentions each in body_text,
    the text without figure descriptions, where panel labels repeat)."""
    if not ENABLED or not paper_text:
        return 0
    reuse_paper_ids(records)
    texts = {"all": paper_text, "body": paper_text if body_text is None else body_text}
    texts = {k: unicodedata.normalize("NFKC", v).lower() for k, v in texts.items()}
    counts, marked = {}, 0

    def mentions(name: str, where: str = "all") -> int:
        if (name, where) not in counts:
            counts[name, where] = len(re.findall(rf"(?<![\w-]){re.escape(name)}(?![\w-])", texts[where]))
        return counts[name, where]

    for r in records:
        names = {s: unicodedata.normalize("NFKC", r.get(f"{s}_name") or "").strip().lower() for s in ("subject", "object")}
        for side, other in (("subject", "object"), ("object", "subject")):
            name = names[side]
            if (not unresolved(r.get(f"{side}_id")) or str(r.get(f"{other}_id")).startswith("LOCAL:")
                    or r.get(f"{side}_network_degraded") or not re.search(r"[a-z]", name) or name in _FUNCTION_WORDS):
                continue
            if unresolved(r.get(f"{other}_id")):
                if (r.get(f"{other}_network_degraded")
                        or min(mentions(name, "body"), mentions(names[other], "body")) < _BOTH_MIN_MENTIONS):
                    continue
            elif mentions(name) < _MIN_MENTIONS:
                continue
            r[f"{side}_local_id"] = local_id(r.get("document_id", ""), r.get(f"{side}_name") or "")
            marked += 1
    return marked
