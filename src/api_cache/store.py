"""Permanent local copy of external API answers.

Each distinct request (method, URL, parameters, body) is answered online once; the API's answer —
status, content type, encoding and raw body — is stored in data/api_cache/<host>.db and returned for
every later identical request, exactly as the API sent it. Only real answers (HTTP 200) are kept:
timeouts, rate limits and server errors are never stored, so they are retried online next time.
Entries never expire (API_CACHE_DIR, API_CACHE=false to bypass).
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from urllib.parse import urlparse

import requests

_DIR = os.getenv("API_CACHE_DIR", "data/api_cache")
_ENABLED = os.getenv("API_CACHE", "true").lower() == "true"
_SECRET_PARAMS = {"api_key", "apikey", "key", "email", "tool", "token"}   # credentials don't change the answer
_lock = threading.Lock()
_conns: dict = {}
_stats = {"local": 0, "online": 0, "stored": 0}   # this run, for the log


# One folder per service, named as people know it; any other service gets a folder named after its host.
_SERVICES = (("rest.ensembl.org", "", "ensembl"), ("www.ncbi.nlm.nih.gov", "/research/pubtator", "pubtator"),
             ("eutils.ncbi.nlm.nih.gov", "", "ncbi"), ("www.ncbi.nlm.nih.gov", "", "ncbi"),
             ("www.ebi.ac.uk", "/ols", "ols"), ("www.ebi.ac.uk", "/spot/oxo", "oxo"),
             ("rest.genenames.org", "", "hgnc"), ("rxnav.nlm.nih.gov", "", "rxnorm"),
             ("www.wikidata.org", "", "wikidata"), ("pubchem.ncbi.nlm.nih.gov", "", "pubchem"),
             ("rest.uniprot.org", "", "uniprot"), ("id.nlm.nih.gov", "/mesh", "mesh"), ("mygene.info", "", "mygene"))


def _service(url: str, params=None) -> str:
    """Folder for a request: the service, then the database or ontology it asks (ncbi_gene, ols/mondo)."""
    u = urlparse(url)
    name = next((n for host, path, n in _SERVICES if u.netloc == host and u.path.startswith(path)), u.netloc)
    p = dict(params) if isinstance(params, (dict, list, tuple)) else {}
    if name == "ncbi" and p.get("db"):
        return f"ncbi_{p['db']}"
    if name == "ncbi":                                   # db given in the URL itself (…?db=gene&term=…)
        db = dict(x.split("=", 1) for x in u.query.split("&") if "=" in x).get("db")
        return f"ncbi_{db}" if db else name
    if name == "ols":
        return os.path.join("ols", str(p.get("ontology") or "_all").lower())
    return name


def _db(url: str, params=None) -> sqlite3.Connection:
    service = _service(url, params)
    if service not in _conns:
        folder = os.path.join(_DIR, service)
        os.makedirs(folder, exist_ok=True)
        conn = sqlite3.connect(os.path.join(folder, "responses.db"), check_same_thread=False, timeout=30)
        conn.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, method TEXT, url TEXT, "
                     "request TEXT, status INTEGER, content_type TEXT, encoding TEXT, body BLOB, fetched_at REAL)")
        _conns[service] = conn
    return _conns[service]


def _request_of(method: str, url: str, kwargs: dict) -> str:
    """The request as a canonical string: same request → same key, whatever the argument order."""
    params = kwargs.get("params") or {}
    params = sorted((k, str(v)) for k, v in (params.items() if isinstance(params, dict) else params)
                    if k.lower() not in _SECRET_PARAMS)
    accept = {k: v for k, v in (kwargs.get("headers") or {}).items() if k.lower() in ("accept", "content-type")}
    return json.dumps({"method": method, "url": url, "params": params, "accept": accept,
                       "json": kwargs.get("json"), "data": kwargs.get("data")}, sort_keys=True, default=str)


def lookup(method: str, url: str, **kwargs):
    """The stored answer to this request as a requests.Response, or None."""
    if not _ENABLED or kwargs.get("stream"):
        return None
    request = _request_of(method, url, kwargs)
    key = hashlib.sha256(request.encode()).hexdigest()
    try:
        with _lock:
            row = _db(url, kwargs.get("params")).execute(
                "SELECT status, content_type, encoding, body FROM responses WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        _stats["online"] += 1
        return None
    _stats["local"] += 1
    resp = requests.models.Response()
    resp.status_code, resp.encoding, resp._content, resp.url = row[0], row[2], row[3], url
    resp.headers["Content-Type"] = row[1] or ""
    resp.headers["X-Api-Cache"] = "hit"
    return resp


def _complete(resp) -> bool:
    """A body worth keeping: not empty, and parseable when the service says it is JSON."""
    body = getattr(resp, "content", b"") or b""
    if not body.strip():
        return False
    if "json" in (resp.headers.get("Content-Type", "") or "").lower():
        try:
            json.loads(body)
        except ValueError:
            return False                        # a cut-off or error page sent as 200
    return True


def remember(method: str, url: str, resp, **kwargs) -> None:
    """Store a real, complete answer (HTTP 200) permanently."""
    if not _ENABLED or kwargs.get("stream") or getattr(resp, "status_code", 0) != 200 or not _complete(resp):
        return
    request = _request_of(method, url, kwargs)
    key = hashlib.sha256(request.encode()).hexdigest()
    try:
        with _lock:
            conn = _db(url, kwargs.get("params"))
            conn.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?,?,?,?)",
                         (key, method, url, request, resp.status_code, resp.headers.get("Content-Type", ""),
                          resp.encoding, resp.content, time.time()))
            conn.commit()
        _stats["stored"] += 1
    except sqlite3.Error:
        pass


def stats() -> dict:
    return dict(_stats)


def reset_stats() -> None:
    for k in _stats:
        _stats[k] = 0


def cached_get(url: str, **kwargs):
    """requests.get that answers from the local copy when it has one."""
    hit = lookup("GET", url, **kwargs)
    if hit is not None:
        return hit
    from src.registry import host_limits
    resp = host_limits.send(requests.get, url, **kwargs)     # registered pace and key
    remember("GET", url, resp, **kwargs)
    return resp
