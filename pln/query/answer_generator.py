"""Convert PLN ReasoningResult into human-readable answer text."""
from pln.query.nl_to_pln import _words
from pln.query.reasoner import ReasoningResult




def _clean(expr: str) -> str:
    """Strip outer parentheses and type prefix from an entity expression."""
    e = expr.strip().strip('()')
    parts = e.split(None, 1)
    if len(parts) == 2 and parts[0].islower():
        return parts[1]
    return e


def _kind(expr: str) -> str:
    """Type prefix of an entity expression: '(disease stroke)' → 'disease'."""
    parts = expr.strip().strip('()').split(None, 1)
    return parts[0] if len(parts) == 2 and parts[0].islower() else ''


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
        sentence = f"{subj} {' '.join(_words(pred))} {obj}"
        how = "inferred" if ans.get('inferred') else "fact"
        lines.append(f"• {sentence}  ({how}; strength={s:.2f}, confidence={c:.2f})")

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
                'subject':    a.get('subject_name') or _clean(a['subject']),
                'object':     a.get('object_name') or _clean(a['object']),
                'papers':     a.get('papers', []),
                'subject_type': a.get('subject_type') or _kind(a['subject']),
                'object_type':  a.get('object_type') or _kind(a['object']),
                'subject_id':   a['subject'],
                'object_id':    a['object'],
                'strength':   a['strength'],
                'confidence': a['confidence'],
                'inferred':   a.get('inferred', False),
                'proof':      a.get('proof', ''),
            }
            for a in result.answers
        ],
        'inferred_count': len(result.inferred_atoms),
        'trace':          result.trace,
    }
