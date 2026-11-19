"""Natural-language question → PeTTaChainer query, by relation templates; entity names
resolved to (type name) through the node label map."""
import re

_ID_PAT = re.compile(r'^[A-Z]+_\d+$')

# (pattern, PLN_predicate, subject_slot, object_slot)
# slot: 'A' = first capture group, 'B' = second, '$var' = free variable
_PATTERNS: list[tuple[re.Pattern, str, str, str]] = [
    # "what does X inhibit"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+inhibits?',       re.I), 'Inhibits',          'A', '$target'),
    # "what inhibits X"
    (re.compile(r'what\s+inhibits\s+(.+)',                      re.I), 'Inhibits',          '$source', 'A'),
    # "what activates X"
    (re.compile(r'what\s+activates\s+(.+)',                     re.I), 'Activates',         '$source', 'A'),
    # "what does X activate"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+activates?',      re.I), 'Activates',         'A', '$target'),
    # "what binds to X"
    (re.compile(r'what\s+binds\s+to\s+(.+)',                    re.I), 'Binds_To',          '$source', 'A'),
    # "what does X bind to"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+binds?\s+to',     re.I), 'Binds_To',          'A', '$target'),
    # "what does X lead to" / "what does X result in"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+(?:leads?\s+to|results?\s+in|contributes?\s+to)', re.I),
                                                                       'Causes',            'A', '$target'),
    # "what leads to X" / "what results in X"
    (re.compile(r'what\s+(?:leads?\s+to|results?\s+in|contributes?\s+to)\s+(.+)', re.I),
                                                                       'Causes',            '$source', 'A'),
    # "what causes X" / "what disease causes X"
    (re.compile(r'what\s+(?:\w+\s+)?causes?\s+(.+)',            re.I), 'Causes',            '$source', 'A'),
    # "what does X cause"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+causes?',         re.I), 'Causes',            'A', '$target'),
    # "is X associated with Y"
    (re.compile(r'is\s+(.+?)\s+associated\s+with\s+(.+)',       re.I), 'Is_Associated_With','A', 'B'),
    # "what is X associated with"
    (re.compile(r'what\s+is\s+(.+?)\s+associated\s+with',      re.I), 'Is_Associated_With','A', '$target'),
    # "what treats X" / "what drugs treat X"
    (re.compile(r'what\s+(?:\w+\s+)?treats?\s+(.+)',            re.I), 'Treats',            '$source', 'A'),
    # "what does X treat"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+treats?',         re.I), 'Treats',            'A', '$target'),
    # "what regulates X"
    (re.compile(r'what\s+regulates\s+(.+)',                     re.I), 'Regulates',         '$source', 'A'),
    # "what does X regulate"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+regulates?',      re.I), 'Regulates',         'A', '$target'),
    # "what upregulates X"
    (re.compile(r'what\s+upregulates?\s+(.+)',                  re.I), 'Upregulates',       '$source', 'A'),
    # "what downregulates X"
    (re.compile(r'what\s+downregulates?\s+(.+)',                re.I), 'Downregulates',     '$source', 'A'),
    # "what does X upregulate"
    (re.compile(r'what\s+(?:does\s+)?(.+?)\s+upregulates?',    re.I), 'Upregulates',       'A', '$target'),
    # "what is a biomarker for X"
    (re.compile(r'what\s+(?:is|are)\s+(?:a\s+)?biomarker(?:s)?\s+(?:for|of)\s+(.+)', re.I),
                                                                       'Is_Biomarker_Of',   '$source', 'A'),
    # "what is identical/equivalent to X"
    (re.compile(r'what\s+is\s+(?:identical|equivalent)\s+to\s+(.+)', re.I),
                                                                       'IdenticalTo',       '$source', 'A'),
]


def _name_slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')[:40]


def _resolve(entity: str, entity_map: dict | None) -> str:
    """Entity name → '(type name_slug)' through entity_map; unknown names → '$entity'."""
    e = entity.strip()
    if entity_map:
        hit = entity_map.get(e.lower())
        if hit:
            etype, _ = hit          # discard canonical ID — atoms use name slugs
            slug = _name_slug(e)
            return f"({etype} {slug})"
    # Fallback: no entity_map hit — use slug as bare token, type unknown
    slug = _name_slug(e)
    return slug if slug else '$entity'


def nl_to_pln_query(question: str,
                    entity_map: dict | None = None) -> str | None:
    """PeTTaChainer query for a question, or None if no template matches."""
    q = question.strip().rstrip('?').strip()
    for pattern, predicate, slot_a, slot_b in _PATTERNS:
        m = pattern.search(q)
        if not m:
            continue
        groups = m.groups()
        A_raw = groups[0].strip() if groups else ''
        B_raw = groups[1].strip() if len(groups) > 1 else ''

        A = _resolve(A_raw, entity_map) if A_raw else '$a'
        B = _resolve(B_raw, entity_map) if B_raw else '$b'

        subj = A if slot_a == 'A' else (B if slot_a == 'B' else slot_a)
        obj  = A if slot_b == 'A' else (B if slot_b == 'B' else slot_b)

        return f"(: $prf ({predicate} {subj} {obj}) $tv)"

    return None


def extract_entities(question: str) -> list[str]:
    """Capitalised or ID-like tokens in a question, for highlighting the answer."""
    tokens = re.findall(r'\b([A-Z][A-Z0-9_-]{2,}|HGNC_\d+|CHEBI_\d+|MESH_\d+|HP_\d+)\b', question)
    return list(dict.fromkeys(tokens))  # deduplicate, preserve order
