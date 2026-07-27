"""Layer 7 step 1 — maps entity text to canonical biomedical identifiers via OLS4, NCBI, UniProt, RxNorm, and Wikidata."""
import logging
import os
import re
import requests
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

_TIMEOUT  = 8
_NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_OLS_BASE  = "https://www.ebi.ac.uk/ols4/api"

# OLS4 in-process cache — avoids redundant API calls for repeated terms.
_ols4_cache: dict = {}

# Short abbreviations (≤2 words) that fail OLS4 lookup are expanded via LLM using the evidence sentence, then retried.
_CONTEXT_EXPAND_MAX_WORDS = 2
_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
_LLM_API_KEY  = os.getenv("LLM_API_KEY", "ollama")
_LLM_MODEL    = os.getenv("LLM_MODEL", "gemma2:27b")

_context_expand_cache: dict = {}
_llm_client = None


def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        from openai import OpenAI
        _llm_client = OpenAI(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL, timeout=15.0)
    return _llm_client


def _extract_context_sentence(chunk_text: str, entity_text: str, window: int = 200) -> str:
    """Return the sentence containing entity_text, or a bounded window when sentence boundaries aren't found."""
    if not chunk_text or not entity_text:
        return ""
    idx = chunk_text.lower().find(entity_text.lower())
    if idx == -1:
        return ""
    start = max(0, idx - window)
    end   = min(len(chunk_text), idx + len(entity_text) + window)
    snippet = chunk_text[start:end]
    # Trim to sentence boundaries within the snippet when they exist.
    sentences = re.split(r"(?<=[.!?])\s+", snippet)
    for s in sentences:
        if entity_text.lower() in s.lower():
            return s.strip()
    return snippet.strip()


def _context_expand(text: str, entity_type: str, context: str) -> str:
    """Expand a short abbreviation to its standard form using context. Returns text unchanged on failure."""
    if not context:
        return text
    cache_key = (text.strip().lower(), entity_type)
    if cache_key in _context_expand_cache:
        return _context_expand_cache[cache_key]

    expanded = text
    try:
        resp = _get_llm_client().chat.completions.create(
            model=_LLM_MODEL,
            temperature=0.0,
            max_tokens=16,
            messages=[{"role": "user", "content": (
                "A biomedical knowledge-graph pipeline extracted the term "
                f"{text!r} (tagged as {entity_type}) from the sentence below. "
                "If it is an abbreviation, respond with its precise standard "
                "scientific/clinical name as used in this sentence. If it is "
                "already a standard, unabbreviated term, respond with it "
                "unchanged. Respond with ONLY the term, no explanation.\n\n"
                f"Sentence: {context}\nTerm: {text}"
            )}],
        )
        candidate = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        if candidate and len(candidate.split()) <= 6:
            expanded = candidate
    except Exception:
        pass

    _context_expand_cache[cache_key] = expanded
    return expanded

# Reject multi-word OLS4 matches with zero token overlap against the query — confidently wrong, not real synonyms.
_STOPWORDS = {"a", "an", "the", "of", "in", "and", "or", "to", "for", "is", "are", "with", "at"}


def _tokens(text: str) -> set:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    # Keep single-digit tokens so "PD-1", "IL-6" etc. aren't collapsed to one token.
    toks = {t for t in text.split() if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())}
    stemmed = set()
    for t in toks:
        if t.endswith("es") and len(t) > 4:
            stemmed.add(t[:-2])
        elif t.endswith("s") and len(t) > 3:
            stemmed.add(t[:-1])
        else:
            stemmed.add(t)
    return stemmed


def _is_confidently_wrong(query_text: str, matched_label: str) -> bool:
    """True if a multi-word OLS4 match shares zero tokens with the query — confidently wrong."""
    if not matched_label:
        return False
    q_tokens = _tokens(query_text)
    if len(q_tokens) < 2:
        return False
    m_tokens = _tokens(matched_label)
    if not m_tokens:
        return False
    return len(q_tokens & m_tokens) == 0


# ── Biolink → OLS4 ontology filter (gene/variant databases handled separately) ─
_TYPE_TO_ONTOLOGIES: dict = {
    "GENE":                        ["ensembl", "hgnc"],
    "PROTEIN":                     ["pr", "hgnc"],
    "TRANSCRIPT":                  ["so"],
    "EXON":                        ["so"],
    "NON_CODING_RNA":              ["so"],
    "GENOMIC_VARIANT":             ["so"],
    "SEQUENCE_VARIANT":            ["so"],
    "STRUCTURAL_VARIANT":          ["so"],
    "HAPLOTYPE":                   ["so"],
    "GENOTYPE":                    [],
    "REGULATORY_REGION":           ["so", "obi"],
    "ENHANCER":                    ["so"],
    "SUPER_ENHANCER":              ["so"],
    "PROMOTER":                    ["so"],
    "TRANSCRIPTION_FACTOR_BINDING_SITE": ["so"],
    "EPIGENOMIC_FEATURE":          ["so", "obi"],
    "MOTIF":                       ["so"],
    "TAD":                         ["so"],
    "SMALL_MOLECULE":              ["chebi", "mesh"],
    "DISEASE":                     ["mondo", "mesh", "doid"],
    "CANCER":                      ["mondo", "ncit", "mesh"],
    "PHENOTYPE":                   ["hp", "mp", "mesh"],
    "SYMPTOM":                     ["hp", "mesh"],
    "PATHWAY":                     ["pw", "go"],
    "REACTION":                    ["go"],
    "BIOLOGICAL_PROCESS":          ["go"],
    "MOLECULAR_FUNCTION":          ["go"],
    "CELLULAR_COMPONENT":          ["go"],
    "ANATOMY":                     ["uberon", "mesh"],
    "TISSUE":                      ["uberon", "bto", "mesh"],
    "CELL_TYPE":                   ["cl", "mesh"],
    "CELL_LINE":                   ["clo", "mesh"],
    "DEVELOPMENTAL_STAGE":         ["uberon"],
    "EXPERIMENTAL_FACTOR":         ["efo", "obi"],
    "THREE_D_GENOME_STRUCTURE":    ["so"],
    "MOLECULAR_INTERACTION":       ["mi"],
    "MACROMOLECULAR_COMPLEX":      ["go"],
    "ORGANISM":                    ["ncbitaxon"],

    "CLINICAL_ENTITY":             ["ncit", "mesh"],
    "CLINICAL_INTERVENTION":       ["ncit", "mesh"],
    "CLINICAL_FINDING":            ["hp", "mesh", "ncit"],
    "PROCEDURE":                   ["ncit", "mesh"],
    "DEVICE":                      ["ncit", "mesh"],
    "DIAGNOSTIC_AID":              ["ncit", "mesh"],
    "TREATMENT":                   ["ncit", "mesh"],
    "DRUG":                        ["chebi", "ncit", "mesh"],

    # ── Exposure & environment ────────────────────────────────────────────────
    "EXPOSURE_EVENT":              ["envo", "ncit"],
    "CHEMICAL_EXPOSURE":           ["chebi", "ncit"],
    "BEHAVIORAL_EXPOSURE":         ["ncit", "mesh"],
    "ENVIRONMENTAL_EXPOSURE":      ["envo", "ncit"],
    "ENVIRONMENTAL_FEATURE":       ["envo"],
    "FOOD":                        ["foodon", "mesh"],

    # ── Behavior & activity ───────────────────────────────────────────────────
    "ACTIVITY":                    ["ncit", "obi"],
    "BEHAVIOR":                    ["ncit", "mesh"],
    "BEHAVIORAL_FEATURE":          ["ncit", "mesh"],

    # ── Physiological & pathological processes ────────────────────────────────
    "PHYSIOLOGICAL_PROCESS":       ["go", "mesh"],
    "PATHOLOGICAL_PROCESS":        ["mesh", "ncit"],

    # ── Population & study ────────────────────────────────────────────────────
    "POPULATION":                  ["ncit", "efo"],
    "COHORT":                      ["ncit", "efo"],
    "STUDY":                       ["efo", "obi"],
    "CLINICAL_TRIAL":              ["ncit", "efo"],

    # ── Organism ──────────────────────────────────────────────────────────────
    "LIFE_STAGE":                  ["hsapdv", "uberon"],
    "INDIVIDUAL_ORGANISM":         ["ncbitaxon"],

    # ── Clinical attributes & outcomes (biolink:Attribute subclasses) ────────
    "CLINICAL_MEASUREMENT":        ["ncit", "mesh"],
    "CLINICAL_ATTRIBUTE":          ["ncit"],
    "ONSET":                       ["hp"],
    "EPIDEMIOLOGICAL_OUTCOME":     ["ncit"],
    "MORTALITY_OUTCOME":           ["ncit"],
    "BEHAVIORAL_OUTCOME":          ["ncit"],
    "DISEASE_OUTCOME":             ["mondo", "mesh", "doid"],

    # ── Clinical patient ──────────────────────────────────────────────────────
    "CASE":                        ["ncit"],

    # ── Specific anatomical / genomic ─────────────────────────────────────────
    "GROSS_ANATOMICAL_STRUCTURE":  ["uberon"],
    "GENOME":                      ["so"],
    "GENETIC_INHERITANCE":         ["hp"],
    "VIRUS":                       ["ncbitaxon"],
    "MICRO_RNA":                   ["so"],
    "SI_RNA":                      ["so"],

    # ── Population & study subtypes ───────────────────────────────────────────
    "STUDY_POPULATION":            ["ncit", "efo"],
    "ORGANISM_TAXON":              ["ncbitaxon"],

    # ── Events & phenomena ────────────────────────────────────────────────────
    "EVENT":                       ["ncit"],
    "PHENOMENON":                  ["ncit"],
    "ENVIRONMENTAL_PROCESS":       ["envo"],

    # OTHER left unscoped — always routes to human review regardless.
    "OTHER":                       [],
}

