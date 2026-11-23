"""PLN reasoner: orchestrates forward materialization + backward query for a question."""
import time
from typing import TYPE_CHECKING

from pln.query.nl_to_pln import plan_query

if TYPE_CHECKING:
    from pln.config import PLNConfig
    from pln.chaining.forward_chainer import ForwardChainer
    from pln.chaining.backward_chainer import BackwardChainer




class ReasoningResult:
    """Structured output from a PLN reasoning run."""
    __slots__ = ('query', 'pln_query', 'proofs', 'answers', 'inferred_atoms', 'trace')

    def __init__(self):
        self.query: str = ''
        self.pln_query: str = ''
        self.proofs: list[str] = []
        self.answers: list[dict] = []
        self.inferred_atoms: list[str] = []
        self.trace: list[dict] = []          # what PLN did, step by step

    def to_dict(self) -> dict:
        return {
            'query':          self.query,
            'pln_query':      self.pln_query,
            'proofs':         self.proofs,
            'answers':        self.answers,
            'inferred_atoms': self.inferred_atoms,
            'trace':          self.trace,
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

    def invalidate(self) -> None:
        """Facts were added: derive again on the next question."""
        self._materialized = False

    def ensure_materialized(self) -> None:
        """Run forward materialization once; subsequent calls are no-ops."""
        if not self._materialized:
            self._forward.materialize()
            self._materialized = True

    def ask(self, question: str, raw_pln_query: str | None = None,
            names: dict | None = None, predicates: list[str] | None = None) -> ReasoningResult:
        """Answer a question; raw_pln_query skips translation. names: entity symbol → its names and
        synonyms; predicates: the relations in the atomspace, whose question forms are tried."""
        result = ReasoningResult()
        result.query = question
        def log(step, detail, t0=None, explain='', data=None):
            result.trace.append({'step': step, 'detail': detail, 'explain': explain, 'data': data,
                                 'ms': round((time.perf_counter() - t0) * 1000) if t0 else None})

        if raw_pln_query:
            pln_query, filters = raw_pln_query, {}
            log('Question', 'raw PeTTa query given, no translation')
        else:
            pln_query, filters, notes = plan_query(question, names, predicates)
            log('Question', notes[0], explain=(
                "Each relation in the atomspace has two question forms made from its own words (BindsTo: 'What does X "
                "bind to?' and 'What binds to X?'); the form that fits gives the relation and which side is asked for: "
                "$target (what X affects) or $source (what affects X)."))
            for note in notes[1:]:
                log('Name', note, explain=(
                    "An entity in the atomspace is its ontology ID, so the same entity from any paper is one atom. The name "
                    "is matched to the entities' names and synonyms: exactly first, else every entity whose name holds all "
                    "the words. One match becomes that entity; several become a variable and only their answers are kept."))
        if not pln_query:
            return result
        result.pln_query = pln_query
        log('PeTTa query', pln_query, explain=(
            "(: $prf (Predicate subject object) $tv) asks PeTTaChainer for any proof $prf of the statement together with "
            "its truth value $tv. Words starting with $ are variables PeTTaChainer fills in; the others must match."))

        derived = self._forward.derived()
        detail = (f"{len(derived)} relation(s) derived by the last inference run, from {len(self._forward.rules())} rules"
                  if self._forward.materialized else
                  f"not needed for a question: backward chaining applies the {len(self._forward.rules())} rules itself")
        log('Forward chaining', detail,
            explain=("Forward chaining starts from the facts and applies every rule to list all relations they imply; "
                     "Run PLN Inference does that and shows them. A question does not need it: backward chaining below "
                     "applies the same rules, starting from the question. Each rule carries its own STV: how reliable "
                     "the pattern is."),
            data={'rules': self._forward.rule_info(),
                  'derived': [{'atom': a, **self._forward.derivations().get(a.split()[1], {})} for a in derived]})

        t0 = time.perf_counter()
        proofs = self._backward.query(pln_query)
        result.proofs = proofs
        log('Backward chaining', f"{len(proofs)} proof(s) found for the query", t0,
            explain=("Backward chaining starts from the query and works back: it matches the statement against facts "
                     "directly, or against a rule's conclusion and then proves that rule's premises the same way. "
                     "Every way it finds is one proof."),
            data={'proofs': proofs})

        answers = [a for a in (self._parse_proof(p) for p in proofs if p) if a]
        if filters:
            kept = [a for a in answers if all(a[role] in allowed for role, allowed in filters.items())]
            log('Filter', f"kept {len(kept)} of {len(answers)} answer(s) about the matched entities",
                explain="The name matched several entities, so only answers about those entities are kept.")
            answers = kept
        result.answers = sorted(answers, key=lambda a: -a['confidence'])   # rule-derived ones keep their low confidence
        for a in result.answers:
            log('Answer', self._explain(a), explain=(
                "A proof that is a fact's own name means the paper states it; its STV is the fact's. A proof "
                "(by rule (conjunction …)) means a rule derived it from the premises inside; PLN combines the premises' "
                "STVs with the rule's. Strength: how likely the statement is true. Confidence: how much evidence backs it."),
                data={'proof': a['proof'], 'inferred': a['inferred']})

        result.inferred_atoms = derived
        return result

    def _explain(self, answer: dict) -> str:
        """How an answer was proved: a stated fact, or a rule over its premises."""
        from pln.chaining.forward_chainer import _proof_parts
        head = f"{answer['subject']} {answer['predicate']} {answer['object']} (stv {answer['strength']:.2f} {answer['confidence']:.2f})"
        parts = _proof_parts(answer['proof'])
        if not parts:
            return f"{head} — stated fact {answer['proof']}"
        rule, premises = parts
        used = ' + '.join(self._forward.premise_label(p) for p in premises)
        return f"{head} — rule {rule} over {used}"

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
            'proof':      prf,                       # a fact's name, or (by rule (conjunction premises…))
            'result':     proof,                     # the whole line PeTTaChainer returned
        }
