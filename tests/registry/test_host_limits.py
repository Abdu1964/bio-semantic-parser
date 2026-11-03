"""Registered pace and key per host; 'too many requests' waits and tries again."""
import time

from src.registry import host_limits

_NCBI = "eutils.ncbi.nlm.nih.gov"


class _Resp:
    def __init__(self, code, retry_after=None):
        self.status_code = code
        self.headers = {"Retry-After": retry_after} if retry_after else {}


def _registry(monkeypatch, sources):
    monkeypatch.setattr(host_limits, "_hosts", {_NCBI: sources})
    monkeypatch.setattr(host_limits, "_next_slot", {})


def test_rate_without_and_with_key(monkeypatch):
    _registry(monkeypatch, [{"rate_limit": 3, "rate_limit_with_key": 10, "api_key_env": "TEST_NCBI_KEY"}])
    monkeypatch.delenv("TEST_NCBI_KEY", raising=False)
    assert host_limits.rate(_NCBI) == 3 and host_limits.api_key(_NCBI) == ""
    monkeypatch.setenv("TEST_NCBI_KEY", "k")
    assert host_limits.rate(_NCBI) == 10 and host_limits.api_key(_NCBI) == "k"
    assert host_limits.rate("unregistered.org") == 0


def test_key_sent_as_param(monkeypatch):
    _registry(monkeypatch, [{"rate_limit": 100, "api_key_env": "TEST_NCBI_KEY"}])
    monkeypatch.setenv("TEST_NCBI_KEY", "k")
    seen = {}
    host_limits.send(lambda url, **kw: seen.update(kw) or _Resp(200), f"https://{_NCBI}/x", params={"db": "gene"})
    assert seen["params"] == {"db": "gene", "api_key": "k"}


def test_requests_paced(monkeypatch):
    _registry(monkeypatch, [{"rate_limit": 20}])
    t = time.monotonic()
    for _ in range(5):
        host_limits.wait_turn(_NCBI)
    assert time.monotonic() - t >= 4 / 20 - 0.01


def test_429_waits_and_retries(monkeypatch):
    _registry(monkeypatch, [])
    monkeypatch.setattr(host_limits.time, "sleep", lambda s: None)
    answers = iter([_Resp(429, "1"), _Resp(429), _Resp(200)])
    assert host_limits.send(lambda url, **kw: next(answers), f"https://{_NCBI}/x").status_code == 200