# Common organism names → NCBITaxon IDs (local fast lookup, no API needed)
_TAXON_LOCAL: dict = {
    "human": "9606",     "humans": "9606",    "homo sapiens": "9606",
    "mouse": "10090",    "mice": "10090",     "mus musculus": "10090",
    "rat": "10116",      "rats": "10116",     "rattus norvegicus": "10116",
    "fly": "7227",       "drosophila": "7227","drosophila melanogaster": "7227",
    "worm": "6239",      "c. elegans": "6239","caenorhabditis elegans": "6239",
    "yeast": "4932",     "saccharomyces cerevisiae": "4932",
    "zebrafish": "7955", "danio rerio": "7955",
    "dog": "9615",       "canis lupus familiaris": "9615",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")


def _ensembl_species_slug(species: str) -> str:
    """Convert a species name to Ensembl's URL slug format (e.g. 'Mus musculus' → 'mus_musculus')."""
    species = (species or "").strip()
    if not species:
        return "human"
    lower = species.lower()
    if lower in _TAXON_LOCAL:
        _COMMON_TO_BINOMIAL = {
            "human": "homo sapiens", "humans": "homo sapiens",
            "mouse": "mus musculus", "mice": "mus musculus",
            "rat": "rattus norvegicus", "rats": "rattus norvegicus",
            "fly": "drosophila melanogaster", "drosophila": "drosophila melanogaster",
            "worm": "caenorhabditis elegans", "c. elegans": "caenorhabditis elegans",
            "yeast": "saccharomyces cerevisiae",
            "zebrafish": "danio rerio",
            "dog": "canis lupus familiaris",
        }
        lower = _COMMON_TO_BINOMIAL.get(lower, lower)
    return re.sub(r"\s+", "_", lower)


# ── Generic name pre-cleaning ────────────────────────────────────────────────

def _clean_name(text: str) -> str:
    """Strip PDF extraction artifacts (ligatures, soft hyphens, citations) before any lookup."""
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff") \
               .replace("ﬃ", "ffi").replace("ﬄ", "ffl").replace("ﬅ", "st")
    text = text.replace("­", "").replace("​", "").replace("﻿", "")
    # "pro- tein" → "protein"
    text = re.sub(r"-\s+", "", text)
    # "cholesterol biosynthesis.35" → "cholesterol biosynthesis"
    text = re.sub(r"\s*\.\d+(\s*,\s*\d+)*\s*$", "", text)
    # "NFY family [12]" → "NFY family"
    text = re.sub(r"\s*\[\d+\]\s*$", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    # "human human APOE" → "human APOE"
    words = text.split()
    dedup = [words[0]] + [w for i, w in enumerate(words[1:], 1) if w.lower() != words[i-1].lower()]
    return " ".join(dedup)


def _extract_embedded_ids(text: str) -> list:
    """Return canonical IDs embedded in a name (rsID, Ensembl, UniProt, OBO) using format patterns only."""
    found = []
    # dbSNP rsID: exactly rs + 4-12 digits (NCBI dbSNP format)
    for m in re.finditer(r"\brs\d{4,12}\b", text, re.I):
        found.append((m.group().lower(), "dbsnp"))
    for m in re.finditer(r"\bENS[A-Z]*G\d{8,}\b", text):  # any organism's Ensembl gene ID
        found.append((f"ENSEMBL:{m.group()}", "ensembl"))
    # UniProt accession (6 or 10 chars): covers P12345, Q9Y2T1, A0A024RBG1, etc.
    for m in re.finditer(r"\b(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]{5}|[A-NR-Z][0-9][A-Z0-9]{8}[0-9])\b", text):
        found.append((f"UniProtKB:{m.group()}", "uniprot"))
    # OBO-style prefixed ID already embedded: e.g. "gene GO:0006914 expression"
    for m in re.finditer(r"\b([A-Z]{2,10}:[A-Z0-9_]{3,15})\b", text):
        if _is_canonical_id(m.group()):
            found.append((m.group(), "embedded_obo"))
    return found


def _try_core_term(text: str, entity_type: str, species: str = "") -> Optional[tuple]:
    """Try progressively shorter prefixes of composite names via OLS4/Ensembl. Returns (id, label, prefix, was_exact)."""
    words = text.strip().split()
    if len(words) < 2:
        return None
    ontologies = _TYPE_TO_ONTOLOGIES.get(entity_type, [])
    if entity_type in ("GENE", "PROTEIN") and "hgnc" in ontologies \
            and _ensembl_species_slug(species) != "human":
        ontologies = [o for o in ontologies if o != "hgnc"]  # HGNC is human-only
    for n_words in range(len(words) - 1, 0, -1):
        core = " ".join(words[:n_words]).strip()
        if len(core) < 2:
            break
        candidate = _ols4_search(core, ontologies)
        if candidate:
            obo_id, label, prefix, was_exact, _synonyms, _iri = candidate
            return (obo_id, label, prefix, was_exact)
        if entity_type in ("GENE", "PROTEIN", "TRANSCRIPT", "NON_CODING_RNA", ""):
            eid = _ensembl_search(core, species=_ensembl_species_slug(species))
            if eid:
                return (*eid, True)   # Ensembl hits are pattern/DB lookups, not fuzzy text search
    return None


def _is_canonical_id(s: str) -> bool:
    """True if s looks like a real canonical ID (has a DB prefix or is an rsID)."""
    if not s:
        return False
    if ":" in s:               # MESH:D..., GO:..., CHEBI:..., NCBITaxon:...
        return True
    if re.match(r"^rs\d+$", s, re.I):   # dbSNP rsID
        return True
    if re.match(r"^ENS[A-Z]*[GT]\d+$", s):  # Ensembl gene/transcript, any organism
        return True
    if re.match(r"^P\d{5}$", s):  # UniProt
        return True
    return False


_OLS4_IRI_TEMPLATE: dict = {
    "mesh":      "http://id.nlm.nih.gov/mesh/{bare}",
    "go":        "http://purl.obolibrary.org/obo/GO_{bare}",
    "chebi":     "http://purl.obolibrary.org/obo/CHEBI_{bare}",
    "doid":      "http://purl.obolibrary.org/obo/DOID_{bare}",
    "mondo":     "http://purl.obolibrary.org/obo/MONDO_{bare}",
    "hp":        "http://purl.obolibrary.org/obo/HP_{bare}",
    "mp":        "http://purl.obolibrary.org/obo/MP_{bare}",
    "ncbitaxon": "http://purl.obolibrary.org/obo/NCBITaxon_{bare}",
    "uberon":    "http://purl.obolibrary.org/obo/UBERON_{bare}",
    "pw":        "http://purl.obolibrary.org/obo/PW_{bare}",
}

_external_id_label_cache: dict = {}


def _external_id_label(canonical_id: str, timeout: int = _TIMEOUT) -> str:
    """Look up the official ontology label for a ready-made canonical ID. Returns "" for unsupported prefixes."""
    if ":" not in canonical_id:
        return ""
    prefix, bare = canonical_id.split(":", 1)
    template = _OLS4_IRI_TEMPLATE.get(prefix.lower())
    if not template:
        return ""
    cache_key = (prefix.lower(), bare)
    if cache_key in _external_id_label_cache:
        return _external_id_label_cache[cache_key]

    label = ""
    try:
        iri = template.format(bare=bare)
        encoded = quote(quote(iri, safe=""), safe="")
        r = requests.get(f"{_OLS_BASE}/ontologies/{prefix.lower()}/terms/{encoded}",
                         headers={"Accept": "application/json"}, timeout=timeout)
        doc = r.json()
        label = doc.get("label", "") or ""
        if isinstance(label, list):
            label = label[0] if label else ""
    except Exception as e:
        logger.warning("External ID label lookup failed for %r: %s", canonical_id, e)

    _external_id_label_cache[cache_key] = label
    return label


# ── OLS4: universal ontology search ─────────────────────────────────────────

_MESH_INVERT_RE = re.compile(r"^([^,]+),\s*(.+)$")


def _uninvert_mesh_label(label: str, ontology_prefix: str) -> str:
    if ontology_prefix.lower() != "mesh":
        return label
    m = _MESH_INVERT_RE.match(label)
    if not m:
        return label
    noun, qualifier = m.group(1).strip(), m.group(2).strip()
    return f"{qualifier} {noun}"


def _ols4_search(text: str, ontologies: list, timeout: int = _TIMEOUT) -> Optional[tuple]:
    """Search EBI OLS4, optionally scoped to specific ontologies; returns (id, label, ontology_prefix, was_exact)."""
    cache_key = (text.lower().strip(), tuple(sorted(ontologies)))
    if cache_key in _ols4_cache:
        return _ols4_cache[cache_key]
    result = _ols4_search_uncached(text, ontologies, timeout)
    _ols4_cache[cache_key] = result
    return result


def _ols4_search_uncached(text: str, ontologies: list, timeout: int = _TIMEOUT) -> Optional[tuple]:
    """Actual OLS4 HTTP call — exact match first, fuzzy fallback. Each ontology queried separately for relevance."""
    query_norm = text.strip().lower()
    fallback: Optional[tuple] = None
    onto_groups = [[o] for o in ontologies] if ontologies else [[]]

    for exact in ("true", "false"):
        for onto_group in onto_groups:
            try:
                params: dict = {
                    "q":     text,
                    "rows":  10,
                    "exact": exact,
                    "fieldList": "id,obo_id,label,ontology_prefix,synonym,iri",
                }
                if onto_group:
                    params["ontology"] = onto_group[0]

                r = requests.get(f"{_OLS_BASE}/search", params=params,
                                 headers={"Accept": "application/json"}, timeout=timeout)
                docs = r.json().get("response", {}).get("docs", [])
                for doc in docs:
                    obo_id = doc.get("obo_id") or doc.get("id", "")
                    label  = doc.get("label") or ""
                    if isinstance(label, list):
                        label = label[0] if label else ""
                    prefix = doc.get("ontology_prefix") or ""
                    if isinstance(prefix, list):
                        prefix = prefix[0] if prefix else ""
                    iri = doc.get("iri") or ""
                    if isinstance(iri, list):
                        iri = iri[0] if iri else ""
                    syns = doc.get("synonym") or []
                    if isinstance(syns, str):
                        syns = [syns]
                    synonyms = "; ".join(s for s in syns if s and s.lower() != label.lower())
                    # Normalise to standard prefix:ID format
                    if obo_id and "_" in obo_id:
                        # OLS returns GO_0006914 → normalise to GO:0006914
                        obo_id = obo_id.replace("_", ":", 1)
                    if not (obo_id and ":" in obo_id):
                        continue

                    label_clean = _uninvert_mesh_label(label, prefix)
                    label_norm  = label.strip().lower()
                    syn_norms   = {s.strip().lower() for s in syns if s}
                    is_true_exact = label_norm == query_norm or query_norm in syn_norms

                    if is_true_exact:
                        return (obo_id, label_clean, prefix, True, synonyms, iri)
                    if fallback is None:
                        fallback = (obo_id, label_clean, prefix, False, synonyms, iri)
            except Exception as e:
                logger.warning("OLS4 search failed for %r (ontology=%s, exact=%s): %s", text, onto_group, exact, e)
    return fallback


# ── NCBI eSearch: genes, variants, taxonomy ──────────────────────────────────

_ensembl_label_cache: dict = {}
_ensembl_search_cache: dict = {}


_GENE_SUFFIX_STRIP = re.compile(
    r"\s+(mutations?|variants?|deficiency|deficiencies|alleles?|gene)$", re.I
)


def _strip_gene_suffix(text: str) -> Optional[str]:
    """Strip qualifier suffix from gene names (e.g. 'PIGV mutations' → 'PIGV'). Returns None if nothing to strip."""
    stripped = _GENE_SUFFIX_STRIP.sub("", text).strip()
    return stripped if stripped and stripped.lower() != text.lower() else None


def _ensembl_search(text: str, species: str = "human",
                    timeout: int = _TIMEOUT, retries: int = 2) -> Optional[tuple]:
    """Cached wrapper around _ensembl_search_uncached. Only successful lookups are cached."""
    cache_key = (text.strip().lower(), species)
    if cache_key in _ensembl_search_cache:
        return _ensembl_search_cache[cache_key]
    result = _ensembl_search_uncached(text, species, timeout, retries)
    if result is not None:
        _ensembl_search_cache[cache_key] = result
    return result


def _ensembl_search_uncached(text: str, species: str = "human",
                             timeout: int = _TIMEOUT, retries: int = 2) -> Optional[tuple]:
    """Ensembl REST API — returns (canonical_id, display_name). Retries on transient failures."""
    candidates = [text]
    stripped = _strip_gene_suffix(text)
    if stripped:
        candidates.append(stripped)

    last_err = None
    for candidate in candidates:
        for attempt in range(1, retries + 1):
            try:
                r = requests.get(
                    f"https://rest.ensembl.org/xrefs/symbol/{species}/{candidate}",
                    params={"content-type": "application/json", "object_type": "gene"},
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
                data = r.json()
                if isinstance(data, list) and data:
                    eid = data[0].get("id", "")
                    # ENS[species code]G prefix covers all organisms, not just human (ENSG).
                    if re.match(r"^ENS[A-Z]*G\d+$", eid):
                        label = _ensembl_display_name(eid, timeout) or candidate
                        return (f"ENSEMBL:{eid}", label, "")
                last_err = None
                break  # valid response, legitimately no match for this candidate
            except Exception as e:
                last_err = e
    if last_err:
        logger.warning("Ensembl xrefs/symbol lookup failed for %r (species=%s) after %d attempt(s): %s",
                        text, species, retries, last_err)
    return None


def _ensembl_display_name(ensembl_id: str, timeout: int = _TIMEOUT, retries: int = 2) -> Optional[str]:
    """Cached lookup of Ensembl's own canonical gene symbol (display_name) for an ID."""
    if ensembl_id in _ensembl_label_cache:
        return _ensembl_label_cache[ensembl_id]
    label = None
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                f"https://rest.ensembl.org/lookup/id/{ensembl_id}",
                params={"content-type": "application/json"},
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            label = r.json().get("display_name") or None
            last_err = None
            break
        except Exception as e:
            last_err = e
    if last_err:
        logger.warning("Ensembl lookup/id failed for %r after %d attempt(s): %s", ensembl_id, retries, last_err)
    _ensembl_label_cache[ensembl_id] = label
    return label


def _ncbi_search(text: str, db: str, prefix: str,
                 extra_term: str = "", timeout: int = _TIMEOUT,
                 fields: tuple = ("All Fields",)) -> Optional[str]:
    """Generic NCBI eSearch. fields restricts which record field must match, tried in order."""
    try:
        for field in fields:
            term = f"{text}[{field}]"
            if extra_term:
                term += f" AND {extra_term}"
            r = requests.get(f"{_NCBI_BASE}/esearch.fcgi",
                             params={"db": db, "term": term, "retmax": 1, "retmode": "json"},
                             timeout=timeout)
            ids = r.json().get("esearchresult", {}).get("idlist", [])
            if ids:
                return f"{prefix}{ids[0]}"
    except Exception as e:
        logger.warning("NCBI eSearch failed for %r (db=%s, fields=%s): %s", text, db, fields, e)
    return None


def _ncbi_gene_summary(gene_id: str, timeout: int = _TIMEOUT) -> tuple:
    """NCBI Gene eSummary — returns (official_symbol, synonyms_str) for a gene ID."""
    try:
        r = requests.get(f"{_NCBI_BASE}/esummary.fcgi",
                         params={"db": "gene", "id": gene_id, "retmode": "json"},
                         timeout=timeout)
        doc = r.json().get("result", {}).get(gene_id, {})
        name = doc.get("name", "") or ""
        syn_set = []
        for alias in (doc.get("otheraliases", "") or "").split(","):
            alias = alias.strip()
            if alias:
                syn_set.append(alias)
        for desig in (doc.get("otherdesignations", "") or "").split("|"):
            desig = desig.strip()
            if desig:
                syn_set.append(desig)
        synonyms = "; ".join(dict.fromkeys(s for s in syn_set if s.lower() != name.lower()))
        return (name, synonyms)
    except Exception as e:
        logger.warning("NCBI Gene eSummary failed for %r: %s", gene_id, e)
    return ("", "")


_ncbi_to_ensembl_cache: dict = {}


def _ncbi_gene_to_ensembl(ncbi_gene_id: str, timeout: int = _TIMEOUT) -> str:
    """Cross-reference an NCBI Gene ID to its Ensembl gene ID via MyGene.info."""
    if ncbi_gene_id in _ncbi_to_ensembl_cache:
        return _ncbi_to_ensembl_cache[ncbi_gene_id]
    ensembl_id = ""
    try:
        r = requests.get(f"https://mygene.info/v3/gene/{ncbi_gene_id}",
                          params={"fields": "ensembl.gene"}, timeout=timeout)
        if r.ok:
            data = r.json()
            ens = data.get("ensembl", {})
            if isinstance(ens, list):   # some genes have multiple Ensembl IDs
                ens = ens[0] if ens else {}
            ensembl_id = ens.get("gene", "") or ""
    except Exception as e:
        logger.warning("NCBI Gene → Ensembl cross-reference failed for %r: %s", ncbi_gene_id, e)
    _ncbi_to_ensembl_cache[ncbi_gene_id] = ensembl_id
    return ensembl_id


def _pubchem_search(text: str, timeout: int = _TIMEOUT) -> Optional[tuple]:
    """PubChem REST API for small molecules not in ChEBI. Returns (canonical_id, name)."""
    try:
        r = requests.get(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{text}/cids/JSON",
            timeout=timeout)
        cids = r.json().get("IdentifierList", {}).get("CID", [])
        if not cids:
            return None
        cid = cids[0]
        name = ""
        try:
            r2 = requests.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/Title/JSON",
                timeout=timeout)
            props = r2.json().get("PropertyTable", {}).get("Properties", [])
            if props:
                name = props[0].get("Title", "") or ""
        except Exception as e:
            logger.warning("PubChem title lookup failed for CID %r: %s", cid, e)
        return (f"PUBCHEM:{cid}", name)
    except Exception as e:
        logger.warning("PubChem search failed for %r: %s", text, e)
    return None


def _uniprot_search(text: str, species: str = "Homo sapiens", timeout: int = _TIMEOUT) -> Optional[tuple]:
    """UniProt REST API — canonical protein accessions. Returns (id, label, prefix, synonyms)."""
    try:
        query = f'{text} AND organism_name:"{species}" AND reviewed:true'
        r = requests.get(
            "https://rest.uniprot.org/uniprotkb/search",
            params={"query": query, "format": "json", "size": 1,
                    "fields": "accession,protein_name,gene_names"},
            headers={"Accept": "application/json"}, timeout=timeout,
        )
        results = r.json().get("results", [])
        if results:
            entry = results[0]
            acc = entry.get("primaryAccession", "")
            if acc:
                label = ""
                genes = entry.get("genes") or []
                if genes:
                    label = (genes[0].get("geneName") or {}).get("value", "") or ""
                if not label:
                    label = (
                        (entry.get("proteinDescription") or {})
                        .get("recommendedName", {})
                        .get("fullName", {})
                        .get("value", "")
                    ) or ""
                syn_set = []
                prot_desc = entry.get("proteinDescription") or {}
                for alt in (prot_desc.get("recommendedName") or {}).get("shortNames", []) or []:
                    if alt.get("value"):
                        syn_set.append(alt["value"])
                for alt_name in prot_desc.get("alternativeNames", []) or []:
                    if (alt_name.get("fullName") or {}).get("value"):
                        syn_set.append(alt_name["fullName"]["value"])
                    for short in alt_name.get("shortNames", []) or []:
                        if short.get("value"):
                            syn_set.append(short["value"])
                if genes:
                    for syn in genes[0].get("synonyms", []) or []:
                        if syn.get("value"):
                            syn_set.append(syn["value"])
                synonyms = "; ".join(dict.fromkeys(s for s in syn_set if s.lower() != label.lower()))
                return (f"UniProtKB:{acc}", label, "", synonyms)
    except Exception as e:
        logger.warning("UniProt search failed for %r (species=%s): %s", text, species, e)
    return None


_rxnorm_name_cache: dict = {}


def _rxnorm_name(rxcui: str, timeout: int = _TIMEOUT) -> str:
    """RxNorm display name for an RXCUI. Returns "" when unavailable."""
    if rxcui in _rxnorm_name_cache:
        return _rxnorm_name_cache[rxcui]
    name = ""
    try:
        r = requests.get(
            f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/property.json",
            params={"propName": "RxNorm Name"}, timeout=timeout,
        )
        concepts = r.json().get("propConceptGroup", {}).get("propConcept", [])
        if concepts:
            name = concepts[0].get("propValue", "") or ""
    except Exception as e:
        logger.warning("RxNorm name lookup failed for RXCUI %r: %s", rxcui, e)
    _rxnorm_name_cache[rxcui] = name
    return name


def _rxnorm_search(text: str, timeout: int = _TIMEOUT) -> Optional[tuple]:
    """RxNorm API — resolves drug names/brands to RxCUI. Returns (canonical_id, name, was_confident)."""
    try:
        r = requests.get(
            "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": text, "search": 1}, timeout=timeout,
        )
        rxcui_list = r.json().get("idGroup", {}).get("rxnormId", [])
        rxcui, was_exact = (rxcui_list[0], True) if rxcui_list else (None, False)

        if not rxcui:
            # Approximate match for misspellings / synonyms not in exact index
            r2 = requests.get(
                "https://rxnav.nlm.nih.gov/REST/approximateTerm.json",
                params={"term": text, "maxEntries": 1}, timeout=timeout,
            )
            candidates = r2.json().get("approximateGroup", {}).get("candidate", [])
            rxcui = candidates[0].get("rxcui", "") if candidates else None

        if not rxcui:
            return None

        name = _rxnorm_name(rxcui, timeout)
        return (f"RxNorm:{rxcui}", name, was_exact and bool(name))
    except Exception as e:
        logger.warning("RxNorm search failed for %r: %s", text, e)
    return None


def _hmdb_search(_text: str, _timeout: int = _TIMEOUT) -> Optional[str]:
    """HMDB stub — hmdb.ca blocks programmatic access; PubChem is used instead."""
    return None


def _wikidata_search(text: str, entity_type: str, timeout: int = _TIMEOUT) -> Optional[tuple]:
    """Wikidata entity search — broad fallback. Returns (canonical DB ID or QID, matched label)."""
    _WD_TYPE_HINTS = {
        "GENE":          "P351",    # NCBI Gene ID
        "PROTEIN":       "P352",    # UniProt ID
        "DISEASE":       "P699",    # Disease Ontology ID
        "SMALL_MOLECULE":"P662",    # PubChem CID
        "PATHWAY":       "P2410",   # Reactome ID
        "ORGANISM":      "P685",    # NCBI Taxonomy ID
    }
    prop = _WD_TYPE_HINTS.get(entity_type, "")
    try:
        r = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbsearchentities", "search": text,
                    "language": "en", "format": "json", "limit": 1},
            headers={"User-Agent": "bio-semantic-parser/1.0 (research@rejuve.bio)"},
            timeout=timeout,
        )
        items = r.json().get("search", [])
        if items:
            qid   = items[0].get("id", "")
            label = items[0].get("label", "") or items[0].get("display", {}).get("label", {}).get("value", "")
            if qid:
                if prop:
                    r2 = requests.get(
                        f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json",
                        timeout=timeout,
                    )
                    claims = r2.json().get("entities", {}).get(qid, {}).get("claims", {})
                    vals = claims.get(prop, [])
                    if vals:
                        ext_id = vals[0].get("mainsnak", {}).get("datavalue", {}).get("value", "")
                        if ext_id:
                            _WD_PREFIX = {
                                "GENE":          "NCBI_GENE:",
                                "PROTEIN":       "UniProtKB:",
                                "DISEASE":       "DOID:",
                                "SMALL_MOLECULE":"PUBCHEM:",
                                "PATHWAY":       "REACTOME:",
                                "ORGANISM":      "NCBITaxon:",
                            }
                            pfx = _WD_PREFIX.get(entity_type, "")
                            return (f"{pfx}{ext_id}" if pfx else ext_id, label)
                return (f"WD:{qid}", label)
    except Exception as e:
        logger.warning("Wikidata search failed for %r (entity_type=%s): %s", text, entity_type, e)
    return None


