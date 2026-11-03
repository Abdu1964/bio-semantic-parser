"""Lookups that fail on the network are retried, and the run log says so (brief and detailed)."""
from src.postextraction import entity_normalization as E


def test_retry_rounds_are_reported(monkeypatch):
    calls = {"n": 0}

    def fake_normalize(record, chunk):
        calls["n"] += 1
        failed = calls["n"] <= 2                                   # first pass fails, retry succeeds
        return {**record, "subject_network_degraded": failed, "object_network_degraded": False}

    monkeypatch.setattr(E, "normalize_record", fake_normalize)
    monkeypatch.setattr(E, "merge_synonym_concepts", lambda out: out)
    monkeypatch.setattr(E, "_NETWORK_RETRY_WAIT", 0)
    E._failed_requests["rest.ensembl.org"] = 4
    messages = []
    records = [{"subject_name": "TGFB1", "object_name": "RA"}, {"subject_name": "IL6", "object_name": "RA"}]
    E.normalize_records_parallel(records, lambda r: {}, workers=1, notify=messages.append)
    text = "\n".join(messages)
    assert "2 record(s) hit network failures" in text and "retry 1/3" in text
    assert "waiting: IL6, TGFB1" in text
    assert "Retry 1/3: 2 resolved, 0 still failing" in text
    assert "All lookups resolved after retry 1/3" in text
