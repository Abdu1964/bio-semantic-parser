"""
Layer 4 — Negation Detector

NLI cross-encoder that classifies each entity's containing clause against a
positive hypothesis (confirmed finding; negated if contradicted, with a syntactic
negation or some negative entailment as backing) and negative hypotheses
(finding absent; negated if entailed).

Sentences are split at contrastive conjunctions ("but not", "however",
"although"…) before classification so that entities in the positive branch
of a contrastive sentence are not incorrectly flagged as negated.
"""
import os
import re

from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
import threading
_infer_lock = threading.Lock()


# Contrastive conjunctions
_CONTRASTIVE = re.compile(
    r'\b(but not|however|although|whereas|while yet|despite|'
    r'in contrast|on the other hand|except|rather than|'
    r'but(?!\s+not)|yet(?!\s+not))\b',
    re.IGNORECASE,
)

def _extract_entity_clause(sentence: str, entity_text: str) -> str:
    """Return the sub-clause containing entity_text, or the full sentence if no split point."""
    parts = _CONTRASTIVE.split(sentence)
    if len(parts) <= 1:
        return sentence
    for part in parts:
        # Skip contrastive conjunctions themselves
        if len(part.split()) <= 2:
            continue
        # Skip very short parts
        if entity_text.lower() in part.lower():
    
            # Remove leading/trailing whitespace and punctuation from the clause
            stripped = part.strip().strip(",;.")
            # Long enough to be a clause
            if len(stripped) > 10:
                return stripped

    return sentence


_DEFAULT_HYPOTHESIS = (
    "This study demonstrates a confirmed biological effect, finding, or association."
)

# Second check: many definite negatives ("did not affect", "no difference") read as neutral,
# not as a contradiction, against the positive hypothesis. These catch them by entailment.
_NEGATIVE_HYPOTHESES = (
    "This study demonstrates that an effect, finding, or association was absent.",
    "It did not have an effect.",
)


class NegationDetector:
    _MODEL     = "cross-encoder/nli-MiniLM2-L6-H768"
    try:
        _THRESHOLD = float(os.getenv("NEGATION_THRESHOLD", "0.45"))
    except ValueError:
        _THRESHOLD = 0.45
    # Calibrated against biomedical test sentences:
    # 0.45 catches "no improvement" (0.89), "NOT recommended" (0.47),
    # while correctly passing "contributes to" (0.03), "enables" (0.04).
    _NEG_THRESHOLD = 0.50   # entailment of a negative hypothesis; on 225 labelled sentences from
                            # 5 papers: negatives caught 16/40 → 26/40, no new false positives on positive findings
    # A contradiction alone also fires on "loss", "lack of", "may lead to"; it counts only with a syntactic
    # negation or some negative entailment. Labelled set: other sentences flagged 35 → 20, positives 5 → 3,
    # negatives 26 → 24.
    _BACKING = 0.20

    def __init__(self, hypothesis: str = _DEFAULT_HYPOTHESIS,
                 negative_hypotheses: tuple = _NEGATIVE_HYPOTHESES):
        self._hypothesis = hypothesis
        self._negative_hypotheses = tuple(negative_hypotheses)
        import os as _os
        _os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        self._tokenizer  = AutoTokenizer.from_pretrained(
            self._MODEL, local_files_only=True
        )
        self._model      = AutoModelForSequenceClassification.from_pretrained(
            self._MODEL,
            local_files_only=True,
            device_map=None,          # prevent accelerate meta tensor path
            low_cpu_mem_usage=False,  # force eager allocation
        )
        self._model.eval()
        # Label order: {0: contradiction, 1: entailment, 2: neutral}
    
    def _is_negated(self, clause: str, neg_arc: bool = False) -> tuple:
        """Negated if the clause entails a negative hypothesis, or contradicts the positive one
        with backing (a syntactic negation in the clause, or some negative entailment)."""
        hypotheses = (self._hypothesis, *self._negative_hypotheses)
        inputs = self._tokenizer(
            [clause] * len(hypotheses), list(hypotheses),
            return_tensors="pt",
            truncation=True,
            padding=True,
            max_length=512,
        )
        with _infer_lock, torch.no_grad():   # shared model — one forward pass at a time
            probs = torch.softmax(self._model(**inputs).logits, dim=-1)
        contradiction_score = float(probs[0][0])
        negative_score = max((float(p[1]) for p in probs[1:]), default=0.0)
        backed = neg_arc or negative_score > self._BACKING
        is_neg = negative_score > self._NEG_THRESHOLD or (contradiction_score > self._THRESHOLD and backed)
        return is_neg, round(max(contradiction_score, negative_score), 3)

    def _find_sentence(self, doc, entity_text: str, start_char: int):
        """The sentence span holding the entity (the whole doc if none)."""
        if start_char >= 0:
            for sent in doc.sents:
                if sent.start_char <= start_char < sent.end_char:
                    return sent
        # PubTator3 entities lack char offsets — fall back to text search
        for sent in doc.sents:
            if entity_text.lower() in sent.text.lower():
                return sent
        return doc

    @staticmethod
    def _has_neg_arc(span, clause: str) -> bool:
        """A syntactic negation (spaCy dependency label "neg") inside the clause."""
        start = span.text.find(clause)
        if start < 0:
            return False
        offset = getattr(span, "start_char", 0)
        return any(t.dep_ == "neg" and start <= t.idx - offset < start + len(clause) for t in span)

    def process(self, entities: list, doc) -> dict:
        clause_cache: dict = {}
        updated = []
        negated = []

        for ent in entities:
            span     = self._find_sentence(doc, ent["text"], ent.get("start", -1))
            clause   = _extract_entity_clause(span.text, ent["text"])

            if clause not in clause_cache:
                clause_cache[clause] = self._is_negated(clause, neg_arc=self._has_neg_arc(span, clause))
            is_neg, confidence = clause_cache[clause]

            assertion = "ABSENT" if is_neg else "PRESENT"
            entry = {**ent, "negated": is_neg, "assertion": assertion, "confidence": confidence}
            updated.append(entry)
            if is_neg:
                negated.append(entry)

        return {
            "entities":         updated,
            "has_negation":     bool(negated),
            "negated_entities": negated,
        }