_WIKIDATA_CROSSREF_PREFIXES = (
    ("uniprot",    "UniProtKB:"),
    ("ncbi_gene",  "NCBI_GENE:"),
    ("pubchem",    "PUBCHEM:"),
    ("ncbi_taxon", "NCBITaxon:"),
)


def _build_source_url(canonical_id: str, id_source: str, ontology_prefix: str, iri: str = "",
                       ensembl_species: str = "") -> str:
    if not canonical_id or canonical_id == "NEEDS_REVIEW" or canonical_id.startswith("TEXT:"):
        return ""

    if id_source == "pubtator3":
        # PubTator3 dispatches on the ID's own format (no ontology_prefix available).
        if canonical_id.startswith("tmVar:"):
            # tmVar composite — extract embedded rsID if present, else no URL.
            m = re.search(r"RS#:(\d+)", canonical_id)
            if m:
                return f"https://www.ncbi.nlm.nih.gov/snp/rs{m.group(1)}"
            return ""
        if canonical_id.upper().startswith("MESH:"):
            return f"https://meshb.nlm.nih.gov/record/ui?ui={canonical_id.split(':', 1)[1]}"
        if canonical_id.startswith("NCBITaxon:"):
            return f"https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={canonical_id.split(':', 1)[1]}"
        if re.match(r"^rs\d+$", canonical_id, re.I):
            return f"https://www.ncbi.nlm.nih.gov/snp/{canonical_id}"
        if re.match(r"^ENS[A-Z]*[GT]\d+$", canonical_id):
            # Generic /id/ redirect resolves species server-side for any organism.
            return f"https://www.ensembl.org/id/{canonical_id}"
        if re.match(r"^P\d{5}$", canonical_id):
            return f"https://www.uniprot.org/uniprotkb/{canonical_id}/entry"
        if ":" in canonical_id:
            # Some other ontology-style ID (GO:, CHEBI:, ...) — best-effort
            # OLS4 search, since we have no IRI to build a direct link from here.
            prefix, bare_id = canonical_id.split(":", 1)
            return f"https://www.ebi.ac.uk/ols4/search?q={bare_id}&ontology={prefix.lower()}"
        return ""

    if id_source == "uniprot" and canonical_id.startswith("UniProtKB:"):
        return f"https://www.uniprot.org/uniprotkb/{canonical_id.split(':', 1)[1]}/entry"

    if id_source == "ncbi_gene" and canonical_id.startswith("NCBI_GENE:"):
        return f"https://www.ncbi.nlm.nih.gov/gene/{canonical_id.split(':', 1)[1]}"

    if id_source == "ensembl" and canonical_id.startswith("ENSEMBL:"):
        # Generic /id/ redirect resolves species server-side for any organism.
        return f"https://www.ensembl.org/id/{canonical_id.split(':', 1)[1]}"

    if id_source == "pubchem" and canonical_id.startswith("PUBCHEM:"):
        return f"https://pubchem.ncbi.nlm.nih.gov/compound/{canonical_id.split(':', 1)[1]}"

    if id_source == "dbsnp":
        rsid = canonical_id.split(":", 1)[-1]
        return f"https://www.ncbi.nlm.nih.gov/snp/{rsid}"

    if id_source == "ncbi_taxon" and canonical_id.startswith("NCBITaxon:"):
        return f"https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={canonical_id.split(':', 1)[1]}"

    if id_source == "rxnorm" and canonical_id.startswith("RxNorm:"):
        return f"https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm={canonical_id.split(':', 1)[1]}"

    if id_source == "wikidata":
        if canonical_id.startswith("WD:"):
            return f"https://www.wikidata.org/wiki/{canonical_id.split(':', 1)[1]}"
        for src, check_prefix in _WIKIDATA_CROSSREF_PREFIXES:
            if canonical_id.startswith(check_prefix):
                return _build_source_url(canonical_id, src, "")
        return ""

    if id_source in ("ols4", "ols4_broad", "ols4_core") and ":" in canonical_id:
        bare_id = canonical_id.split(":", 1)[1]
        if ontology_prefix.lower() == "mesh":
            return f"https://meshb.nlm.nih.gov/record/ui?ui={bare_id}"
        onto = ontology_prefix or canonical_id.split(":", 1)[0].lower()
        if iri:
            # OLS4 term page uses the IRI double URL-encoded as the path segment.
            encoded_iri = quote(quote(iri, safe=""), safe="")
            return f"https://www.ebi.ac.uk/ols4/ontologies/{onto.lower()}/classes/{encoded_iri}"
        return f"https://www.ebi.ac.uk/ols4/search?q={bare_id}&ontology={onto}"

    return ""


