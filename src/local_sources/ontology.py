"""Local copies of the ontologies the normalizer searches, answering OLS4's exact search the way OLS does.

Built from each ontology's own release: OBO files from the OBO Foundry, MeSH from NLM's XML,
NCBI Taxonomy from NCBI's taxdump. One folder per ontology: data/local_sources/ontology/<name>/terms.db.
"""
import gzip
import json
import os
import re
import tarfile
import time
import xml.etree.ElementTree as ET

import requests

from src.local_sources.common import connect, download, meta, new_db, norm, path, query, swap_in

_OLS = "https://www.ebi.ac.uk/ols4/api"
_OBO_URL = "http://purl.obolibrary.org/obo/{}.obo"
_OWN_OBO_URL = {"efo": "http://www.ebi.ac.uk/efo/efo.obo"}          # ontologies released outside the OBO PURLs
_IRI_BASE = {"EFO": "http://www.ebi.ac.uk/efo/", "Orphanet": "http://www.orpha.net/ORDO/"}
_MESH = "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/"
_TAXDUMP = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"
_SCHEMA = """
CREATE TABLE terms (id INTEGER PRIMARY KEY, obo_id TEXT, label TEXT, synonyms TEXT, iri TEXT, extra TEXT);
CREATE TABLE names (key TEXT, term INTEGER, kind INTEGER);
"""


def _db(name: str) -> str:
    return path("ontology", name.lower(), "terms.db")


def default_ontologies() -> list:
    """The ontologies the normalizer searches by entity type (database-only names like ensembl are skipped)."""
    from src.postextraction.entity_normalization import _TYPE_TO_ONTOLOGIES
    names = {o for onts in _TYPE_TO_ONTOLOGIES.values() for o in onts}
    return sorted(names - {"ensembl", "hgnc"})


def available(name: str) -> bool:
    return connect(_db(name)) is not None


def search_exact(name: str, text: str, rows: int = 10):
    """OLS4-style docs whose label or synonym is exactly the text (labels first); None if not built."""
    db = _db(name)
    if connect(db) is None:
        return None
    prefix = meta(db, "prefix", name.upper())
    hits = query(db, "SELECT DISTINCT t.obo_id, t.label, t.synonyms, t.iri, MIN(n.kind) k FROM names n "
                     "JOIN terms t ON t.id = n.term WHERE n.key = ? GROUP BY t.id ORDER BY k LIMIT ?",
                 (norm(text), rows))
    return [{"obo_id": h[0], "label": h[1], "ontology_prefix": prefix, "synonym": json.loads(h[2]), "iri": h[3]}
            for h in hits]


def taxon_candidates(text: str, rows: int = 5):
    """[(taxid, scientific name, common name, rank)] for an exact taxonomy name; None if not built."""
    db = _db("ncbitaxon")
    if connect(db) is None:
        return None
    hits = query(db, "SELECT DISTINCT t.obo_id, t.label, t.extra FROM names n JOIN terms t ON t.id = n.term "
                     "WHERE n.key = ? LIMIT ?", (norm(text), rows))
    out = []
    for oid, label, extra in hits:
        x = json.loads(extra or "{}")
        out.append((oid.split(":", 1)[1], label, x.get("common", ""), x.get("rank", "")))
    return out


# ── Building ────────────────────────────────────────────────────────────────

def _ols_prefix(name: str) -> str:
    """The prefix OLS reports for this ontology (MONDO, mesh, NCBITaxon…)."""
    try:
        cfg = requests.get(f"{_OLS}/ontologies/{name}", timeout=30).json().get("config", {})
        return cfg.get("preferredPrefix") or name.upper()
    except (requests.RequestException, ValueError):
        return name.upper()


def _iri(obo_id: str) -> str:
    prefix, local = obo_id.split(":", 1)
    return _IRI_BASE.get(prefix, "http://purl.obolibrary.org/obo/") + f"{prefix}_{local}"


