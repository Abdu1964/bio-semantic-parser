"""A long or partly invalid LLM reply must not lose the chunk's valid relations."""
import json
from types import SimpleNamespace

import pytest

from src.extraction import extractor


def _rel(subject, obj, relation="downregulates"):
    return {"extraction_viable": True, "subject_name": subject, "subject_type": "PROTEIN",
            "relation": relation, "object_name": obj, "object_type": "GENE", "negated": False,
            "confidence": 0.9, "reasoning": f"The text states that {subject} lowers {obj} expression in RA-FLS cells.",
            "species": "", "tissue": "", "condition": "", "effect_size": ""}


def _reply(rels):
    return json.dumps({"relations": rels})


@pytest.fixture
def fake_llm(monkeypatch):
    """Answers each call from a function of the chunk text; records the texts sent."""
    sent = []

    def install(answer):
        def create(**kw):
            text = kw["messages"][1]["content"]
            sent.append(text)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer(text)))])
        monkeypatch.setattr(extractor, "_client",
                            lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        monkeypatch.setattr(extractor.httpx, "get", lambda *a, **k: None)
        monkeypatch.setattr(extractor, "_log_rejected", lambda *a, **k: None)
        return sent
    return install


def test_one_invalid_relation_keeps_the_others(fake_llm):
    bad = dict(_rel("RTD-1", "IL6"), relation="not_a_relation")
    fake_llm(lambda text: _reply([_rel("RTD-1", "CCL7"), bad, _rel("RTD-1", "CCL20")]))
    result = extractor.extract({"text": "RTD-1 suppressed CCL7. RTD-1 suppressed CCL20."})
    assert not result.rejected
    assert {r.object_name for r in result.relations} == {"CCL7", "CCL20"}


def test_cut_off_reply_keeps_complete_relations():
    raw = _reply([_rel("RTD-1", "CCL7"), _rel("RTD-1", "CCL20")])[:-2] + ', {"subject_name": "RTD-1", "rel'
    assert [r.object_name for r in extractor._salvage_relations(raw)] == ["CCL7", "CCL20"]


def test_long_chunk_is_split_until_replies_fit(fake_llm):
    genes = [f"GENE{i}" for i in range(8)]
    text = ". ".join(f"RTD-1 suppressed {g}" for g in genes) + "."

    def answer(prompt):
        found = [g for g in genes if f"suppressed {g}" in prompt]
        full = _reply([_rel("RTD-1", g) for g in found])
        return full if len(found) <= 2 else full[: len(full) // 2]      # long replies are cut off

    fake_llm(answer)
    result = extractor.extract({"text": text})
    assert not result.rejected
    assert {r.object_name for r in result.relations} == set(genes)