# ── Main normalization function ───────────────────────────────────────────────

def normalize_entity(
    text: str,
    entity_type: str,
    existing_id: Optional[str] = None,
    species: str = "",
    context: str = "",
) -> dict:
    """Normalize one entity to a canonical ID. Returns dict with canonical_id, canonical_name, id_source, needs_review, source_url."""
    result = _normalize_entity_core(text, entity_type, existing_id, species, context)
    result["source_url"] = _build_source_url(
        result["canonical_id"], result["id_source"], result.get("ontology_prefix", ""),
        result.get("iri", ""), result.get("ensembl_species", "")
    )
    return result


def _normalize_entity_core(
    text: str,
    entity_type: str,
    existing_id: Optional[str] = None,
    species: str = "",
    context: str = "",
) -> dict:
    """Core normalization logic. Returns dict with canonical_id, canonical_name, id_source, ontology_prefix, needs_review."""
    if not text or not text.strip():
        return {"canonical_id": "NEEDS_REVIEW", "canonical_name": "", "id_source": "review", "ontology_prefix": "", "needs_review": True}

    # ── Priority 1: PubTator3 ID — validated against ontology label when available.
    if existing_id and _is_canonical_id(existing_id):
        ref_label = _external_id_label(existing_id)
        pubtator_risk = bool(ref_label) and _is_confidently_wrong(text, ref_label)
        return {"canonical_id": existing_id, "canonical_name": ref_label,
                "id_source": "pubtator3", "ontology_prefix": "", "needs_review": pubtator_risk}

    # ── Pre-clean: strip PDF artifacts before any lookup ─────────────────────
    text = _clean_name(text)
    if not text:
        return {"canonical_id": "NEEDS_REVIEW", "canonical_name": "", "id_source": "review", "ontology_prefix": "", "needs_review": True}

    # Strip gene qualifiers once so all resolvers see the same clean symbol.
    if entity_type in ("GENE", "TRANSCRIPT", "EXON", "NON_CODING_RNA"):
        text = _strip_gene_suffix(text) or text

    # ── Pattern extraction: canonical IDs embedded in composite names ─────────
    embedded = _extract_embedded_ids(text)
    if embedded:
        canon_id, source = embedded[0]
        return {"canonical_id": canon_id, "canonical_name": "", "id_source": source, "ontology_prefix": "", "needs_review": False}

    # ── Infer entity type from name structure when LLM type is unreliable ─────
    if re.match(r"^rs\d{4,}$", text.strip(), re.I):
        # rsIDs are always dbSNP regardless of declared entity_type
        return {"canonical_id": text.strip().lower(), "canonical_name": "", "id_source": "dbsnp", "ontology_prefix": "", "needs_review": False}
    if re.match(r"^ENS[A-Z]*G\d{8,}$", text.strip()):
        return {"canonical_id": f"ENSEMBL:{text.strip()}", "canonical_name": "", "id_source": "ensembl", "ontology_prefix": "", "needs_review": False}

    # ── Priority 2: Fast local lookup for common organisms ───────────────────
    if entity_type == "ORGANISM":
        lower = text.lower().strip()
        if lower in _TAXON_LOCAL:
            return {"canonical_id": f"NCBITaxon:{_TAXON_LOCAL[lower]}",
                    "canonical_name": "", "id_source": "ncbi_taxon", "ontology_prefix": "", "needs_review": False}

    # ── Priority 3: Ensembl — checked before OLS4 so genes get ENSG not HGNC ───
    # (management requirement: gene IDs must be Ensembl, not HGNC/NCBI Gene)
    if entity_type in ("GENE", "TRANSCRIPT", "EXON", "NON_CODING_RNA"):
        species_slug = _ensembl_species_slug(species)
        ens = _ensembl_search(text, species=species_slug)
        if ens:
            eid, elabel, eprefix = ens
            return {"canonical_id": eid, "canonical_name": elabel, "id_source": "ensembl",
                    "ontology_prefix": eprefix, "needs_review": False, "ensembl_species": species_slug}

    # ── Priority 4: UniProt — proteins' canonical source (before OLS4 Protein Ontology).
    if entity_type == "PROTEIN":
        up = _uniprot_search(text, species=species or "Homo sapiens")
        if up:
            uid, ulabel, uprefix, usynonyms = up
            if not _is_confidently_wrong(text, ulabel):
                return {"canonical_id": uid, "canonical_name": ulabel, "id_source": "uniprot",
                        "ontology_prefix": uprefix, "needs_review": False, "synonyms": usynonyms}

    # ── Priority 5: OLS4 universal search ──────────────────────────────────────
    ontologies = _TYPE_TO_ONTOLOGIES.get(entity_type, [])
    if entity_type in ("GENE", "PROTEIN") and "hgnc" in ontologies \
            and _ensembl_species_slug(species) != "human":
        # Exclude HGNC for non-human species — it's human-only.
        ontologies = [o for o in ontologies if o != "hgnc"]
    ols = _ols4_search(text, ontologies)

    if (not ols or not ols[3]) and context and len(text.split()) <= _CONTEXT_EXPAND_MAX_WORDS:
        expanded = _context_expand(text, entity_type, context)
        if expanded.strip().lower() != text.strip().lower():
            expanded_ols = _ols4_search(expanded, ontologies)
            if expanded_ols and expanded_ols[3]:
                ols = expanded_ols

    if ols:
        ols_id, ols_label, ols_prefix, ols_exact, ols_synonyms, ols_iri = ols
        if not _is_confidently_wrong(text, ols_label):
            # Fuzzy OLS4 matches route to human review — token overlap alone can't rule out wrong-entity hits.
            fuzzy_risk = not ols_exact
            return {"canonical_id": ols_id, "canonical_name": ols_label, "id_source": "ols4",
                    "ontology_prefix": ols_prefix, "needs_review": fuzzy_risk, "synonyms": ols_synonyms,
                    "iri": ols_iri}

    # ── Priority 6: RxNorm — covers brand names OLS4/ChEBI miss (e.g. Rapamune) ─
    if entity_type == "SMALL_MOLECULE":
        rx = _rxnorm_search(text)
        if rx:
            rxid, rxname, rx_confident = rx
            return {"canonical_id": rxid, "canonical_name": rxname, "id_source": "rxnorm",
                    "ontology_prefix": "", "needs_review": not rx_confident}

    # ── Priority 7: HMDB — metabolite synonyms OLS4 ChEBI search misses ────────
    if entity_type == "SMALL_MOLECULE":
        hid = _hmdb_search(text)
        if hid:
            return {"canonical_id": hid, "canonical_name": "", "id_source": "hmdb", "ontology_prefix": "", "needs_review": False}

    # ── Priority 8: NCBI eSearch fallback per entity type ────────────────────
    if entity_type in ("GENE", "PROTEIN", "TRANSCRIPT", "EXON", "NON_CODING_RNA"):
        gid = _ncbi_search(text, "gene", "NCBI_GENE:", f"{species or 'Homo sapiens'}[Organism]",
                            fields=("sym", "Gene Name"))
        if gid:
            gname, gsynonyms = _ncbi_gene_summary(gid.split(":", 1)[1])
            text_norm = text.strip().lower()
            syn_norms = {s.strip().lower() for s in gsynonyms.split("; ") if s.strip()}
            gene_risk = bool(gname) and text_norm != gname.strip().lower() and text_norm not in syn_norms

            ens_id = _ncbi_gene_to_ensembl(gid.split(":", 1)[1])
            if ens_id:
                return {"canonical_id": f"ENSEMBL:{ens_id}", "canonical_name": gname,
                        "id_source": "ensembl", "ontology_prefix": "", "needs_review": gene_risk,
                        "synonyms": gsynonyms}

            return {"canonical_id": gid, "canonical_name": gname, "id_source": "ncbi_gene",
                    "ontology_prefix": "", "needs_review": gene_risk, "synonyms": gsynonyms}

    if entity_type in ("GENOMIC_VARIANT", "SEQUENCE_VARIANT", "HAPLOTYPE"):
        snp = _ncbi_search(text, "snp", "rs")
        if snp:
            return {"canonical_id": snp, "canonical_name": "", "id_source": "dbsnp", "ontology_prefix": "", "needs_review": False}

    if entity_type == "ORGANISM":
        tid = _ncbi_search(text, "taxonomy", "NCBITaxon:")
        if tid:
            return {"canonical_id": tid, "canonical_name": "", "id_source": "ncbi_taxon", "ontology_prefix": "", "needs_review": False}

    if entity_type == "SMALL_MOLECULE":
        pub = _pubchem_search(text)
        if pub:
            pub_id, pub_name = pub
            return {"canonical_id": pub_id, "canonical_name": pub_name, "id_source": "pubchem", "ontology_prefix": "", "needs_review": False}

    # ── Priority 9: OLS4 without ontology filter — always, not just typed ────
    ols_broad = _ols4_search(text, [])
    if ols_broad:
        ob_id, ob_label, ob_prefix, ob_exact, ob_synonyms, ob_iri = ols_broad
        if not _is_confidently_wrong(text, ob_label):
            fuzzy_risk = not ob_exact
            return {"canonical_id": ob_id, "canonical_name": ob_label, "id_source": "ols4_broad",
                    "ontology_prefix": ob_prefix, "needs_review": fuzzy_risk, "synonyms": ob_synonyms,
                    "iri": ob_iri}

    # ── Priority 10: Composite decomposition — "PICALM loci" → PICALM ──────────
    core = _try_core_term(text, entity_type, species)
    if core and not _is_confidently_wrong(text, core[1]):
        core_id, core_label, core_prefix, core_exact = core
        return {"canonical_id": core_id, "canonical_name": core_label, "id_source": "ols4_core",
                "ontology_prefix": core_prefix, "needs_review": not core_exact}

    # ── Priority 11: Wikidata — broad fallback covering anything not in above ──
    wd = _wikidata_search(text, entity_type)
    if wd:
        wd_id, wd_label = wd
        if not _is_confidently_wrong(text, wd_label):
            return {"canonical_id": wd_id, "canonical_name": "", "id_source": "wikidata", "ontology_prefix": "", "needs_review": False}

    # ── Priority 12: TEXT:slug — consistent fallback, never bare text ─────────
    slug = _slug(text)
    if slug:
        return {"canonical_id": f"TEXT:{slug}", "canonical_name": "", "id_source": "fuzzy", "ontology_prefix": "", "needs_review": True}

    return {"canonical_id": "NEEDS_REVIEW", "canonical_name": "", "id_source": "review", "ontology_prefix": "", "needs_review": True}


