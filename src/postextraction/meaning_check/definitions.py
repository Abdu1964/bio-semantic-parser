"""Labels, definitions and synonyms of ontology terms, and candidate terms for a name — from OLS4.

Requests go through the normalizer's HTTP layer, so they are paced, retried and stored like every lookup.
"""
from src.postextraction import entity_normalization as en

_FIELDS = "obo_id,label,description,synonym,ontology_prefix"
_DEFINITION_CHARS = 220


def _first(value) -> str:
    return (value[0] if value else "") if isinstance(value, list) else (value or "")


def _term(doc: dict) -> dict:
    syns = doc.get("synonym") or []
    return {"id": _first(doc.get("obo_id")), "label": _first(doc.get("label")),
            "definition": " ".join(_first(doc.get("description")).split())[:_DEFINITION_CHARS],
            "synonyms": [syns] if isinstance(syns, str) else syns[:6], "prefix": _first(doc.get("ontology_prefix"))}


def describe(cid: str):
    """The term behind an ontology ID: label, definition, synonyms ({} when OLS does not know the ID;
    None when OLS could not be asked)."""
    try:
        docs = en._http_get(f"{en._OLS_BASE}/search", params={"q": cid, "queryFields": "obo_id", "exact": "true",
                                                             "rows": 1, "fieldList": _FIELDS},
                            headers={"Accept": "application/json"}, timeout=en._TIMEOUT).json()["response"]["docs"]
        return _term(docs[0]) if docs else {}
    except Exception as e:
        en.logger.warning("Meaning check: no definition for %s: %s", cid, e)
        return None


def candidates(name: str, ontologies: list, rows: int = 6) -> list:
    """Terms OLS finds for a name within these ontologies (any ontology when none), with definitions."""
    params = {"q": name, "rows": rows, "exact": "false", "fieldList": _FIELDS}
    if ontologies:
        params["ontology"] = ",".join(ontologies)
    try:
        docs = en._http_get(f"{en._OLS_BASE}/search", params=params, headers={"Accept": "application/json"},
                            timeout=en._TIMEOUT).json()["response"]["docs"]
    except Exception as e:
        en.logger.warning("Meaning check: no candidates for %r: %s", name, e)
        return []
    out = []
    for d in docs:
        t = _term(d)
        if t["id"] and ":" in t["id"] and t["label"]:
            t["id"] = t["id"].replace("_", ":", 1) if ":" not in t["id"] else t["id"]
            out.append(t)
    return out
