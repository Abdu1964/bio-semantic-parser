"""Local copy of HGNC (human gene symbols, previous and alias symbols), answering its REST fetch endpoint."""
import csv
import os
import time

from src.local_sources.common import connect, download, new_db, norm, path, query, swap_in

_URL = "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
_FIELDS = ("symbol", "prev_symbol", "alias_symbol")
_DB = path("hgnc", "genes.db")
_SCHEMA = """
CREATE TABLE genes (id INTEGER PRIMARY KEY, hgnc_id TEXT, symbol TEXT, name TEXT, ensembl_gene_id TEXT,
                    entrez_id TEXT, prev_symbol TEXT, alias_symbol TEXT);
CREATE TABLE names (field TEXT, key TEXT, gene INTEGER);
"""


def available() -> bool:
    return connect(_DB) is not None


def fetch(field: str, value: str):
    """HGNC docs for rest.genenames.org/fetch/<field>/<value>; None if not built or the field isn't copied."""
    if field not in _FIELDS or connect(_DB) is None:
        return None
    rows = query(_DB, "SELECT g.hgnc_id, g.symbol, g.name, g.ensembl_gene_id, g.entrez_id, g.prev_symbol, "
                      "g.alias_symbol FROM names n JOIN genes g ON g.id = n.gene WHERE n.field = ? AND n.key = ?",
                 (field, norm(value)))
    keys = ("hgnc_id", "symbol", "name", "ensembl_gene_id", "entrez_id", "prev_symbol", "alias_symbol")
    docs = []
    for row in rows:
        doc = {k: v for k, v in zip(keys, row) if v}
        for k in ("prev_symbol", "alias_symbol"):
            if k in doc:
                doc[k] = doc[k].split("|")
        docs.append(doc)
    return docs


def build(log=print) -> int:
    t0 = time.time()
    file = download(_URL, path("hgnc", "hgnc_complete_set.txt"))
    conn = new_db(_DB, _SCHEMA)
    n = 0
    with open(file, encoding="utf-8") as f:
        for n, row in enumerate(csv.DictReader(f, delimiter="\t"), 1):
            if row.get("status") and row["status"] != "Approved":
                continue
            cur = conn.execute("INSERT INTO genes (hgnc_id, symbol, name, ensembl_gene_id, entrez_id, prev_symbol, "
                               "alias_symbol) VALUES (?,?,?,?,?,?,?)",
                               (row["hgnc_id"], row["symbol"], row.get("name", ""), row.get("ensembl_gene_id", ""),
                                row.get("entrez_id", ""), row.get("prev_symbol", ""), row.get("alias_symbol", "")))
            for field in _FIELDS:
                for value in filter(None, (row.get(field) or "").strip('"').split("|")):
                    conn.execute("INSERT INTO names VALUES (?,?,?)", (field, norm(value), cur.lastrowid))
    conn.execute("CREATE INDEX idx_names ON names (field, key)")
    os.remove(file)
    swap_in(conn, _DB, {"source": _URL, "genes": n, "built_at": time.strftime("%Y-%m-%d")})
    log(f"  hgnc: {n:,} genes ({time.time() - t0:.0f}s)")
    return n