# ── OTHER-type correction — only overrides "OTHER"; never overrides a specific LLM type ──

# ID-format-derived types are trusted outright.
_ID_FORMAT_TYPE: dict = {
    "ensembl": "GENE",
    "uniprot": "PROTEIN",
    "dbsnp":   "GENOMIC_VARIANT",
}

# OLS4 ontology namespace → EntityType (used for OTHER-typed entities via broad OLS4 search).
_ONTOLOGY_PREFIX_TYPE: dict = {
    "mondo": "DISEASE", "doid": "DISEASE", "ncit": "DISEASE",
    "chebi": "SMALL_MOLECULE",
    "hp": "PHENOTYPE", "mp": "PHENOTYPE",
    "uberon": "ANATOMY", "bto": "TISSUE",
    "cl": "CELL_TYPE", "clo": "CELL_LINE",
    "ncbitaxon": "ORGANISM",
    "pw": "PATHWAY",
    "hgnc": "GENE", "pr": "PROTEIN",
    "efo": "EXPERIMENTAL_FACTOR",
    # "mesh" and "go" excluded — both span multiple entity types.
}

# PubTator3 infons["type"] vocabulary — mapped to internal EntityType.
_PUBTATOR_TYPE: dict = {
    "Gene": "GENE", "Disease": "DISEASE", "Chemical": "SMALL_MOLECULE",
    "Species": "ORGANISM", "Strain": "ORGANISM", "CellLine": "CELL_LINE",
    "Mutation": "GENOMIC_VARIANT", "SNP": "GENOMIC_VARIANT",
    "DNAMutation": "SEQUENCE_VARIANT", "ProteinMutation": "SEQUENCE_VARIANT",
}


