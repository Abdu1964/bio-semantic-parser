"""Natural-language question → PeTTaChainer query. Question forms are built from the relation names in
the atomspace (BindsTo → "What does X bind to?" / "What binds to X?"); entity names resolve to the
entity symbols (normalised ontology IDs) through their names and synonyms."""
import re
from functools import lru_cache


def _name_slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')


def _words(predicate: str) -> list[str]:
    """'RegulatesExpressionOf' → ['regulates', 'expression', 'of']."""
    return [w.lower() for w in re.findall(r'[A-Z][a-z0-9]*|[a-z0-9]+', predicate)]


@lru_cache(maxsize=1)
def _tagger():
    """The pipeline's scispaCy model, tagger only; None when it is not installed."""
    try:
        import spacy
        return spacy.load("en_core_sci_lg", disable=["ner", "parser"])
    except Exception:
        return None


@lru_cache(maxsize=None)
def question_forms(predicate: str) -> tuple[str, str]:
    """(asks for objects, asks for subjects) for a relation, from its own words and the tag of the first:
    a verb ('binds to') → 'What does {s} bind to?' / 'What binds to {o}?'; 'is …' → 'What is {s} …?';
    'has …' → 'What … does {s} have?'; a noun or participle ('part of') → 'What is {s} part of?'."""
    words = _words(predicate)
    rest = ' '.join(words[1:])
    nlp = _tagger()
    tok = nlp(f"it {' '.join(words)} x")[1] if nlp else None
    tag, lemma = (tok.tag_, tok.lemma_.lower()) if tok else ('VBZ', words[0].rstrip('s'))
    phrase = ' '.join(words)
    if lemma == 'be':
        return f"What is {{s}} {rest}?", f"What is {rest} {{o}}?"
    if lemma == 'have':
        return f"What {rest} does {{s}} have?", f"What has {rest} {{o}}?"
    if tag in ('VBZ', 'VBP'):
        return f"What does {{s}} {' '.join([lemma] + words[1:])}?", f"What {phrase} {{o}}?"
    return f"What is {{s}} {phrase}?", f"What is {phrase} {{o}}?"


def _form_regex(form: str, slot: str) -> re.Pattern:
    """A question form as a pattern: the slot captures the entity name; spacing, case and a word's
    -s/-es/-d/-ed ending ('regulates' for 'regulate') and an article before a word are free."""
    before, after = form.rstrip('?').split(slot)
    esc = lambda t: r'\s+'.join(r'(?:(?:a|an|the)\s+)?' + re.escape(w) + r'(?:e?s|e?d)?' for w in t.split())
    return re.compile(rf"^\s*{esc(before)}\s*(.+?)\s*{esc(after)}\s*\??\s*$", re.I)


def _candidates(name: str, names: dict[str, set]) -> tuple[list[str], str]:
    """(entity symbols, how) a name may mean: an entity whose name or synonym is exactly it, or else
    every entity with a name holding all of its words."""
    slug = _name_slug(name)
    exact = [t for t, labels in names.items() if slug in {_name_slug(l) for l in labels}]
    if exact:
        return exact, 'its name or a synonym'
    words = set(slug.split('_')) - {''}
    found = [t for t, labels in names.items()
             if words and any(words <= set(_name_slug(l).split('_')) for l in labels)]
    return found, 'all words in a name'


def _respell(question: str, names: dict[str, set], predicates: list[str]) -> tuple[str, list[str]]:
    """The question with each word that is close to a question-form word ('regulates', 'regualtes' →
    'regulate') spelled as that word; words of entity names are left alone. Returns (question, changes)."""
    import difflib
    vocab = {w for p in predicates for f in question_forms(p)
             for w in re.findall(r'[a-z]+', f.lower().replace('{s}', ' ').replace('{o}', ' '))}
    name_words = {w for labels in names.values() for l in labels for w in _name_slug(l).split('_')}
    out, changes = [], []
    for tok in re.split(r'(\W+)', question):
        low = tok.lower()
        if len(low) > 3 and low not in vocab and low not in name_words:
            close = difflib.get_close_matches(low, vocab, n=1, cutoff=0.8)
            if close:
                changes.append(f"'{tok}' → '{close[0]}'")
                tok = close[0]
        out.append(tok)
    return ''.join(out), changes


def plan_query(question: str, names: dict[str, set] | None = None,
               predicates: list[str] | None = None) -> tuple[str | None, dict, list[str]]:
    """(query, filters, notes) for a question over the given relations. A name that matches one entity
    becomes that entity; one that matches several becomes a variable, and filters ({'subject'|'object':
    set of symbols}) keeps only answers about those entities. notes say how each step went. A question
    no form matches is tried once more with its words respelled to the forms' words."""
    names = names or {}
    query, filters, notes = _plan(question, names, predicates)
    if query is None:
        respelled, changes = _respell(question, names, predicates or [])
        if changes:
            query, filters, notes = _plan(respelled, names, predicates)
            notes = [f"read {', '.join(changes)}"] + notes
    if query is None and re.match(r"\s*(?:why|how|which)\b", question, re.I):   # the proof answers "why"
        asked = re.sub(r"^\s*(?:why|how|which)\b", "What", question, flags=re.I)
        query, filters, notes = plan_query(asked, names, predicates)
        if query:
            notes = [f"read '{question.split()[0]}' as 'what': the proof shows why"] + notes
    if query is None:
        query, filters, notes = _about(question, names, predicates or []) or (query, filters, notes)
    return query, filters, notes


