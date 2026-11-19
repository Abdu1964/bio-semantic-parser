"""PLN reasoner: orchestrates forward materialization + backward query for a question."""
import re
from typing import TYPE_CHECKING

from pln.query.nl_to_pln import nl_to_pln_query

if TYPE_CHECKING:
    from pln.config import PLNConfig
    from pln.chaining.forward_chainer import ForwardChainer
    from pln.chaining.backward_chainer import BackwardChainer


_STV_RE  = re.compile(r'\(STV\s+([\d.]+)\s+([\d.]+)\)')
_ATOM_RE = re.compile(r'\(:\s+\S+\s+\((\w+)\s+(.*?)\)\s+\(STV', re.S)


class ReasoningResult:
    """Structured output from a PLN reasoning run."""
    __slots__ = ('query', 'pln_query', 'proofs', 'answers', 'inferred_atoms')

    def __init__(self):
        self.query: str = ''
        self.pln_query: str = ''
        self.proofs: list[str] = []
        self.answers: list[dict] = []
        self.inferred_atoms: list[str] = []

    def to_dict(self) -> dict:
        return {
            'query':          self.query,
            'pln_query':      self.pln_query,
            'proofs':         self.proofs,
            'answers':        self.answers,
            'inferred_atoms': self.inferred_atoms,
        }


class PLNReasoner:
    """Combines forward chaining + backward query to answer NL questions over the KG."""

    def __init__(self, cfg: "PLNConfig",
                 forward: "ForwardChainer",
                 backward: "BackwardChainer"):
        self._cfg     = cfg
        self._forward = forward
        self._backward = backward
        self._materialized = False

    def ensure_materialized(self) -> None:
        """Run forward materialization once; subsequent calls are no-ops."""
        if not self._materialized:
            self._forward.materialize()
            self._materialized = True

    def ask(self, question: str, raw_pln_query: str | None = None,
            entity_map: dict | None = None) -> ReasoningResult:
        """Answer a question; raw_pln_query skips translation, entity_map resolves names."""
        result = ReasoningResult()
        result.query = question

        pln_query = raw_pln_query or nl_to_pln_query(question, entity_map)
        if not pln_query:
            return result
        result.pln_query = pln_query

        self.ensure_materialized()
        proofs = self._backward.query(pln_query)
        result.proofs = proofs
        result.answers = [self._parse_proof(p) for p in proofs if p]
        result.answers = [a for a in result.answers if a]

        # Inferred atoms not among the original facts
        result.inferred_atoms = self._forward.all_facts()
        return result

    def _parse_proof(self, proof: str) -> dict | None:
        """Extract subject, predicate, object, strength, confidence from a proof trace."""
        stv = _STV_RE.search(proof)
        m   = _ATOM_RE.search(proof)
        if not m:
            return None
        predicate = m.group(1)
        args_str  = m.group(2).strip()
        args      = _split_top(args_str)
        strength   = float(stv.group(1)) if stv else 0.0
        confidence = float(stv.group(2)) if stv else 0.0
        if confidence < self._cfg.min_confidence:
            return None
        return {
            'predicate':  predicate,
            'subject':    args[0] if args else '',
            'object':     args[1] if len(args) > 1 else '',
            'strength':   round(strength,   3),
            'confidence': round(confidence, 3),
            'proof':      proof,
        }


def _split_top(s: str) -> list[str]:
    """Split on top-level spaces only (ignore nested parens)."""
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch == '(':
            depth += 1; buf.append(ch)
        elif ch == ')':
            depth -= 1; buf.append(ch)
        elif ch == ' ' and depth == 0:
            t = ''.join(buf).strip()
            if t: parts.append(t)
            buf = []
        else:
            buf.append(ch)
    t = ''.join(buf).strip()
    if t: parts.append(t)
    return parts
