"""Local Ensembl gene index: Ensembl's own gene IDs and symbols, read from its released GTF files.

Lookups need no REST call, so they work when rest.ensembl.org is slow or down. Built by build().
"""
import gzip
import io
import logging
import os
import re
import sqlite3
import threading
import time

import requests

logger = logging.getLogger(__name__)

_DB = os.getenv("ENSEMBL_LOCAL_DB", "data/ensembl_local/genes.db")
_FTP = "https://ftp.ensembl.org/pub"
SPECIES = [s.strip() for s in os.getenv(
    "ENSEMBL_LOCAL_SPECIES",
    "homo_sapiens,mus_musculus,rattus_norvegicus,danio_rerio,drosophila_melanogaster,"
    "caenorhabditis_elegans,saccharomyces_cerevisiae").split(",") if s.strip()]
_ATTR = re.compile(r'(\w+) "([^"]*)"')
_lock = threading.Lock()
_conn = None


def _db():
    global _conn
    if _conn is None and os.path.exists(_DB):
        _conn = sqlite3.connect(_DB, check_same_thread=False)
    return _conn


def available() -> bool:
    return _db() is not None


def symbol(name: str, species: str) -> list:
    """Ensembl gene IDs whose symbol is this name in this species (Ensembl slug, e.g. 'homo_sapiens')."""
    conn = _db()
    if conn is None or not name:
        return []
    with _lock:
        return [(r[0], r[1]) for r in conn.execute(
            "SELECT gene_id, name FROM genes WHERE name_lower=? AND species=?", (name.strip().lower(), species))]


def symbols_starting(stem: str, species: str) -> list:
    """Gene symbols in this species that start with stem (lowercase)."""
    conn = _db()
    if conn is None or not stem:
        return []
    with _lock:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT name FROM genes WHERE name_lower >= ? AND name_lower < ? AND species=?",
            (stem, stem + "￿", species))]


def species_list() -> list:
    """Species slugs in the index ('rattus_norvegicus', ...)."""
    conn = _db()
    if conn is None:
        return []
    with _lock:
        return [r[0] for r in conn.execute("SELECT DISTINCT species FROM genes")]


def gene(gene_id: str):
    """(symbol, species slug) of an Ensembl gene ID, or None."""
    conn = _db()
    if conn is None:
        return None
    with _lock:
        row = conn.execute("SELECT name, species FROM genes WHERE gene_id=?", (gene_id,)).fetchone()
    return tuple(row) if row else None


def has_species(species: str) -> bool:
    conn = _db()
    if conn is None:
        return False
    with _lock:
        return conn.execute("SELECT 1 FROM genes WHERE species=? LIMIT 1", (species,)).fetchone() is not None


def _latest_release() -> str:
    page = requests.get(f"{_FTP}/", timeout=60).text
    return max(re.findall(r'href="release-(\d+)/"', page), key=int)


def _gtf_url(release: str, species: str) -> str:
    """The species' main gene file: the plain .gtf.gz, not the chromosome-only or predicted-gene variants
    (species from Ensembl's sister projects carry their own release number, e.g. '.63')."""
    page = requests.get(f"{_FTP}/release-{release}/gtf/{species}/", timeout=60).text
    name = min((f for f in re.findall(r'href="([^"]+\.gtf\.gz)"', page)
                if ".chr" not in f and ".abinitio" not in f), key=len)
    return f"{_FTP}/release-{release}/gtf/{species}/{name}"


def build(species: list = None, log=print) -> dict:
    """Download Ensembl's GTF for each species and keep its genes (ID, symbol, biotype). Returns counts."""
    species = species or SPECIES
    release = _latest_release()
    os.makedirs(os.path.dirname(_DB) or ".", exist_ok=True)
    tmp = _DB + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE genes (gene_id TEXT, name TEXT, name_lower TEXT, species TEXT, biotype TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    counts = {}
    for sp in species:
        try:
            url = _gtf_url(release, sp)
        except (ValueError, requests.RequestException) as e:
            log(f"  {sp}: skipped — no gene file found ({e})")
            continue
        t = time.time()
        rows = []
        gz_path = os.path.join(os.path.dirname(_DB), url.rsplit("/", 1)[1])
        with requests.get(url, stream=True, timeout=(30, 600)) as r:   # Ensembl GTFs are multi-part gzip:
            r.raise_for_status()                                       # download whole, then read
            with open(gz_path, "wb") as f:
                for block in r.iter_content(1 << 20):
                    f.write(block)
        with gzip.open(gz_path, "rt", encoding="utf-8") as gz:
            for line in gz:
                parts = line.split("\t")
                if len(parts) < 9 or parts[2] != "gene":
                    continue
                a = dict(_ATTR.findall(parts[8]))
                gid = a.get("gene_id")
                if gid:
                    nm = a.get("gene_name") or gid
                    rows.append((gid, nm, nm.lower(), sp, a.get("gene_biotype", "")))
        os.remove(gz_path)
        conn.executemany("INSERT INTO genes VALUES (?,?,?,?,?)", rows)
        counts[sp] = len(rows)
        log(f"  {sp}: {len(rows):,} genes ({time.time() - t:.0f}s)")
    conn.execute("CREATE INDEX idx_symbol ON genes (name_lower, species)")
    conn.execute("CREATE INDEX idx_id ON genes (gene_id)")
    conn.execute("INSERT INTO meta VALUES ('release', ?), ('built_at', ?)", (release, time.strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    os.replace(tmp, _DB)                         # swap in only a complete index
    global _conn
    _conn = None
    return {"release": release, "genes": counts}
