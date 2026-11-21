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


def _term_slug(term: str) -> str:
    """'(type name_slug)' → name_slug."""
    return term[1:-1].split(' ', 1)[-1] if term.startswith('(') else term


def _candidates(entity: str, entity_map: dict | None, terms: list[str]) -> tuple[list[str], str]:
    """(terms, how) — this paper's terms a name may mean: the exact name, a synonym with the
    same ID, or else every term whose name holds all the words of the question's name."""
    slug = _name_slug(entity)
    exact = [t for t in terms if _term_slug(t) == slug]
    if exact:
        return exact, 'exact name'
    hit = (entity_map or {}).get(entity.strip().lower())
    if hit:
        names = {_name_slug(label) for label, v in entity_map.items() if v == hit}
        same_id = [t for t in terms if _term_slug(t) in names]
        if same_id:
            return same_id, f'synonym of {hit[1]}'
    words = set(slug.split('_')) - {''}
    return [t for t in terms if words and words <= set(_term_slug(t).split('_'))], 'all words in the name'


def plan_query(question: str, entity_map: dict | None = None,
               terms: list[str] | None = None) -> tuple[str | None, dict, list[str]]:
    """(query, filters, notes) for a question. With the paper's terms, a name that matches one
    term becomes that term; a name that matches several becomes a variable, and filters
    ({'subject'|'object': set of terms}) keeps only answers about those terms. notes say how."""
    q = question.strip().rstrip('?').strip()
    for pattern, predicate, slot_a, slot_b in _PATTERNS:
        m = pattern.search(q)
        if not m:
            continue
        groups = m.groups()
        raw = {'A': groups[0].strip() if groups else '',
               'B': groups[1].strip() if len(groups) > 1 else ''}
        filters: dict[str, set] = {}
        parts = []
        notes = [f"template '{pattern.pattern}' → {predicate}(subject, object)"]
        for role, slot in (('subject', slot_a), ('object', slot_b)):
            name = raw.get(slot)
            if name is None:                       # a free variable slot like '$target'
                parts.append(slot)
                continue
            found, how = _candidates(name, entity_map, terms) if terms is not None and name else ([], '')
            if len(found) == 1:
                parts.append(found[0])
                notes.append(f"{role} '{name}' → {found[0]} ({how})")
            elif found:
                parts.append(f'${role}')
                filters[role] = set(found)
                notes.append(f"{role} '{name}' matches {len(found)} entities ({how}): {', '.join(found)} → "
                             f"query with ${role}, keep only these")
            else:
                parts.append(_resolve(name, entity_map) if name else f'${role}')
                notes.append(f"{role} '{name}' is not an entity in this paper → {parts[-1]}")
        return f"(: $prf ({predicate} {parts[0]} {parts[1]}) $tv)", filters, notes
    return None, {}, ['no question template matched']


def nl_to_pln_query(question: str,
                    entity_map: dict | None = None) -> str | None:
    """PeTTaChainer query for a question, or None if no template matches."""
    return plan_query(question, entity_map)[0]


def extract_entities(question: str) -> list[str]:
    """Capitalised or ID-like tokens in a question, for highlighting the answer."""
    tokens = re.findall(r'\b([A-Z][A-Z0-9_-]{2,}|HGNC_\d+|CHEBI_\d+|MESH_\d+|HP_\d+)\b', question)
    return list(dict.fromkeys(tokens))  # deduplicate, preserve order
