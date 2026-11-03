"""Request pace and API key per host, from the registered sources (config/sources.yaml)."""
import os
import threading
import time
from urllib.parse import urlparse

_RATE_RETRIES = int(os.getenv("RATE_LIMIT_RETRIES", "4"))     # tries again after 'too many requests' (HTTP 429)
_lock = threading.Lock()
_next_slot: dict = {}
_hosts = None


def _sources(host: str) -> list:
    """Registered sources served from this host."""
    global _hosts
    if _hosts is None:
        hosts = {}
        try:
            from src.registry.registry import SourceRegistry
            for s in SourceRegistry().get_all_sources():
                h = urlparse(s.get("base_url") or "").netloc
                if h:
                    hosts.setdefault(h, []).append(s)
        except Exception:
            pass
        _hosts = hosts
    return _hosts.get(host, [])


def api_key(host: str) -> str:
    """The key of the first registered source for this host whose key variable is set."""
    for s in _sources(host):
        key = os.getenv(s.get("api_key_env") or "", "")
        if key:
            return key
    return ""


def rate(host: str) -> float:
    """Requests per second: the registered rate_limit, or rate_limit_with_key when a key is set; 0 = unpaced."""
    keyed = bool(api_key(host))
    rates = [(keyed and s.get("rate_limit_with_key")) or s.get("rate_limit") for s in _sources(host)]
    rates = [r for r in rates if r]
    return min(rates) if rates else 0


def wait_turn(host: str) -> None:
    """Wait for this host's next request slot, shared by all threads."""
    r = rate(host)
    if not r:
        return
    with _lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(host, 0))
        _next_slot[host] = slot + 1 / r
    if slot > now:
        time.sleep(slot - now)


def _retry_after(resp, attempt: int) -> float:
    try:
        return min(float(resp.headers.get("Retry-After")), 30)
    except (TypeError, ValueError, AttributeError):
        return float(attempt + 1)


def send(fn, url, *args, **kwargs):
    """fn(url, …) at the host's pace, with its key; 'too many requests' waits as asked and tries again."""
    host = urlparse(url).netloc
    key = api_key(host)
    if key and isinstance(kwargs.get("params") or {}, dict):
        kwargs["params"] = {**(kwargs.get("params") or {}), "api_key": key}
    for attempt in range(_RATE_RETRIES + 1):
        wait_turn(host)
        resp = fn(url, *args, **kwargs)
        if getattr(resp, "status_code", 200) != 429 or attempt == _RATE_RETRIES:
            return resp
        time.sleep(_retry_after(resp, attempt))