_ABOUT = re.compile(r"^\s*(?:what\s+(?:does|do|did|is|are|about)|tell\s+me\s+about|about)\s+(.+?)"
                    r"(?:\s+(?:do|does|affect|have|has))?\s*\??\s*$", re.I)


def _about(question: str, names: dict, predicates: list[str]):
    """'What does X?' / 'Tell me about X': every relation with X as the subject. The query's relation is
    ALL_RELATIONS and filters['all_relations'] lists them; the reasoner sends one query per relation."""
    m = _ABOUT.match(question)
    if not m or not predicates:
        return None
    name = m.group(1).strip()
    found, how = _candidates(name, names)
    if not found:
        return None
    filters: dict = {'all_relations': list(predicates)}
    if len(found) == 1:
        term, note = found[0], f"subject '{name}' → {found[0]} ({how})"
    else:
        term, note = '$subject', f"subject '{name}' matches {len(found)} entities ({how}): {', '.join(found[:12])} → query with $subject, keep only these"
        filters['subject'] = set(found)
    return (f"(: $prf (ALL_RELATIONS {term} $target) $tv)", filters,
            [f"no relation named: every relation ({len(predicates)}) with '{name}' as the subject", note])


def _plan(question: str, names: dict[str, set], predicates: list[str] | None) -> tuple[str | None, dict, list[str]]:
    # longer relations first, so 'regulates expression of' wins over 'regulates'
    for pred in sorted(predicates or [], key=lambda p: -len(_words(p))):
        fwd, back = question_forms(pred)
        plain = f"What does {{s}} {' '.join(_words(pred))}?"          # the relation's own words after "What does X"
        forms = [(fwd, '{s}', 'subject', '$target'), (back, '{o}', 'object', '$source')]
        if plain.lower() != fwd.lower():
            forms.append((plain, '{s}', 'subject', '$target'))
        for form, slot, role, free in forms:
            m = _form_regex(form, slot).match(question)
            if not m:
                continue
            name = m.group(1).strip()
            notes = [f"form '{form.replace(slot, 'X')}' → {pred}(subject, object), asking for the {'object' if role == 'subject' else 'subject'}"]
            found, how = _candidates(name, names)
            filters: dict[str, set] = {}
            if len(found) == 1:
                term = found[0]
                notes.append(f"{role} '{name}' → {term} ({how})")
            elif found:
                term = f'${role}'
                filters[role] = set(found)
                notes.append(f"{role} '{name}' matches {len(found)} entities ({how}): {', '.join(found[:12])}"
                             f"{' …' if len(found) > 12 else ''} → query with ${role}, keep only these")
            else:
                term = _name_slug(name) or f'${role}'
                notes.append(f"{role} '{name}' is not an entity in the atomspace → {term}")
            subj, obj = (term, free) if role == 'subject' else (free, term)
            return f"(: $prf ({pred} {subj} {obj}) $tv)", filters, notes
    return None, {}, ['no question form of the loaded relations matched']


def suggest_questions(facts: list[tuple[str, str, str]], names: dict[str, set], limit: int = 3) -> dict:
    """Questions the atomspace can answer, made from its own facts: one relation after another, a new
    entity each time."""
    show = lambda t: sorted(names.get(t) or {t}, key=len)[0]
    by_pred: dict[str, list[tuple[str, str]]] = {}
    for pred, s, o in facts:
        by_pred.setdefault(pred, []).append((s, o))
    queues = []
    for pred, pairs in sorted(by_pred.items(), key=lambda kv: -len(kv[1])):
        fwd, back = question_forms(pred)
        queues.append([{'question': f.format(s=show(s), o=show(o)), 'predicate': pred, 'facts': len(pairs)}
                       for s, o in pairs for f in (fwd, back)])
    out, seen = [], set()
    while queues and len(out) < limit:                 # round-robin, so different relations show up
        for q in list(queues):
            while q and q[0]['question'].lower() in seen:
                q.pop(0)
            if q:
                item = q.pop(0)
                seen.add(item['question'].lower())
                out.append(item)
            if not q:
                queues.remove(q)
            if len(out) >= limit:
                break
    return {'questions': out}


def nl_to_pln_query(question: str, names: dict | None = None, predicates: list | None = None) -> str | None:
    """PeTTaChainer query for a question, or None if no form matches."""
    return plan_query(question, names, predicates)[0]


def extract_entities(question: str) -> list[str]:
    """Capitalised or ID-like tokens in a question, for highlighting the answer."""
    tokens = re.findall(r'\b([A-Z][A-Z0-9_-]{2,}|HGNC_\d+|CHEBI_\d+|MESH_\d+|HP_\d+)\b', question)
    return list(dict.fromkeys(tokens))  # deduplicate, preserve order