def _obo_terms(file: str):
    """(id, label, synonyms, iri, extra) for each live [Term] of an OBO file."""
    term = None
    with open(file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("["):
                if term and term.get("id") and term.get("name") and not term.get("obsolete"):
                    yield term["id"], term["name"], term["syn"], _iri(term["id"]), None
                term = {"syn": []} if line == "[Term]" else None
            elif term is not None and ": " in line:
                tag, value = line.split(": ", 1)
                if tag == "id" and ":" in value:
                    term["id"] = value.strip()
                elif tag == "name":
                    term["name"] = value.strip()
                elif tag == "synonym":
                    m = re.match(r'"((?:[^"\\]|\\.)*)"', value)
                    if m:
                        term["syn"].append(m.group(1).replace('\\"', '"'))
                elif tag == "is_obsolete" and value.strip() == "true":
                    term["obsolete"] = True
    if term and term.get("id") and term.get("name") and not term.get("obsolete"):
        yield term["id"], term["name"], term["syn"], _iri(term["id"]), None


def _mesh_terms(folder: str):
    """MeSH descriptors and supplementary concepts (chemicals, rare diseases) with all their entry terms."""
    listing = requests.get(_MESH, timeout=60).text
    year = max(re.findall(r'href="desc(\d{4})\.gz"', listing))
    for kind, file, ui, name in (("desc", f"desc{year}.gz", "DescriptorUI", "DescriptorName"),
                                 ("supp", f"supp{year}.gz", "SupplementalRecordUI", "SupplementalRecordName")):
        local = download(_MESH + file, os.path.join(folder, file))
        with gzip.open(local) as f:
            for _, el in ET.iterparse(f):
                if el.tag not in ("DescriptorRecord", "SupplementalRecord"):
                    continue
                uid, label = el.findtext(ui), el.findtext(f"{name}/String")
                if uid and label:
                    terms = {s.text for term in el.iter("Term") for s in term.iter("String") if s.text}
                    yield f"mesh:{uid}", label, sorted(terms - {label}), f"http://id.nlm.nih.gov/mesh/{uid}", None
                el.clear()
        os.remove(local)


def _taxon_terms(folder: str):
    """NCBI Taxonomy: scientific name as label; every other name (common, synonym, …) as a synonym."""
    local = download(_TAXDUMP, os.path.join(folder, "taxdump.tar.gz"))
    with tarfile.open(local) as tar:
        ranks = {}
        for line in tar.extractfile("nodes.dmp"):
            parts = line.decode("utf-8", "ignore").split("\t|\t")
            ranks[parts[0]] = parts[2]
        names: dict = {}
        for line in tar.extractfile("names.dmp"):
            tid, name, _, cls = line.decode("utf-8", "ignore").rstrip("\t|\n").split("\t|\t")
            if cls in ("authority", "type material", "in-part", "includes"):
                continue
            names.setdefault(tid, {"sci": "", "common": "", "syn": []})
            entry = names[tid]
            if cls == "scientific name":
                entry["sci"] = name
            else:
                entry["syn"].append(name)
                if cls == "genbank common name" or (cls == "common name" and not entry["common"]):
                    entry["common"] = name
    os.remove(local)
    for tid, e in names.items():
        if e["sci"]:
            yield (f"NCBITaxon:{tid}", e["sci"], e["syn"], f"http://purl.obolibrary.org/obo/NCBITaxon_{tid}",
                   {"rank": ranks.get(tid, ""), "common": e["common"]})


def build(name: str, log=print) -> int:
    """Download and index one ontology; returns its term count (0 when it has no downloadable release)."""
    name, db, t0 = name.lower(), _db(name), time.time()
    folder = os.path.dirname(db)
    if name == "mesh":
        terms, source = _mesh_terms(folder), _MESH
    elif name == "ncbitaxon":
        terms, source = _taxon_terms(folder), _TAXDUMP
    else:
        source = _OWN_OBO_URL.get(name, _OBO_URL.format(name))
        try:
            obo = download(source, os.path.join(folder, f"{name}.obo"))
        except requests.RequestException as e:
            log(f"  {name}: no OBO release ({e}) — stays online")
            return 0
        terms = _obo_terms(obo)
    conn = new_db(db, _SCHEMA)
    n = 0
    for n, (oid, label, syns, iri, extra) in enumerate(terms, 1):
        cur = conn.execute("INSERT INTO terms (obo_id, label, synonyms, iri, extra) VALUES (?,?,?,?,?)",
                           (oid, label, json.dumps(syns), iri, json.dumps(extra) if extra else None))
        keys = {(norm(label), 0)} | {(norm(s), 1) for s in syns if norm(s) != norm(label)}
        conn.executemany("INSERT INTO names VALUES (?,?,?)", [(k, cur.lastrowid, kind) for k, kind in keys])
    conn.execute("CREATE INDEX idx_names ON names (key)")
    if os.path.exists(os.path.join(folder, f"{name}.obo")):
        os.remove(os.path.join(folder, f"{name}.obo"))
    swap_in(conn, db, {"prefix": _ols_prefix(name), "source": source, "terms": n,
                       "built_at": time.strftime("%Y-%m-%d")})
    log(f"  {name}: {n:,} terms ({time.time() - t0:.0f}s)")
    return n
