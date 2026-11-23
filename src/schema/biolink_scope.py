"""Which Biolink-generated relations fit a chunk, from Biolink domain/range and the chunk's entity labels."""
import os

from src.schema.biolink_generated import ANCESTORS, DOMAIN_RANGE, ENTITY_CLASS, GENERATED, NER_LABEL_CLASS

ENABLED = os.getenv("BIOLINK_PROMPT_FILTER", "true").lower() == "true"
_GENERATED = frozenset(g["value"] for g in GENERATED)


def hidden_relations(labels) -> frozenset:
    """Generated relations whose Biolink domain or range fits no entity in the chunk (none hidden if a label is unknown)."""
    if not ENABLED or not labels:
        return frozenset()
    classes = set()
    for label in labels:
        key = str(label or "").upper()
        c = NER_LABEL_CLASS.get(key) or ENTITY_CLASS.get(key)
        if not c:
            return frozenset()
        classes.add(c)
    lineages = [set(ANCESTORS.get(c, [c])) for c in classes]
    fits = lambda cls: not cls or any(cls in a for a in lineages)
    return frozenset(r for r in _GENERATED if not (fits(DOMAIN_RANGE.get(r, ("", ""))[0]) and fits(DOMAIN_RANGE.get(r, ("", ""))[1])))
