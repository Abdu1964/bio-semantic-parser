"""Fact alignment asks several pairs per LLM call."""
from src.postextraction import fact_alignment as F


def _f(s, rel, o, sent):
    return {"subject_name": s, "subject_id": "A:1", "relation": rel, "object_name": o, "object_id": "B:1",
            "subject_evidence": sent, "object_evidence": sent}


def test_pairs_batched_in_one_call(monkeypatch):
    calls = []
    monkeypatch.setattr(F, "_verdict_cache", {})
    monkeypatch.setattr(F, "call_llm", lambda msgs, **k: calls.append(1) or
                        '{"pairs": [{"pair": 1, "verdict": "SAME", "keep": "B", "reason": "x"}, {"pair": 2, "verdict": "DIFFERENT", "keep": "A", "reason": "y"}]}')
    pairs = [(_f("X", "promotes", "Y", "X promotes Y."), _f("X", "upregulates", "Y", "X raises Y.")),
             (_f("X", "causes", "Y", "X causes Y."), _f("X", "associates_with", "Y", "X is linked to Y."))]
    out = F._same_claims(pairs)
    assert len(calls) == 1 and [v["verdict"] for v in out] == ["SAME", "DIFFERENT"] and out[0]["keep"] == "B"
    assert F._same_claims(pairs) == out and len(calls) == 1          # cached: not asked again