# MeSH tree prefixes for dietary/food headings — corrects SMALL_MOLECULE misclassifications.
_MESH_FOOD_TREE_PREFIXES = ("G07.203.650", "E02.642", "J02")
_mesh_tree_cache: dict = {}


def _mesh_tree_numbers(mesh_id: str, timeout: int = _TIMEOUT) -> list:
    """Fetch a MeSH descriptor's tree numbers from NLM's own MeSH RDF API."""
    if mesh_id in _mesh_tree_cache:
        return _mesh_tree_cache[mesh_id]
    trees: list = []
    try:
        resp = requests.get(f"https://id.nlm.nih.gov/mesh/{mesh_id}.json", timeout=timeout)
        if resp.ok:
            data = resp.json()
            trees = [t.rsplit("/", 1)[-1] for t in (data.get("treeNumber") or [])]
    except Exception as e:
        logger.warning("MeSH tree lookup failed for %r: %s", mesh_id, e)
    _mesh_tree_cache[mesh_id] = trees
    return trees


def _correct_mesh_food_type(declared_type: str, canonical_id: str) -> str:
    """Override entity_type to FOOD when the MeSH tree classification says so, even over a specific LLM guess."""
    if not canonical_id.lower().startswith("mesh:"):
        return declared_type
    mesh_id = canonical_id.split(":", 1)[1]
    trees = _mesh_tree_numbers(mesh_id)
    if any(t.startswith(_MESH_FOOD_TREE_PREFIXES) for t in trees):
        return "FOOD"
    return declared_type


