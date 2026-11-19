"""Convert PLN ReasoningResult into human-readable answer text."""
from pln.query.reasoner import ReasoningResult


_PRED_TEMPLATES: dict[str, str] = {
    'Inhibits':          '{subject} inhibits {object}',
    'Activates':         '{subject} activates {object}',
    'Binds_To':          '{subject} binds to {object}',
    'Causes':            '{subject} causes {object}',
    'Is_Associated_With':'{subject} is associated with {object}',
    'Treats':            '{subject} treats {object}',
    'Upregulates':       '{subject} upregulates {object}',
    'Downregulates':     '{subject} downregulates {object}',
    'Is_Biomarker_Of':   '{subject} is a biomarker of {object}',
    'Protects_Against':  '{subject} protects against {object}',
    'IdenticalTo':       '{subject} may refer to the same entity as {object}',
    'Encodes':           '{subject} encodes {object}',
    'Co_Expressed_With': '{subject} is co-expressed with {object}',
}


def _clean(expr: str) -> str:
    """Strip outer parentheses and type prefix from an entity expression."""
    e = expr.strip().strip('()')
    parts = e.split(None, 1)
    if len(parts) == 2 and parts[0].islower():
        return parts[1]
    return e


def generate_answer(result: ReasoningResult, max_answers: int = 10) -> str:
    """A short readable answer from a ReasoningResult; '' when there is none."""
    if not result.answers:
        if result.pln_query:
            return f"No PLN proof found for: {result.query}"
        return f"Could not translate question to PLN query: {result.query}"

    lines = [f"PLN answer for: {result.query}", ""]
    seen = set()
    for ans in result.answers[:max_answers]:
        pred  = ans.get('predicate', '')
        subj  = _clean(ans.get('subject',  ''))
        obj   = _clean(ans.get('object',   ''))
        s     = ans.get('strength',   0.0)
        c     = ans.get('confidence', 0.0)
        key = (pred, subj, obj)
        if key in seen:
            continue
        seen.add(key)
        tmpl = _PRED_TEMPLATES.get(pred, '{subject} — {predicate} — {object}')
        sentence = tmpl.format(subject=subj, object=obj, predicate=pred)
        lines.append(f"• {sentence}  (strength={s:.2f}, confidence={c:.2f})")

    if len(result.answers) > max_answers:
        lines.append(f"  … and {len(result.answers) - max_answers} more results")
    lines.append("")
    lines.append(f"[PLN query: {result.pln_query}]")
    return "\n".join(lines)


def generate_json(result: ReasoningResult) -> dict:
    """
    Return a structured dict suitable for API JSON serialization.
    """
    return {
        'question':    result.query,
        'pln_query':   result.pln_query,
        'answer_count': len(result.answers),
        'answers': [
            {
                'predicate':  a['predicate'],
                'subject':    _clean(a['subject']),
                'object':     _clean(a['object']),
                'strength':   a['strength'],
                'confidence': a['confidence'],
            }
            for a in result.answers
        ],
        'inferred_count': len(result.inferred_atoms),
    }
