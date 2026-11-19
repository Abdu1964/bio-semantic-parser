"""PLN reasoner: orchestrates forward materialization + backward query for a question."""
from typing import TYPE_CHECKING

from pln.query.nl_to_pln import nl_to_pln_query

if TYPE_CHECKING:
    from pln.config import PLNConfig
    from pln.chaining.forward_chainer import ForwardChainer
    from pln.chaining.backward_chainer import BackwardChainer




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
        answers = [a for a in (self._parse_proof(p) for p in proofs if p) if a]
        result.answers = sorted(answers, key=lambda a: -a['confidence'])   # rule-derived ones keep their low confidence

        result.inferred_atoms = self._forward.run()          # atoms the rules derived
        return result

    def _parse_proof(self, proof: str) -> dict | None:
        """Subject, predicate, object and STV of a query result; rule-derived ones are marked inferred."""
        from pln.chaining.forward_chainer import _parse_result
        parsed = _parse_result(proof)
        if not parsed:
            return None
        prf, predicate, subject, obj, strength, confidence = parsed
        return {
            'predicate':  predicate,
            'subject':    subject,
            'object':     obj,
            'strength':   round(strength,   3),
            'confidence': round(confidence, 3),
            'inferred':   prf.startswith('(by '),
            'proof':      proof,
        }
