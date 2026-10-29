"""
EntityResolver — maps biological entity IDs to publication lists.

Supports any database configured in config/entity_databases.yaml.
Currently implemented: FlyBase (tsv_gz), others via NCBI Gene API (api_ncbi).

Usage:
    er = EntityResolver()
    db = er.identify("FBgn0000022")   # → "FlyBase" or None
    pubs = er.lookup("FBgn0000022")   # → [Publication(...), ...]
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "entity_databases.yaml"
_CACHE_DIR   = Path("data/entity_db_cache")


@dataclass
class Publication:
    pmid:    str = ""
    pmcid:   str = ""
    doi:     str = ""
    fbrf:    str = ""
    title:   str = ""
    year:    str = ""
    open_access: bool = False

    @property
    def display_id(self) -> str:
        if self.pmcid:
            return self.pmcid
        if self.pmid:
            return self.pmid
        return self.doi or self.fbrf

    @property
    def pipeline_input_type(self) -> str:
        if self.pmcid:
            return "pmc"
        return "pmid"

    @property
    def pipeline_input_value(self) -> str:
        if self.pmcid:
            return self.pmcid
        return self.pmid

    def to_dict(self) -> dict:
        return {
            "pmid":         self.pmid,
            "pmcid":        self.pmcid,
            "doi":          self.doi,
            "fbrf":         self.fbrf,
            "title":        self.title,
            "year":         self.year,
            "open_access":  self.open_access,
            "display_id":   self.display_id,
            "input_type":   self.pipeline_input_type,
            "input_value":  self.pipeline_input_value,
        }


class EntityResolver:
    """Resolves biological entity IDs to publication lists."""

    def __init__(self):
        self._config   = self._load_config()
        self._patterns = [
            (re.compile(db["id_pattern"]), db)
            for db in self._config
            if db.get("id_pattern")
        ]

    # ── Public API ────────────────────────────────────────────────────────────

    def identify(self, text: str) -> Optional[dict]:
        """Return the matching database config dict, or None if unrecognized."""
        t = text.strip()
        for pattern, db in self._patterns:
            if pattern.match(t):
                return db
        return None

    def lookup(self, entity_id: str) -> list[Publication]:
        """Return publications for the given entity ID. Downloads/caches TSV on first call."""
        db = self.identify(entity_id)
        if db is None:
            return []
        rtype = db.get("resolver_type", "api_ncbi")
        if rtype == "tsv_gz":
            return self._lookup_tsv(entity_id, db)
        if rtype == "tsv_gz_scan":
            return self._lookup_tsv_scan(entity_id, db)
        if rtype == "api_ncbi":
            return self._lookup_ncbi(entity_id, db)
        return []

    # ── TSV resolver (FlyBase and similar) ───────────────────────────────────

    def _lookup_tsv(self, entity_id: str, db: dict) -> list[Publication]:
        primary   = self._get_tsv_index(db)
        has_crossref = bool(db.get("crossref_tsv_url") or db.get("crossref_dir_url"))
        crossref  = self._get_crossref_index(db) if has_crossref else {}
        raw_refs  = primary.get(entity_id.strip())
        if not raw_refs:
            return []
        symbol = raw_refs.get("symbol", "")
        pubs   = []
        for fbrf, pmid in raw_refs.get("refs", []):
            cr  = crossref.get(fbrf, {})
            pub = Publication(
                pmid         = pmid or cr.get("pmid", ""),
                pmcid        = cr.get("pmcid", ""),
                doi          = cr.get("doi", ""),
                fbrf         = fbrf,
                open_access  = bool(cr.get("pmcid")),
            )
            if pub.pmid or pub.pmcid:
                pubs.append(pub)
        # Enrich with PubMed titles for display (best-effort, non-blocking)
        self._enrich_titles(pubs)
        return pubs

    # ── Scanned TSV resolver (very large files, e.g. FlyBase entity_publication) ──

    def _lookup_tsv_scan(self, entity_id: str, db: dict) -> list[Publication]:
        gz_path = self._get_cached_gz_file(db)
        comment = db.get("tsv_comment_char", "#")
        id_col      = db.get("id_column", "entity_id")
        ref_id_col  = db.get("ref_id_column", "FlyBase_publication_id")
        pmid_col    = db.get("pmid_column", "PubMed_id")
        pmid_prefix = db.get("pmid_prefix", "")
        missing     = db.get("missing_value_token", "-")
        target      = entity_id.strip()

        raw_refs: list[tuple[str, str]] = []
        with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
            header = None
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.startswith(comment * 2):
                    continue
                if line.startswith(comment):
                    if header is None:
                        header = line[len(comment):].split("\t")
                    continue
                if header is None:
                    continue
                parts = line.split("\t")
                row   = dict(zip(header, parts))
                if row.get(id_col, "").strip() != target:
                    continue
                ref_id = row.get(ref_id_col, "").strip()
                pmid   = row.get(pmid_col, "").strip()
                if pmid_prefix and pmid.startswith(pmid_prefix):
                    pmid = pmid[len(pmid_prefix):].strip()
                if pmid == missing:
                    pmid = ""
                if ref_id:
                    raw_refs.append((ref_id, pmid))

        has_crossref = bool(db.get("crossref_tsv_url") or db.get("crossref_dir_url"))
        crossref = self._get_crossref_index(db) if has_crossref else {}
        pubs = []
        for ref_id, pmid in raw_refs:
            cr  = crossref.get(ref_id, {})
            pub = Publication(
                pmid        = pmid or cr.get("pmid", ""),
                pmcid       = cr.get("pmcid", ""),
                doi         = cr.get("doi", ""),
                fbrf        = ref_id,
                open_access = bool(cr.get("pmcid")),
            )
            if pub.pmid or pub.pmcid:
                pubs.append(pub)
        self._enrich_titles(pubs)
        return pubs

    def _get_cached_gz_file(self, db: dict) -> Path:
        """Download the primary .gz to a local cache file (kept compressed — the
        source file may be too large to usefully hold as a parsed in-memory/JSON
        index) and return its path. Re-downloads once the TTL expires."""
        cache_path = self._cache_path(db["name"], "primary.tsv.gz")
        if cache_path.exists():
            age_days = (time.time() - cache_path.stat().st_mtime) / 86400
            if age_days <= db.get("cache_ttl_days", 30):
                return cache_path
        url = self._resolve_url(db, "tsv_url", "tsv_dir_url", "tsv_filename_regex")
        logger.info("[EntityResolver] Downloading %s primary file (scan mode) from %s…", db["name"], url)
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        cache_path.write_bytes(resp.content)
        return cache_path

    def _get_tsv_index(self, db: dict) -> dict:
        """Download + parse the primary TSV; return {entity_id: {symbol, refs: [(fbrf, pmid)]}}."""
        cache_path = self._cache_path(db["name"], "primary.json")
        cached     = self._load_cache(cache_path, db.get("cache_ttl_days", 30))
        if cached is not None:
            return cached

        url = self._resolve_url(db, "tsv_url", "tsv_dir_url", "tsv_filename_regex")
        logger.info("[EntityResolver] Downloading %s primary TSV from %s…", db["name"], url)
        raw = self._download_gz(url)
        index: dict = {}
        comment = db.get("tsv_comment_char", "#")
        id_col  = db.get("id_column", "FBgn_ID")
        sym_col = db.get("symbol_column", "Symbol")
        ref_col = db.get("refs_column", "References")
        fmt     = db.get("refs_format", "flybase_pipe")

        for row in self._iter_tsv_rows(raw, comment):
            eid  = row.get(id_col, "").strip()
            sym  = row.get(sym_col, "").strip()
            refs = row.get(ref_col, "").strip()
            if not eid:
                continue
            index[eid] = {
                "symbol": sym,
                "refs":   self._parse_refs(
                    refs, fmt,
                    db.get("missing_value_token", "-"),
                    db.get("pmid_prefix", ""),
                ),
            }

        self._save_cache(cache_path, index)
        logger.info("[EntityResolver] Indexed %d %s entries", len(index), db["name"])
        return index

    def _get_crossref_index(self, db: dict) -> dict:
        """Download + parse the cross-reference TSV; return {fbrf: {pmid, pmcid, doi}}."""
        cache_path = self._cache_path(db["name"], "crossref.json")
        cached     = self._load_cache(cache_path, db.get("cache_ttl_days", 30))
        if cached:                                  # an empty index is a failed parse — rebuild
            return cached

        url = self._resolve_url(db, "crossref_tsv_url", "crossref_dir_url", "crossref_filename_regex")
        logger.info("[EntityResolver] Downloading %s cross-reference TSV from %s…", db["name"], url)
        raw     = self._download_gz(url)
        index   = {}
        comment = db.get("tsv_comment_char", "#")
        id_col  = db.get("crossref_id_col",   "FBrf_ID")
        pm_col  = db.get("crossref_pmid_col",  "PMID")
        pmc_col = db.get("crossref_pmcid_col", "PMCID")
        doi_col = db.get("crossref_doi_col",   "DOI")

        for row in self._iter_tsv_rows(raw, comment):
            fbrf  = row.get(id_col, "").strip()
            if not fbrf:
                continue
            pmid_prefix = db.get("pmid_prefix", "")
            missing     = db.get("missing_value_token", "-")
            pmid  = row.get(pm_col,  "").strip()
            if pmid_prefix and pmid.startswith(pmid_prefix):
                pmid = pmid[len(pmid_prefix):].strip()
            pmcid = row.get(pmc_col, "").strip()
            doi   = row.get(doi_col, "").strip()
            index[fbrf] = {
                "pmid":  pmid  if pmid  not in ("", missing) else "",
                "pmcid": pmcid if pmcid not in ("", missing) else "",
                "doi":   doi   if doi   not in ("", missing) else "",
            }

        self._save_cache(cache_path, index)
        logger.info("[EntityResolver] Indexed %d cross-reference entries", len(index))
        return index

    def _iter_tsv_rows(self, raw: str, comment: str):
        """Yield {column: value} dicts from a tab-delimited file.

        The header is the last tab-separated comment line before the data
        (e.g. "#col1\\tcol2"), else the first data line.
        """
        header = None
        in_data = False
        for line in raw.splitlines():
            if not line.strip():
                continue
            if line.startswith(comment) and not in_data:
                if "\t" in line and not line.startswith(comment * 2):
                    header = line[len(comment):].split("\t")
                continue
            parts = line.split("\t")
            if header is None:
                header = parts
                continue
            in_data = True
            yield dict(zip(header, parts))

    def _parse_refs(self, refs_str: str, fmt: str, missing: str = "-", pmid_prefix: str = "") -> list[tuple[str, str]]:
        """Parse a refs column into [(fbrf, pmid), ...]. Empty PMID → ''."""
        result = []
        if fmt == "flybase_pipe":
            for item in refs_str.split(","):
                item = item.strip()
                if not item or item == missing:
                    continue
                parts = item.split("|")
                fbrf  = parts[0].strip() if len(parts) > 0 else ""
                pmid  = parts[1].strip() if len(parts) > 1 else ""
                if pmid == missing:
                    pmid = ""
                elif pmid_prefix and pmid.startswith(pmid_prefix):
                    pmid = pmid[len(pmid_prefix):].strip()
                if fbrf:
                    result.append((fbrf, pmid))
        return result

    # ── NCBI Gene API resolver (WormBase, MGI, etc.) ─────────────────────────

    def _lookup_ncbi(self, entity_id: str, db: dict) -> list[Publication]:
        """Query NCBI Gene API to get publications for the given entity ID."""
        # Map organism-specific IDs to NCBI Gene IDs via eSearch, then get papers
        # This is a best-effort implementation; extend per-database as needed
        cache_path = self._cache_path(db["name"], f"{entity_id}.json")
        cached     = self._load_cache(cache_path, db.get("cache_ttl_days", 30))
        if cached is not None:
            fields = Publication.__dataclass_fields__
            return [Publication(**{k: v for k, v in p.items() if k in fields}) for p in cached]

        try:
            base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
            # Search for the entity ID in Gene database
            search_url = (f"{base}esearch.fcgi?db=gene&term={entity_id}"
                          f"[Gene+ID]&retmode=json&retmax=1")
            resp = requests.get(search_url, timeout=10)
            resp.raise_for_status()
            ids  = resp.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return []

            ncbi_gene_id = ids[0]
            # Get linked PubMed articles via eLink
            link_url = (f"{base}elink.fcgi?dbfrom=gene&db=pubmed"
                        f"&id={ncbi_gene_id}&retmode=json")
            resp = requests.get(link_url, timeout=10)
            resp.raise_for_status()
            linksets = resp.json().get("linksets", [])
            pmids = []
            for ls in linksets:
                for lsd in ls.get("linksetdbs", []):
                    if lsd.get("linkname") == "gene_pubmed":
                        pmids = lsd.get("links", [])[:20]  # cap at 20
                        break

            pubs = [Publication(pmid=str(p)) for p in pmids]
            self._enrich_titles(pubs)
            self._save_cache(cache_path, [p.to_dict() for p in pubs])
            return pubs
        except Exception as exc:
            logger.warning("[EntityResolver] NCBI lookup failed for %s: %s", entity_id, exc)
            return []

    # ── Title enrichment (PubMed eSummary) ───────────────────────────────────

    def _enrich_titles(self, pubs: list[Publication]) -> None:
        """Add title + year to publications that have a PMID but no title."""
        pmids = [p.pmid for p in pubs if p.pmid and not p.title]
        if not pmids:
            return
        try:
            base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
            url  = f"{base}esummary.fcgi?db=pubmed&id={','.join(pmids)}&retmode=json"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            result = resp.json().get("result", {})
            for pub in pubs:
                if pub.pmid and pub.pmid in result:
                    entry   = result[pub.pmid]
                    pub.title = entry.get("title", "")
                    pub.year  = entry.get("pubdate", "")[:4]
        except Exception as exc:
            logger.warning("[EntityResolver] Title enrichment failed: %s", exc)

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _cache_path(self, db_name: str, filename: str) -> Path:
        p = _CACHE_DIR / db_name.lower()
        p.mkdir(parents=True, exist_ok=True)
        return p / filename

    def _load_cache(self, path: Path, ttl_days: int) -> Optional[dict]:
        if not path.exists():
            return None
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days > ttl_days:
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def _save_cache(self, path: Path, data) -> None:
        try:
            path.write_text(json.dumps(data))
        except Exception as exc:
            logger.warning("[EntityResolver] Cache write failed: %s", exc)

    def _resolve_url(self, db: dict, url_key: str, dir_key: str, regex_key: str) -> str:
        """Return a concrete file URL.

        If `db[url_key]` is set, use it as-is (a pinned/versioned URL).
        Otherwise discover the current file by fetching the directory index at
        `db[dir_key]` and matching `db[regex_key]` against the listed filenames —
        avoids hardcoding a release version that will go stale.
        """
        if db.get(url_key):
            return db[url_key]
        dir_url = db.get(dir_key)
        pattern = db.get(regex_key)
        if not dir_url or not pattern:
            raise ValueError(
                f"{db['name']}: need either '{url_key}' or both '{dir_key}' and '{regex_key}' in config"
            )
        resp = requests.get(dir_url, timeout=30)
        resp.raise_for_status()
        hrefs     = re.findall(r'href=[\'"]([^\'"]+)[\'"]', resp.text)
        basenames = [h.rsplit("/", 1)[-1] for h in hrefs]
        matches   = [n for n in basenames if re.search(pattern, n)]
        if not matches:
            raise ValueError(f"{db['name']}: no file matching '{pattern}' found under {dir_url}")
        return dir_url.rstrip("/") + "/" + matches[0]

    def _download_gz(self, url: str) -> str:
        """Download a .gz URL and return the decompressed text content."""
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        raw = io.BytesIO(resp.content)
        with gzip.open(raw, "rt", encoding="utf-8", errors="replace") as f:
            return f.read()

    # ── Config loader ─────────────────────────────────────────────────────────

    @staticmethod
    def _load_config() -> list[dict]:
        try:
            with open(_CONFIG_PATH) as f:
                data = yaml.safe_load(f)
            return data.get("entity_databases", [])
        except Exception as exc:
            logger.warning("[EntityResolver] Could not load entity_databases.yaml: %s", exc)
            return []
