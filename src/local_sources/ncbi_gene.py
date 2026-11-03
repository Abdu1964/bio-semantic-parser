"""Local copy of NCBI Gene (gene_info): symbols, synonyms and Ensembl cross-references for every organism.

Answers what the normalizer asks NCBI Gene online (eSearch by symbol, eSummary, MyGene's Ensembl link)
in one local lookup. Groups: LOCAL_NCBI_GENE_GROUPS (NCBI's GENE_INFO folders), all by default.
"""
import gzip
import os
import time

from src.local_sources.common import connect, download, new_db, norm, path, query, swap_in

_BASE = "https://ftp.ncbi.nlm.nih.gov/gene/DATA/GENE_INFO/{0}/All_{0}.gene_info.gz"
GROUPS = [g.strip() for g in os.getenv(
    "LOCAL_NCBI_GENE_GROUPS",
    "Mammalia,Non-mammalian_vertebrates,Invertebrates,Plants,Fungi,Protozoa,Archaea_Bacteria,Viruses").split(",")
    if g.strip()]
_DB = path("ncbi_gene", "genes.db")
_SCHEMA = """
CREATE TABLE genes (gene_id TEXT PRIMARY KEY, tax_id TEXT, symbol TEXT, synonyms TEXT, description TEXT,
                    ensembl TEXT, designations TEXT);
CREATE TABLE names (tax_id TEXT, key TEXT, gene_id TEXT, kind INTEGER);
"""


def available() -> bool:
    return connect(_DB) is not None


def has_taxon(tax_id: str) -> bool:
    return bool(tax_id) and bool(query(_DB, "SELECT 1 FROM genes WHERE tax_id=? LIMIT 1", (str(tax_id),)))


def find(text: str, tax_id: str):
    """The one gene in this organism whose symbol, else whose synonym, is the text:
    {gene_id, symbol, synonyms, ensembl}; None if no single gene matches."""
    for kind in (0, 1):
        rows = query(_DB, "SELECT DISTINCT g.gene_id, g.symbol, g.synonyms, g.designations, g.ensembl "
                          "FROM names n JOIN genes g ON g.gene_id = n.gene_id "
                          "WHERE n.tax_id = ? AND n.key = ? AND n.kind = ?", (str(tax_id), norm(text), kind))
        if len(rows) == 1:
            gid, symbol, syns, designations, ensembl = rows[0]
            names = [s for s in (syns or "").split("|") + (designations or "").split("|") if s and s != "-"]
            return {"gene_id": gid, "symbol": symbol, "synonyms": "; ".join(dict.fromkeys(names)),
                    "ensembl": ensembl or ""}
        if len(rows) > 1:
            return None                          # the name is shared by several genes: ambiguous
    return None


def build(log=print) -> int:
    t0 = time.time()
    conn = new_db(_DB, _SCHEMA)
    n = 0
    for group in GROUPS:
        file = download(_BASE.format(group), path("ncbi_gene", f"{group}.gene_info.gz"))
        with gzip.open(file, "rt", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                p = line.rstrip("\n").split("\t")
                if len(p) < 14:
                    continue
                tax, gid, symbol, syns, xrefs, desc, designations = p[0], p[1], p[2], p[4], p[5], p[8], p[13]
                ensembl = next((x.split(":", 1)[1] for x in xrefs.split("|") if x.startswith("Ensembl:")), "")
                conn.execute("INSERT OR REPLACE INTO genes VALUES (?,?,?,?,?,?,?)",
                             (gid, tax, symbol, syns, desc, ensembl, designations))
                keys = {(norm(symbol), 0)} | {(norm(s), 1) for s in syns.split("|") if s and s != "-"}
                conn.executemany("INSERT INTO names VALUES (?,?,?,?)", [(tax, k, gid, kind) for k, kind in keys])
                n += 1
        os.remove(file)
        log(f"  ncbi_gene {group}: {n:,} genes so far")
    conn.execute("CREATE INDEX idx_names ON names (tax_id, key, kind)")
    swap_in(conn, _DB, {"groups": GROUPS, "genes": n, "built_at": time.strftime("%Y-%m-%d")})
    log(f"  ncbi_gene: {n:,} genes ({time.time() - t0:.0f}s)")
    return n
