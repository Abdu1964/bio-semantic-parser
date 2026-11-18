"""Full local copies of lookup sources, checked before the stored answers and before going online.

Requests a copy can answer completely are answered here in the service's own response format,
so callers cannot tell the difference: OLS4 exact search per ontology, HGNC fetch by (previous/alias) symbol.
NCBI Gene and NCBI Taxonomy are used through their own helpers (ncbi_gene.find, ontology.taxon_candidates).
"""
import json
import threading
from urllib.parse import unquote, urlparse

import requests

from src.local_sources import hgnc, ncbi_gene, ontology

_stats = {"answered": 0}
_lock = threading.Lock()


def _response(url: str, payload: dict) -> requests.Response:
    resp = requests.models.Response()
    resp.status_code, resp.encoding, resp.url = 200, "utf-8", url
    resp._content = json.dumps(payload).encode()
    resp.headers["Content-Type"] = "application/json"
    resp.headers["X-Local-Source"] = "copy"
    with _lock:
        _stats["answered"] += 1
    return resp


def answer(method: str, url: str, **kwargs):
    """The response a built local copy gives for this request, or None to ask elsewhere."""
    if method != "GET":
        return None
    u, params = urlparse(url), dict(kwargs.get("params") or {})
    if u.netloc == "www.ebi.ac.uk" and u.path.endswith("/ols4/api/search"):
        onto = str(params.get("ontology") or "")
        if str(params.get("exact")).lower() != "true" or not onto or "," in onto:
            return None                                   # loose and cross-ontology searches stay online
        docs = ontology.search_exact(onto, str(params.get("q") or ""), int(params.get("rows") or 10))
        return None if docs is None else _response(url, {"response": {"numFound": len(docs), "docs": docs}})
    if u.netloc == "rest.genenames.org" and u.path.startswith("/fetch/"):
        parts = u.path.split("/")
        if len(parts) >= 4:
            docs = hgnc.fetch(parts[2], unquote("/".join(parts[3:])))
            if docs is not None:
                return _response(url, {"response": {"numFound": len(docs), "docs": docs}})
    return None


def stats() -> dict:
    return dict(_stats)


def reset_stats() -> None:
    _stats["answered"] = 0


__all__ = ["answer", "stats", "reset_stats", "hgnc", "ncbi_gene", "ontology"]