def _correct_other_type(declared_type: str, id_source: str, ontology_prefix: str,
                         pubtator_type: str = "") -> str:
    """Correct entity_type when the LLM declared OTHER and normalization resolved a clearer signal."""
    if declared_type != "OTHER":
        return declared_type
    if id_source in _ID_FORMAT_TYPE:
        return _ID_FORMAT_TYPE[id_source]
    if id_source in ("ols4", "ols4_broad", "ols4_core") and ontology_prefix:
        mapped = _ONTOLOGY_PREFIX_TYPE.get(ontology_prefix.lower())
        if mapped:
            return mapped
    if pubtator_type and pubtator_type in _PUBTATOR_TYPE:
        return _PUBTATOR_TYPE[pubtator_type]
    return declared_type


def normalize_batch(records: list, chunk: dict) -> list:
    """Normalize a list of records against a single chunk."""
    return [normalize_record(r, chunk) for r in records]


def normalize_record(record: dict, annotated_chunk: dict) -> dict:
    """Normalize subject and object entities in one extraction record; adds *_id and *_id_source fields."""
    subject_name = record.get("subject_name", "")
    subject_type = record.get("subject_type", "OTHER")
    object_name  = record.get("object_name",  "")
    object_type  = record.get("object_type",  "OTHER")
    species      = record.get("species", "")
    chunk_text   = record.get("_chunk_text", "")

    layer4_map: dict = {
        e["text"].lower(): {
            "identifier": e.get("identifier") or e.get("normalized", ""),
            "label":      e.get("label", ""),
        }
        for e in annotated_chunk.get("entities", [])
        if e.get("identifier") or e.get("normalized") or e.get("label")
    }
    subj_layer4 = layer4_map.get(subject_name.lower(), {})
    obj_layer4  = layer4_map.get(object_name.lower(), {})

    subject_evidence = _extract_context_sentence(chunk_text, subject_name)
    object_evidence  = _extract_context_sentence(chunk_text, object_name)

    subj_norm = normalize_entity(
        subject_name, subject_type,
        existing_id=subj_layer4.get("identifier", ""),
        species=species,
        context=subject_evidence,
    )
    obj_norm = normalize_entity(
        object_name, object_type,
        existing_id=obj_layer4.get("identifier", ""),
        species=species,
        context=object_evidence,
    )

    subject_type = _correct_other_type(
        subject_type, subj_norm["id_source"], subj_norm.get("ontology_prefix", ""),
        pubtator_type=subj_layer4.get("label", ""),
    )
    object_type = _correct_other_type(
        object_type, obj_norm["id_source"], obj_norm.get("ontology_prefix", ""),
        pubtator_type=obj_layer4.get("label", ""),
    )
    subject_type = _correct_mesh_food_type(subject_type, subj_norm["canonical_id"])
    object_type  = _correct_mesh_food_type(object_type,  obj_norm["canonical_id"])

    review_reason = ""
    if subj_norm["needs_review"]:
        review_reason += f"subject '{subject_name}' not normalized; "
    if obj_norm["needs_review"]:
        review_reason += f"object '{object_name}' not normalized"

    return {
        **record,
        "subject_type":       subject_type,
        "object_type":        object_type,
        "subject_id":         subj_norm["canonical_id"],
        "subject_canonical_name": subj_norm["canonical_name"],
        "subject_id_source":  subj_norm["id_source"],
        "subject_needs_review": subj_norm["needs_review"],
        "subject_source_url": subj_norm.get("source_url", ""),
        "subject_synonyms":   subj_norm.get("synonyms", ""),
        "subject_evidence":   subject_evidence,
        "object_id":          obj_norm["canonical_id"],
        "object_canonical_name": obj_norm["canonical_name"],
        "object_id_source":   obj_norm["id_source"],
        "object_needs_review": obj_norm["needs_review"],
        "object_source_url":  obj_norm.get("source_url", ""),
        "object_synonyms":    obj_norm.get("synonyms", ""),
        "object_evidence":    object_evidence,
        "review_reason":      review_reason.strip("; "),
    }
