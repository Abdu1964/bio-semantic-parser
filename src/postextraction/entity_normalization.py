"""Layer 7 step 1 — maps entity text to canonical biomedical identifiers via OLS4, NCBI, UniProt, RxNorm, and Wikidata."""
import collections
import contextvars
import logging
import os
import re
import requests
from typing import Optional
from urllib.parse import quote

from src.postextraction.name_matching import match_quality, normalize_term, term_key, tokens, EXACT, NONE
import threading
import time
from urllib.parse import urlparse

# ── Network guard ──
_HOST_FAILS_TO_PAUSE = 3          # consecutive failures before a host is paused
_HOST_PAUSE_SECONDS = 120
_host_failures: dict = {}
_host_paused_until: dict = {}
_net_state = threading.local()


class HostPausedError(requests.exceptions.ConnectionError):
    """Raised instead of waiting on a host that just failed repeatedly."""


def _http_get(url, *args, **kwargs):
    """requests.get with per-host fail-fast."""
    return _http_request(requests.get, url, *args, **kwargs)


def _http_post(url, *args, **kwargs):
    """requests.post with the same per-host fail-fast as _http_get."""
    return _http_request(requests.post, url, *args, **kwargs)


_SLOW_HOSTS = {"rest.ensembl.org": 25}       # read timeout for slow hosts


def _http_request(send, url, *args, **kwargs):
    host = urlparse(url).netloc
    if host in _SLOW_HOSTS and "timeout" in kwargs:
        t = kwargs["timeout"]
        kwargs["timeout"] = (t[0], max(t[1], _SLOW_HOSTS[host])) if isinstance(t, tuple) else max(t, _SLOW_HOSTS[host])
    if time.time() < _host_paused_until.get(host, 0):
        _net_state.errors = getattr(_net_state, "errors", 0) + 1
        raise HostPausedError(f"{host} paused after repeated failures")
    try:
        resp = send(url, *args, **kwargs)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        _net_state.errors = getattr(_net_state, "errors", 0) + 1
        _host_failures[host] = _host_failures.get(host, 0) + 1
        if _host_failures[host] >= _HOST_FAILS_TO_PAUSE:
            _host_paused_until[host] = time.time() + _HOST_PAUSE_SECONDS
            logger.warning("Pausing lookups to %s for %ss after repeated network failures", host, _HOST_PAUSE_SECONDS)
        raise
    _host_failures[host] = 0
    return resp


def reset_network_state() -> None:
    """Give paused hosts another chance (e.g. before the retry pass)."""
    _host_failures.clear()
    _host_paused_until.clear()


def _net_errors() -> int:
    """Network errors in this thread's lookup."""
    return getattr(_net_state, "errors", 0)

logger = logging.getLogger(__name__)

_TIMEOUT  = (5, 8)   # (connect_timeout, read_timeout) — prevents hung sockets
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
_spell_correct_cache: dict = {}
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


def _llm_spell_correct(text: str, entity_type: str, context: str = "") -> str:
    """LLM spelling fix after a zero-result search."""
    cache_key = (text.strip().lower(), entity_type)
    if cache_key in _spell_correct_cache:
        return _spell_correct_cache[cache_key]

    corrected = text
    context_line = f'\nSource sentence: "{context[:300]}"' if context else ""
    try:
        resp = _get_llm_client().chat.completions.create(
            model=_LLM_MODEL,
            temperature=0.0,
            max_tokens=20,
            messages=[{"role": "user", "content": (
                f'The biomedical term "{text}" (type: {entity_type}) returned zero results '
                f'in an ontology search — it may be misspelled.{context_line}\n\n'
                'If you recognize the correct spelling of a real biomedical term, respond '
                'with ONLY the corrected term. If it already looks correctly spelled, '
                'respond with it unchanged. Respond with ONLY the term, no explanation.'
            )}],
        )
        candidate = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        if candidate and len(candidate.split()) <= 8:
            corrected = candidate
    except Exception:
        pass

    _spell_correct_cache[cache_key] = corrected
    return corrected

_llm_canonical_cache: dict = {}

_TYPE_TO_NS_HINT: dict = {
    "GENE":              "Ensembl gene ID — ENSEMBL:ENSG... for human, ENSEMBL:WBGene... for C. elegans, ENSEMBL:FBgn... for Drosophila. Use NCBI_GENE:<number> only if Ensembl unavailable.",
    "PROTEIN":           "UniProt accession — UniProtKB:<accession> (e.g. UniProtKB:P04637).",
    "DISEASE":           "MONDO ontology — MONDO:<number>. Fallback: MESH:D<number>.",
    "CANCER":            "MONDO ontology — MONDO:<number>. Fallback: MESH:D<number>.",
    "PHENOTYPE":         "Human Phenotype Ontology — HP:<number>. For mouse: MP:<number>.",
    "SYMPTOM":           "Human Phenotype Ontology — HP:<number>. Fallback: MESH:D<number>.",
    "ORGANISM":          "NCBI Taxonomy — NCBITaxon:<number> (e.g. NCBITaxon:6239 for C. elegans, NCBITaxon:9606 for human).",
    "ORGANISM_TAXON":    "NCBI Taxonomy — NCBITaxon:<number>.",
    "INDIVIDUAL_ORGANISM":"NCBI Taxonomy — NCBITaxon:<number>.",
    "VIRUS":             "NCBI Taxonomy — NCBITaxon:<number>.",
    "SMALL_MOLECULE":    "ChEBI — CHEBI:<number> (e.g. CHEBI:15422 for ATP). Fallback: MESH:D<number>.",
    "DRUG":              "ChEBI — CHEBI:<number>. Fallback: MESH:D<number>.",
    "PATHWAY":           "Gene Ontology — GO:<number>. Or Reactome: REACTOME:<id>.",
    "BIOLOGICAL_PROCESS":"Gene Ontology — GO:<number>.",
    "MOLECULAR_FUNCTION":"Gene Ontology — GO:<number>.",
    "CELLULAR_COMPONENT":"Gene Ontology — GO:<number>.",
    "ANATOMY":           "UBERON — UBERON:<number>. Fallback: MESH:A<number>.",
    "TISSUE":            "UBERON — UBERON:<number>. Fallback: BTO:<number>.",
    "CELL_TYPE":         "Cell Ontology — CL:<number>.",
    "CELL_LINE":         "Cell Line Ontology — CLO:<number>.",
    "GENOMIC_VARIANT":   "dbSNP rsID — rs<number>. For C. elegans alleles: WB:WBVar<number>. For Drosophila: FB:FBal<number>.",
    "SEQUENCE_VARIANT":  "dbSNP rsID — rs<number>. For model organism alleles use organism-specific databases.",
    "HAPLOTYPE":         "dbSNP rsID or ClinVar. For C. elegans alleles: WB:WBVar<number>.",
    "GENOTYPE":          "For C. elegans alleles: WB:WBVar<number>. For Drosophila: FB:FBal<number>.",
    "EXPERIMENTAL_FACTOR":"EFO — EFO:<number>.",
    "POPULATION":        "NCIT — NCIT:C<number>.",
    "CLINICAL_ENTITY":   "NCIT — NCIT:C<number>. Fallback: MESH:D<number>.",
    "TREATMENT":         "NCIT — NCIT:C<number>. Fallback: ChEBI or MESH.",
}


def _llm_guess_mismatches(text: str, ref_label: str) -> bool:
    """Compare an LLM-guessed ID's reverse-looked-up name against the original
    entity text. _is_confidently_wrong only flags multi-word mismatches (by
    design, to tolerate abbreviations) — but most gene/protein symbols are a
    single word, so that check alone would never catch a wrong single-word
    guess. Single-word text requires a close direct match instead."""
    q_tokens = _tokens(text)
    if len(q_tokens) < 2:
        return text.strip().lower() != ref_label.strip().lower()
    return _is_confidently_wrong(text, ref_label)


def _reverse_lookup_label(cid: str) -> Optional[str]:
    """Fetch a canonical ID's official name from the database it claims to be
    from, reusing the same reverse-lookup helpers used elsewhere for forward
    matches. Returns None (not a mismatch) when no reverse lookup exists for
    that prefix or the request fails — caller should fall back to self-report."""
    if ":" not in cid:
        return None
    prefix, bare = cid.split(":", 1)
    prefix_lc = prefix.lower()
    if prefix_lc in _OLS4_IRI_TEMPLATE:
        return _external_id_label(cid) or None
    if prefix_lc == "ensembl":
        return _ensembl_display_name(bare)
    if prefix_lc in ("ncbi_gene", "ncbigene", "entrez"):
        name, _ = _ncbi_gene_summary(bare)
        return name or None
    if prefix_lc in ("rxnorm", "rxcui"):
        return _rxnorm_name(bare) or None
    return None


_ID_LOCAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _malformed_id(cid: str) -> bool:
    """Placeholder or malformed ID ('MP:0000000', 'c:EsCI2D')."""
    prefix, sep, local = cid.partition(":")
    return (not sep or len(prefix) < 2 or not prefix[0].isalpha() or not _ID_LOCAL_RE.match(local)
            or (local.isdigit() and int(local) == 0) or ":" in local)


_MISSING_ID = "\x00missing"   # ID not in the database


def _reverse_lookup_names(cid: str) -> tuple:
    """(label, synonyms) for a canonical ID; ('', []) when no lookup exists or it fails."""
    prefix, _, bare = cid.partition(":")
    p = prefix.lower()
    try:
        if p == "ncbitaxon":
            r = _http_get(f"{_NCBI_BASE}/esummary.fcgi",
                             params={"db": "taxonomy", "id": bare, "retmode": "json"}, timeout=_TIMEOUT)
            d = r.json().get("result", {}).get(bare, {})
            if not d or d.get("error"):
                return _MISSING_ID, []
            return d.get("scientificname", ""), [d.get("commonname", "")]
        if p in ("uniprotkb", "uniprot"):
            r = _http_get(f"https://rest.uniprot.org/uniprotkb/{bare}.json",
                             params={"fields": "protein_name,gene_names"}, timeout=_TIMEOUT)
            if r.status_code in (400, 404, 410):
                return _MISSING_ID, []                               # no such accession
            d = r.json() if r.ok else {}
            pd = d.get("proteinDescription") or {}
            label = ((pd.get("recommendedName") or {}).get("fullName") or {}).get("value", "")
            syns = [g.get("geneName", {}).get("value", "") for g in d.get("genes") or []]
            syns += [s.get("value", "") for g in d.get("genes") or [] for s in g.get("synonyms") or []]
            syns += [((a.get("fullName") or {}).get("value", "")) for a in pd.get("alternativeNames") or []]
            return label, [x for x in syns if x]
    except Exception as e:
        logger.warning("Reverse lookup failed for %r: %s", cid, e)
        return "", []
    return (_reverse_lookup_label(cid) or ""), []


# Organism words in ontology labels like "superoxide dismutase [Cu-Zn] (human)" → genus.
_LABEL_ORGANISMS = {
    "human": "homo", "mouse": "mus", "rat": "rattus", "yeast": "saccharomyces", "budding yeast": "saccharomyces",
    "fission yeast": "schizosaccharomyces", "fruit fly": "drosophila", "fly": "drosophila", "worm": "caenorhabditis",
    "nematode": "caenorhabditis", "zebrafish": "danio", "chicken": "gallus", "bovine": "bos", "pig": "sus",
    "dog": "canis", "frog": "xenopus", "arabidopsis": "arabidopsis", "e. coli": "escherichia",
}


def _wrong_organism(label: str, species: str) -> bool:
    """Label names another organism, e.g. '(yeast)' in a fly paper."""
    m = re.search(r"\(([^()]+)\)\s*$", label or "")
    genus = (species or "").strip().split(" ")[0].lower()
    if not m or not genus:
        return False
    tag = m.group(1).strip().lower()
    named = _LABEL_ORGANISMS.get(tag) or (tag.split(" ")[0] if " " in tag and tag[0].isalpha() else "")
    return bool(named) and named != genus and named in set(_LABEL_ORGANISMS.values()) | {tag.split(" ")[0]}


_NEGATING_PREFIX = re.compile(r"^(?:non|un|anti|in|im|ir|dis|de|a)-?", re.I)
_ACRONYM = re.compile(r"^(?=.*[A-Z0-9])[A-Za-z0-9.-]{1,8}$")


def _drops_negation(text: str, new: str) -> bool:
    """Rewrite lost a negation ('nonaging' → 'aging')."""
    new_toks = set(tokens(new))
    if {"non", "not", "no", "without", "anti", "un"} & (set(normalize_term(text).replace("-", " ").split())
                                                     - set(normalize_term(new).replace("-", " ").split())):
        return True
    for t in tokens(text):
        m = _NEGATING_PREFIX.match(t)
        if m and len(t) - m.end() >= 3 and t[m.end():] in new_toks and t not in new_toks:
            return True
    return False


def _rewrite_fits(text: str, new: str) -> bool:
    """An LLM rewrite (abbreviation expansion, spelling fix) must keep the term's meaning."""
    if not isinstance(new, str) or not new.strip() or _drops_negation(text, new):
        return False
    if _ACRONYM.match(text.strip()):                           # "IL6" → "interleukin 6"
        return True
    shared = (set(tokens(text)) - _GENERIC_WORDS) & set(tokens(new))
    return bool(shared) or match_quality(text, new, []) != NONE or _typo_close_words(text, new)


def _typo_close_words(a: str, b: str) -> bool:
    import difflib
    return difflib.SequenceMatcher(None, " ".join(tokens(a)), " ".join(tokens(b))).ratio() >= 0.85


def _proposed_name_fits(text: str, proposed: str, result: dict) -> bool:
    """An LLM-proposed name is kept only if it stays tied to the original term."""
    if _drops_negation(text, proposed):
        return False
    orig = {t for t in tokens(text) if not t.isdigit()}
    prop = set(tokens(proposed))
    if prop and prop < orig:                                   # LLM only dropped words
        return (orig - prop) <= _CORE_QUALIFIERS
    syns = result.get("synonyms") or ""
    syns = [x for x in (syns.split("; ") if isinstance(syns, str) else syns) if x]
    names = [result.get("canonical_name") or ""] + syns
    if not syns:                                           # e.g. taxonomy hits
        names += _reverse_lookup_names(result.get("canonical_id", ""))[1]
    shared = {t for n in names for t in tokens(n)} & (orig - _GENERIC_WORDS)
    return bool(shared) or match_quality(text, proposed, []) != NONE


def _llm_standard_name(text: str, entity_type: str, context: str = "") -> str:
    """LLM proposes the standard name of a term (never an ID); '' if it has none."""
    cache_key = (text.strip().lower(), entity_type)
    if cache_key in _llm_canonical_cache:
        return _llm_canonical_cache[cache_key]

    context_line = f'\nSource sentence: "{context[:300]}"' if context else ""
    prompt = (
        f'Give the standard name used in biomedical ontologies for the entity below.\n'
        f'Entity: "{text}"\nType: {entity_type}{context_line}\n\n'
        f'Rules:\n'
        f'- Return only the name (e.g. "Drosophila melanogaster", "longevity", "superoxide dismutase 1")\n'
        f'- Expand abbreviations and fix spelling; do not return an ID\n'
        f'- If it is vague or study-specific (e.g. "long-lived population", "treated group"), return NONE'
    )
    name = ""
    try:
        resp = _get_llm_client().chat.completions.create(
            model=_LLM_MODEL, temperature=0.0, max_tokens=24,
            messages=[{"role": "user", "content": prompt}],
        )
        cand = (resp.choices[0].message.content or "").strip().strip('"').strip("'").strip()
        if cand and cand.upper() != "NONE" and ":" not in cand and len(cand.split()) <= 8:
            name = cand
    except Exception:
        pass
    _llm_canonical_cache[cache_key] = name
    return name


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


def _shares_no_tokens(query_text: str, matched_label: str) -> bool:
    """No shared tokens."""
    if not matched_label:
        return False
    q_tokens = _tokens(query_text)
    if len(q_tokens) < 2:
        return False
    m_tokens = _tokens(matched_label)
    if not m_tokens:
        return False
    return len(q_tokens & m_tokens) == 0


def _is_confidently_wrong(query_text: str, matched_label: str, synonyms="") -> bool:
    """True if neither the label nor any synonym names the queried term."""
    if not matched_label:
        return False
    return match_quality(query_text, matched_label, synonyms) == NONE


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

# Common lay disease names → MeSH ID (local fast lookup, avoids OLS4 round-trip/timeout).
# IDs verified against live OLS4 (exact match, mesh ontology).
_DISEASE_LOCAL: dict = {
    "diabetes": ("D003920", "Diabetes Mellitus"),
    "obesity": ("D009765", "Obesity"),
    "malaria": ("D008288", "Malaria"),
    "cancer": ("D009369", "Neoplasms"),
    "alzheimer disease": ("D000544", "Alzheimer Disease"),
    "alzheimer's disease": ("D000544", "Alzheimer Disease"),
    "parkinson disease": ("D010300", "Parkinson Disease"),
    "parkinson's disease": ("D010300", "Parkinson Disease"),
    "asthma": ("D001249", "Asthma"),
    "hypertension": ("D006973", "Hypertension"),
    "depression": ("D003863", "Depression"),
    "epilepsy": ("D004827", "Epilepsy"),
    "influenza": ("D007251", "Influenza, Human"),
    "flu": ("D007251", "Influenza, Human"),
    "tuberculosis": ("D014376", "Tuberculosis"),
    "hiv": ("D015658", "HIV Infections"),
    "stroke": ("D020521", "Stroke"),
}

# Common/lay name → canonical scientific (binomial) name, lowercase-keyed.
_COMMON_TO_BINOMIAL: dict = {
    "human": "homo sapiens", "humans": "homo sapiens",
    "mouse": "mus musculus", "mice": "mus musculus",
    "rat": "rattus norvegicus", "rats": "rattus norvegicus",
    "fly": "drosophila melanogaster", "drosophila": "drosophila melanogaster",
    "worm": "caenorhabditis elegans", "c. elegans": "caenorhabditis elegans",
    "yeast": "saccharomyces cerevisiae",
    "zebrafish": "danio rerio",
    "dog": "canis lupus familiaris",
}

# Separators used when a single species field states multiple organisms.
_SPECIES_SPLIT_RE = re.compile(r"\s*(?:,|;|&|\band\b)\s*", re.IGNORECASE)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")


def _ensembl_species_slug(species: str) -> str:
    """Convert a species name to Ensembl's URL slug format (e.g. 'Mus musculus' → 'mus_musculus')."""
    species = (species or "").strip()
    if not species:
        return "human"
    lower = species.lower()
    if lower in _TAXON_LOCAL:
        lower = _COMMON_TO_BINOMIAL.get(lower, lower)
    return re.sub(r"\s+", "_", lower)


def normalize_species(raw: str) -> str:
    """Canonicalize a species field: fix common-name/case variants via
    _COMMON_TO_BINOMIAL, split combined multi-organism values, dedupe, and
    rejoin with ", " in Title Case. Unknown terms are Title-Cased as-is
    rather than guessed at."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in _SPECIES_SPLIT_RE.split(raw) if p.strip()]
    seen, out = set(), []
    for p in parts:
        canon  = _COMMON_TO_BINOMIAL.get(p.lower(), p.lower())
        words  = canon.split()
        cased  = " ".join([words[0].capitalize()] + [w.lower() for w in words[1:]]) if words else canon
        if cased.lower() not in seen:
            seen.add(cased.lower())
            out.append(cased)
    return ", ".join(out)


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


# Qualifiers the core-term fallback may drop ("PICALM loci" → PICALM).
_CORE_QUALIFIERS = {
    "locus", "loci", "gene", "mutation", "mutant", "variant", "allele", "polymorphism", "deficiency",
    "knockout", "transcript", "expression", "level", "activity", "signaling", "signalling", "isoform",
    "homolog", "ortholog", "family", "subunit", "product", "region", "domain", "site",
}


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
        if not all(t in _CORE_QUALIFIERS for t in tokens(" ".join(words[n_words:]))):
            break                               # head noun
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
    e0 = _net_errors()
    try:
        iri = template.format(bare=bare)
        encoded = quote(quote(iri, safe=""), safe="")
        r = _http_get(f"{_OLS_BASE}/ontologies/{prefix.lower()}/terms/{encoded}",
                         headers={"Accept": "application/json"}, timeout=timeout)
        doc = r.json()
        label = doc.get("label", "") or ""
        if isinstance(label, list):
            label = label[0] if label else ""
    except Exception as e:
        logger.warning("External ID label lookup failed for %r: %s", canonical_id, e)

    if _net_errors() == e0:
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
    e0 = _net_errors()
    result = _ols4_search_uncached(text, ontologies, timeout)
    if _net_errors() == e0:
        _ols4_cache[cache_key] = result
    return result


_candidates_cache: dict = {}
_pick_cache: dict = {}


def _ols4_candidates(text: str, ontologies: list, rows: int = 10) -> list:
    """Top OLS4 hits for a term (no label check): [(id, label, prefix, synonyms, iri)]."""
    key = (text.lower().strip(), tuple(sorted(ontologies)))
    if key in _candidates_cache:
        return _candidates_cache[key]
    e0, out = _net_errors(), []
    params = {"q": text, "rows": rows, "exact": "false", "fieldList": "id,obo_id,label,ontology_prefix,synonym,iri"}
    if ontologies:
        params["ontology"] = ",".join(ontologies)
    try:
        docs = _http_get(f"{_OLS_BASE}/search", params=params, headers={"Accept": "application/json"},
                         timeout=_TIMEOUT).json().get("response", {}).get("docs", [])
        for d in docs:
            first = lambda v: (v[0] if v else "") if isinstance(v, list) else (v or "")
            oid, label = first(d.get("obo_id") or d.get("id")), first(d.get("label"))
            oid = oid.replace("_", ":", 1) if "_" in oid and ":" not in oid else oid
            syns = d.get("synonym") or []
            syns = [syns] if isinstance(syns, str) else syns
            if oid and ":" in oid and label and not _malformed_id(oid):
                out.append((oid, _uninvert_mesh_label(label, first(d.get("ontology_prefix"))),
                            first(d.get("ontology_prefix")), "; ".join(syns), first(d.get("iri"))))
    except Exception as e:
        logger.warning("OLS4 candidate search failed for %r: %s", text, e)
    if _net_errors() == e0:
        _candidates_cache[key] = out
    return out


def _llm_pick(text: str, entity_type: str, context: str, cands: list) -> Optional[int]:
    """LLM picks a database candidate (or none)."""
    if not cands:
        return None
    key = (text.lower().strip(), entity_type, tuple(c[0] for c in cands))
    if key in _pick_cache:
        return _pick_cache[key]
    listing = "\n".join(f"{i}. {c[1]}" + (f" (also: {c[3][:120]})" if c[3] else "") for i, c in enumerate(cands, 1))
    context_line = f'\nSentence: "{context[:400]}"' if context else ""
    prompt = (
        f'Term: "{text}" (type: {entity_type}){context_line}\n\n'
        f'Ontology candidates:\n{listing}\n\n'
        'Which candidate denotes exactly the same concept as the term as used here? '
        'A broader, narrower or related concept does not count; neither does a different organism\'s '
        'gene or a different disease. If several fit, pick the lowest number. Answer with the number only, or NONE.'
    )
    pick = None
    try:
        resp = _get_llm_client().chat.completions.create(
            model=_LLM_MODEL, temperature=0.0, max_tokens=8,
            messages=[{"role": "user", "content": prompt}])
        m = re.search(r"\d+", resp.choices[0].message.content or "")
        if m and 1 <= int(m.group()) <= len(cands):
            pick = int(m.group()) - 1
        _pick_cache[key] = pick
    except Exception as e:
        logger.warning("Candidate check failed for %r: %s", text, e)
    return pick


def _verified_candidate(text: str, entity_type: str, context: str, ontologies: list,
                        species: str = "") -> Optional[dict]:
    """Loose OLS4 search, confirmed by the LLM."""
    core = set(tokens(text)) - _GENERIC_WORDS - _FILLER_WORDS
    if not core:
        return None
    seen, cands = set(), []
    for onto in ([ontologies] if ontologies else []) + [[]]:
        for c in _ols4_candidates(text, onto):
            words = set(tokens(c[1])) | {t for syn in c[3].split("; ") for t in tokens(syn)}
            if c[0] not in seen and core & words and not _wrong_organism(c[1], species):
                seen.add(c[0]); cands.append(c)
    cands = sorted(cands, key=lambda c: _ontology_rank(c[2]))[:8]     # core ontologies first
    i = _llm_pick(text, entity_type, context, cands) if cands else None
    if i is None or not 0 <= i < len(cands):
        return None
    cid, label, prefix, syns, iri = cands[i]
    return {"canonical_id": cid, "canonical_name": label, "id_source": "ols4", "ontology_prefix": prefix,
            "needs_review": False, "synonyms": syns, "iri": iri, "verified_by": "candidate_check"}


def _confirm_close(text: str, entity_type: str, context: str, cid: str, label: str, syns) -> bool:
    """A close (not exact) match is kept only if the LLM confirms it means the term here."""
    syns = syns if isinstance(syns, str) else "; ".join(syns or [])
    return _llm_pick(text, entity_type, context, [(cid, label, "", syns, "")]) == 0


# Preferred ontologies when several match.
_ONTOLOGY_RANK = {p: i for i, p in enumerate((
    "mondo", "hp", "go", "mesh", "chebi", "uberon", "cl", "ncbitaxon", "pr", "so", "doid", "ncit", "efo",
    "pato", "mp", "oba", "apo", "fbcv", "fbbt", "wbphenotype", "wbbt", "pw", "clo", "bto", "hgnc", "ordo",
))}


def _ontology_rank(prefix: str) -> int:
    return _ONTOLOGY_RANK.get((prefix or "").lower(), len(_ONTOLOGY_RANK))


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

                # One short retry for transient network failures before giving up on
                # this ontology/exact combination — timeouts were a real observed
                # cause of otherwise-resolvable terms falling through to the LLM.
                try:
                    r = _http_get(f"{_OLS_BASE}/search", params=params,
                                     headers={"Accept": "application/json"}, timeout=timeout)
                except requests.exceptions.RequestException:
                    import time as _time
                    _time.sleep(0.5)
                    r = _http_get(f"{_OLS_BASE}/search", params=params,
                                     headers={"Accept": "application/json"}, timeout=timeout)
                docs = r.json().get("response", {}).get("docs", [])
                exact_hits = []
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
                    if not (obo_id and ":" in obo_id) or _malformed_id(obo_id):
                        continue

                    label_clean = _uninvert_mesh_label(label, prefix)
                    label_norm  = label.strip().lower()
                    syn_norms   = {s.strip().lower() for s in syns if s}
                    # Label/synonym must name the term
                    quality = match_quality(text, label_clean, syns)
                    if quality == NONE and label_clean != label:
                        quality = match_quality(text, label, syns)
                    is_true_exact = (label_norm == query_norm or query_norm in syn_norms
                                     or quality == EXACT)

                    if is_true_exact:
                        exact_hits.append((obo_id, label_clean, prefix, True, synonyms, iri))
                    elif quality != NONE and (fallback is None or _ontology_rank(prefix) < _ontology_rank(fallback[2])):
                        fallback = (obo_id, label_clean, prefix, False, synonyms, iri)
                if exact_hits:
                    return min(exact_hits, key=lambda h: _ontology_rank(h[2]))
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
                r = _http_get(
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
        if species.lower() in _HUMAN:               # Ensembl times out on well-studied genes; HGNC does not
            for candidate in candidates:
                hit = _hgnc_gene(candidate)
                if hit:
                    return hit
    return None


_HUMAN = {"human", "homo_sapiens", "homo sapiens"}


def _hgnc_gene(symbol: str, timeout=_TIMEOUT) -> Optional[tuple]:
    """HGNC REST — (ENSEMBL id, approved symbol, '') for a human gene symbol, previous or alias symbol."""
    for field in ("symbol", "prev_symbol", "alias_symbol"):
        try:
            r = _http_get(f"https://rest.genenames.org/fetch/{field}/{quote(symbol, safe='')}",
                          headers={"Accept": "application/json"}, timeout=timeout)
            docs = r.json().get("response", {}).get("docs", [])
        except Exception as e:
            logger.warning("HGNC lookup failed for %r: %s", symbol, e)
            return None
        if len(docs) == 1 and docs[0].get("ensembl_gene_id"):      # an alias shared by genes is ambiguous
            return (f"ENSEMBL:{docs[0]['ensembl_gene_id']}", docs[0].get("symbol") or symbol, "")
    return None


def _ensembl_display_name(ensembl_id: str, timeout: int = _TIMEOUT, retries: int = 2) -> Optional[str]:
    """Cached lookup of Ensembl's own canonical gene symbol (display_name) for an ID."""
    if ensembl_id in _ensembl_label_cache:
        return _ensembl_label_cache[ensembl_id]
    label = None
    last_err = None
    e0 = _net_errors()
    for attempt in range(1, retries + 1):
        try:
            r = _http_get(
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
    if _net_errors() == e0:
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
            r = _http_get(f"{_NCBI_BASE}/esearch.fcgi",
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
        r = _http_get(f"{_NCBI_BASE}/esummary.fcgi",
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
    e0 = _net_errors()
    try:
        r = _http_get(f"https://mygene.info/v3/gene/{ncbi_gene_id}",
                          params={"fields": "ensembl.gene"}, timeout=timeout)
        if r.ok:
            data = r.json()
            ens = data.get("ensembl", {})
            if isinstance(ens, list):   # some genes have multiple Ensembl IDs
                ens = ens[0] if ens else {}
            ensembl_id = ens.get("gene", "") or ""
    except Exception as e:
        logger.warning("NCBI Gene → Ensembl cross-reference failed for %r: %s", ncbi_gene_id, e)
    if _net_errors() == e0:
        _ncbi_to_ensembl_cache[ncbi_gene_id] = ensembl_id
    return ensembl_id


def _pubchem_search(text: str, timeout: int = _TIMEOUT) -> Optional[tuple]:
    """PubChem REST API for small molecules not in ChEBI. Returns (canonical_id, name)."""
    try:
        r = _http_get(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{text}/cids/JSON",
            timeout=timeout)
        cids = r.json().get("IdentifierList", {}).get("CID", [])
        if not cids:
            return None
        cid = cids[0]
        name = ""
        try:
            r2 = _http_get(
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
        r = _http_get(
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
    e0 = _net_errors()
    try:
        r = _http_get(
            f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/property.json",
            params={"propName": "RxNorm Name"}, timeout=timeout,
        )
        concepts = r.json().get("propConceptGroup", {}).get("propConcept", [])
        if concepts:
            name = concepts[0].get("propValue", "") or ""
    except Exception as e:
        logger.warning("RxNorm name lookup failed for RXCUI %r: %s", rxcui, e)
    if _net_errors() == e0:
        _rxnorm_name_cache[rxcui] = name
    return name


def _rxnorm_search(text: str, timeout: int = _TIMEOUT) -> Optional[tuple]:
    """RxNorm API — resolves drug names/brands to RxCUI. Returns (canonical_id, name, was_confident)."""
    try:
        r = _http_get(
            "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": text, "search": 1}, timeout=timeout,
        )
        rxcui_list = r.json().get("idGroup", {}).get("rxnormId", [])
        rxcui, was_exact = (rxcui_list[0], True) if rxcui_list else (None, False)

        if not rxcui:
            # Approximate match for misspellings / synonyms not in exact index
            r2 = _http_get(
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


_taxon_search_cache: dict = {}
_ABBREV_BINOMIAL_RE = re.compile(r"^([A-Z])\.\s*([a-z][a-z-]+)$")


def _ncbi_taxon_candidates(term: str, retmax: int = 5, timeout: int = _TIMEOUT) -> list:
    """NCBI Taxonomy eSearch + eSummary → [(taxid, scientific_name, common_name, rank)]."""
    try:
        r = _http_get(f"{_NCBI_BASE}/esearch.fcgi",
                         params={"db": "taxonomy", "term": term, "retmax": retmax, "retmode": "json"},
                         timeout=timeout)
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        r2 = _http_get(f"{_NCBI_BASE}/esummary.fcgi",
                          params={"db": "taxonomy", "id": ",".join(ids), "retmode": "json"},
                          timeout=timeout)
        res = r2.json().get("result", {})
        return [(i, res.get(i, {}).get("scientificname", ""), res.get(i, {}).get("commonname", ""),
                 res.get(i, {}).get("rank", "")) for i in ids if i in res]
    except Exception as e:
        logger.warning("NCBI Taxonomy lookup failed for %r: %s", term, e)
        return []


def _ncbi_taxon_spell(term: str, timeout: int = _TIMEOUT) -> str:
    """NCBI's own spelling suggestion for a taxonomy query ('' if none)."""
    try:
        r = _http_get(f"{_NCBI_BASE}/espell.fcgi", params={"db": "taxonomy", "term": term},
                         timeout=timeout)
        m = re.search(r"<CorrectedQuery>(.*?)</CorrectedQuery>", r.text or "")
        return (m.group(1).strip() if m else "")
    except Exception:
        return ""


def _ncbi_taxon_search(text: str) -> Optional[tuple]:
    """NCBI Taxonomy lookup with spelling and epithet retries."""
    key = text.strip().lower()
    if key in _taxon_search_cache:
        return _taxon_search_cache[key]
    e0 = _net_errors()

    def _pick(cands, query):
        best = None
        for tid, sci, common, rank in cands:
            q = match_quality(query, sci, [common] if common else [])
            if q == EXACT:
                return (f"NCBITaxon:{tid}", sci, False)
            if q != NONE and best is None:
                best = (f"NCBITaxon:{tid}", sci, True)
        return best

    result = _pick(_ncbi_taxon_candidates(text), text)

    if result is None:                       # typo → NCBI's spelling suggestion
        corrected = _ncbi_taxon_spell(text)
        if corrected and corrected.lower() != key:
            hit = _pick(_ncbi_taxon_candidates(corrected), corrected)
            if hit and match_quality(text, hit[1]) != NONE:
                result = (hit[0], hit[1], False)   # corrected spelling of the same name

    if result is None:                       # "D. melanogaster": search the epithet
        m = _ABBREV_BINOMIAL_RE.match(text.strip())
        if m:
            cands = [c for c in _ncbi_taxon_candidates(f"{m.group(2)}[All Names]", retmax=20)
                     if c[3] in ("species", "subspecies")]
            result = _pick(cands, text)

    if _net_errors() == e0:
        _taxon_search_cache[key] = result
    return result


def _local_taxon(text: str) -> Optional[str]:
    """Common-organism table lookup, tolerant of abbreviations and typos."""
    lower = text.lower().strip()
    if lower in _TAXON_LOCAL:
        return _TAXON_LOCAL[lower]
    for name, taxid in _TAXON_LOCAL.items():
        if " " in name and match_quality(text, name) != NONE:
            return taxid
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
        r = _http_get(
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
                    r2 = _http_get(
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
            return f"https://www.ensembl.org/id/{canonical_id}"
        if canonical_id.startswith("ENSEMBL:"):
            return f"https://www.ensembl.org/id/{canonical_id.split(':', 1)[1]}"
        if re.match(r"^P\d{5}$", canonical_id):
            return f"https://www.uniprot.org/uniprotkb/{canonical_id}/entry"
        if canonical_id.startswith("UniProtKB:"):
            return f"https://www.uniprot.org/uniprotkb/{canonical_id.split(':', 1)[1]}/entry"
        if canonical_id.startswith("NCBI_GENE:"):
            return f"https://www.ncbi.nlm.nih.gov/gene/{canonical_id.split(':', 1)[1]}"
        if canonical_id.startswith("PUBCHEM:"):
            return f"https://pubchem.ncbi.nlm.nih.gov/compound/{canonical_id.split(':', 1)[1]}"
        if canonical_id.startswith("RxNorm:"):
            return f"https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm={canonical_id.split(':', 1)[1]}"
        if ":" in canonical_id:
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

# ── Document-level concept consistency: one ID per term within a paper ──────
class _DocState:
    """Per-paper state: concept cache, genera, main species, paper-defined abbreviations, term locks."""
    def __init__(self):
        self.concepts: dict = {}
        self.genera: set = set()
        self.species: str = ""
        self.abbrevs: dict = {}
        self.locks: dict = {}
        self.locks_guard = threading.Lock()


# Each pipeline run (thread) has its own state; worker pools get it via in_document().
_doc_var: contextvars.ContextVar = contextvars.ContextVar("bsp_document_state", default=None)
_default_doc = _DocState()


def _doc() -> _DocState:
    return _doc_var.get() or _default_doc


def in_document(fn):
    """Wrap fn so worker threads use the calling run's document state."""
    state = _doc()

    def run(*args, **kwargs):
        token = _doc_var.set(state)
        try:
            return fn(*args, **kwargs)
        finally:
            _doc_var.reset(token)
    return run

# Families keep genes, proteins and organisms apart so a gene and its protein don't merge.

_TYPE_FAMILY: dict = {
    "GENE": "gene", "TRANSCRIPT": "gene", "EXON": "gene", "NON_CODING_RNA": "gene",
    "MICRO_RNA": "gene", "SI_RNA": "gene",
    "PROTEIN": "protein", "MACROMOLECULAR_COMPLEX": "protein",
    "GENOMIC_VARIANT": "variant", "SEQUENCE_VARIANT": "variant", "STRUCTURAL_VARIANT": "variant",
    "HAPLOTYPE": "variant", "GENOTYPE": "variant",
    "SMALL_MOLECULE": "chemical", "DRUG": "chemical", "CHEMICAL_EXPOSURE": "chemical",
    "ORGANISM": "organism", "ORGANISM_TAXON": "organism", "INDIVIDUAL_ORGANISM": "organism",
    "VIRUS": "organism",
    "CELL_LINE": "cell_line",
}


_GENUS_RE = re.compile(r"^([A-Z][a-z]{2,})\s+[a-z][a-z-]+")


def _concept_lock(ckey):
    """One lock per term so concurrent lookups of the same term run once."""
    d = _doc()
    with d.locks_guard:
        return d.locks.setdefault(ckey, threading.Lock())


_PAREN_SHORT = re.compile(r"\(\s*([^()\s][^()]{0,9}?)\s*\)")


def _long_form(short: str, before: str) -> Optional[str]:
    """Schwartz & Hearst (2003) long form for '(SF)'."""
    words = before.split()
    window = " ".join(words[-min(len(short) + 5, len(short) * 2):])
    s_i, l_i = len(short) - 1, len(window) - 1
    while s_i >= 0:
        c = short[s_i].lower()
        if not c.isalnum():
            s_i -= 1
            continue
        while l_i >= 0 and (window[l_i].lower() != c or (s_i == 0 and l_i > 0 and window[l_i - 1].isalnum())):
            l_i -= 1
        if l_i < 0:
            return None
        l_i -= 1
        s_i -= 1
    long = window[l_i + 1:].strip(" ,;:-")
    if len(long) <= len(short) or short.lower() in long.lower().split():
        return None
    return long


def find_abbreviations(text: str) -> dict:
    """Short forms the document defines as 'long form (SF)' → their long forms."""
    found: dict = {}
    for m in _PAREN_SHORT.finditer(text or ""):
        short = m.group(1).strip()
        if not (2 <= len(short) <= 10 and short[0].isalnum() and any(ch.isalpha() for ch in short)
                and len(short.split()) <= 2 and any(ch.isupper() for ch in short)):
            continue
        sentence_start = max(text.rfind(". ", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        long = _long_form(short, text[sentence_start:m.start()])
        if long:
            found.setdefault(short, long)
    return found


def _expand_document_abbreviations(text: str) -> str:
    """Expand abbreviations the paper defines."""
    abbrevs = _doc().abbrevs
    if not abbrevs or not text:
        return text
    for short, long in abbrevs.items():
        if short in text:
            text = re.sub(rf"(?<![\w-]){re.escape(short)}(?![\w-])", long, text)
    return text


def reset_document_cache() -> None:
    """Start fresh per-paper state for this run."""
    _doc_var.set(_DocState())


def _remember_genus(*names: str) -> None:
    for name in names:
        m = _GENUS_RE.match((name or "").strip())
        if m:
            _doc().genera.add(m.group(1))


def _expand_abbreviated_binomial(text: str) -> Optional[str]:
    """'D. simulans' → 'Drosophila simulans' if the species exists."""
    m = _ABBREV_BINOMIAL_RE.match(text.strip())
    if not m:
        return None
    initial, epithet = m.group(1), m.group(2)
    known = [n for n in _TAXON_LOCAL if " " in n and "." not in n
             and n.split()[0].startswith(initial.lower()) and n.split()[1] == epithet.lower()]
    if len(known) == 1:
        return known[0].capitalize()
    for pool in (_doc().genera,
                 {n.split()[0].capitalize() for n in _TAXON_LOCAL if " " in n and "." not in n}):
        fits = sorted(g for g in pool if g.startswith(initial))
        real = [f"{g} {epithet}" for g in fits if _ncbi_taxon_search(f"{g} {epithet}")]
        if len(real) == 1:
            return real[0]
    return None


def _concept_key(text: str, entity_type: str) -> Optional[tuple]:
    key = term_key(_clean_name(text or ""))
    if not key:
        return None
    return (_TYPE_FAMILY.get(entity_type, "concept"), key)


# Organism encoded in gene/protein IDs → genus.
_ID_GENUS = (("ENSEMBL:ENSG", "homo"), ("ENSEMBL:ENSMUSG", "mus"), ("ENSEMBL:ENSRNOG", "rattus"),
             ("ENSEMBL:ENSDARG", "danio"), ("ENSEMBL:FBgn", "drosophila"), ("FB:FBgn", "drosophila"),
             ("ENSEMBL:WBGene", "caenorhabditis"), ("WB:WBGene", "caenorhabditis"), ("hgnc:", "homo"),
             ("HGNC:", "homo"), ("MGI:", "mus"), ("RGD:", "rattus"), ("ZFIN:", "danio"), ("SGD:", "saccharomyces"))


_GENE_SOURCES = {"ensembl", "ncbi_gene", "pubtator3"}
_MOLECULAR = {"gene", "protein", "variant"}


def _kind_conflict(entity_type: str, cid: str) -> bool:
    """ID is a different kind of thing (gene given a ChEBI ID)."""
    t = _TYPE_FAMILY.get(entity_type)
    p = _PREFIX_FAMILY.get(_ontology_prefix_of(cid)) if cid and ":" in cid else None
    if not t or not p or t not in _MOLECULAR | {"chemical", "organism"}:
        return False
    group = lambda f: "molecular" if f in _MOLECULAR else f
    return group(t) != group(p)


def _organism_conflict(cid: str, species: str) -> bool:
    """A gene/protein ID from another organism than the one the relation names."""
    genus = (species or "").strip().split(" ")[0].lower()
    id_genus = next((g for prefix, g in _ID_GENUS if (cid or "").startswith(prefix)), "")
    return bool(genus and id_genus and genus != id_genus)


def normalize_entity(
    text: str,
    entity_type: str,
    existing_id: Optional[str] = None,
    species: str = "",
    context: str = "",
) -> dict:
    """Normalize one entity to a canonical ID (dict with canonical_id, canonical_name, id_source, ...)."""
    result = _normalize_entity_cached(text, entity_type, existing_id, species or _doc().species, context)
    fam = _TYPE_FAMILY.get(entity_type)
    if result.get("network_degraded") and fam == "gene" and result.get("id_source") not in _GENE_SOURCES:
        # Gene sources failed on the network: retry later, no guessing
        return {"canonical_id": f"TEXT:{_slug(text)}", "canonical_name": "", "id_source": "fuzzy",
                "ontology_prefix": "", "needs_review": True, "source_url": "", "network_degraded": True}
    if _kind_conflict(entity_type, result.get("canonical_id", "")):
        logger.info("Rejected %s for %r: not a %s", result["canonical_id"], text, entity_type)
        return {"canonical_id": f"TEXT:{_slug(text)}", "canonical_name": "", "id_source": "fuzzy",
                "ontology_prefix": "", "needs_review": True, "source_url": "", "kind_mismatch": result["canonical_id"]}
    if species and _organism_conflict(result.get("canonical_id", ""), species):
        logger.info("Rejected %s for %r: not a %s gene", result["canonical_id"], text, species)
        return {"canonical_id": f"TEXT:{_slug(text)}", "canonical_name": "", "id_source": "fuzzy",
                "ontology_prefix": "", "needs_review": True, "source_url": "",
                "organism_mismatch": result["canonical_id"]}
    return result


def _normalize_entity_cached(text: str, entity_type: str, existing_id: Optional[str], species: str,
                             context: str) -> dict:
    ambiguous_abbreviation = False
    expanded_doc = _expand_document_abbreviations(text)
    if expanded_doc != text:
        text = expanded_doc
    if _TYPE_FAMILY.get(entity_type) == "organism" and _ABBREV_BINOMIAL_RE.match((text or "").strip()):
        expanded = _expand_abbreviated_binomial(text)
        if expanded:
            text = expanded
        else:
            ambiguous_abbreviation = True

    ckey = _concept_key(text, entity_type)
    has_trusted_id = bool(existing_id) and _is_canonical_id(existing_id)
    with _concept_lock(ckey):            # one lookup per term
        concepts = _doc().concepts
        if ckey and not has_trusted_id and ckey in concepts:
            return dict(concepts[ckey])

        _net_state.errors = 0
        result = _normalize_entity_core(text, entity_type, existing_id, species, context)
        result["source_url"] = _build_source_url(
            result["canonical_id"], result["id_source"], result.get("ontology_prefix", ""),
            result.get("iri", ""), result.get("ensembl_species", "")
        )
        if ambiguous_abbreviation:
            result["needs_review"] = True
        if getattr(_net_state, "errors", 0):
            result["network_degraded"] = True      # retried later
        # Remember only real, network-clean resolutions so later mentions reuse them.
        cid = result.get("canonical_id", "")
        if (ckey and cid and not cid.startswith("TEXT:") and cid != "NEEDS_REVIEW"
                and not result.get("network_degraded")):
            concepts.setdefault(ckey, dict(result))
            if _TYPE_FAMILY.get(entity_type) == "organism" and not ambiguous_abbreviation:
                _remember_genus(text, result.get("canonical_name", ""))
        return result


def _normalize_entity_core(
    text: str,
    entity_type: str,
    existing_id: Optional[str] = None,
    species: str = "",
    context: str = "",
    allow_llm_name: bool = True,
) -> dict:
    """Core normalization logic. Returns dict with canonical_id, canonical_name, id_source, ontology_prefix, needs_review."""
    if not text or not text.strip():
        return {"canonical_id": "NEEDS_REVIEW", "canonical_name": "", "id_source": "review", "ontology_prefix": "", "needs_review": True}

    # ── Priority 1: PubTator3 ID — validated against ontology label when available.
    if existing_id and _is_canonical_id(existing_id):
        ref_label = _external_id_label(existing_id)
        pubtator_risk = bool(ref_label) and _shares_no_tokens(text, ref_label)
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
        local_taxid = _local_taxon(text)
        if local_taxid:
            return {"canonical_id": f"NCBITaxon:{local_taxid}",
                    "canonical_name": "", "id_source": "ncbi_taxon", "ontology_prefix": "", "needs_review": False}

    # ── Priority 2b: Fast local lookup for common lay disease terms ──────────
    if entity_type == "DISEASE":
        lower = text.lower().strip()
        if lower in _DISEASE_LOCAL:
            mesh_id, mesh_label = _DISEASE_LOCAL[lower]
            return {"canonical_id": f"mesh:{mesh_id}", "canonical_name": mesh_label,
                    "id_source": "mesh_local", "ontology_prefix": "mesh", "needs_review": False}

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
            if not _is_confidently_wrong(text, ulabel, usynonyms):
                return {"canonical_id": uid, "canonical_name": ulabel, "id_source": "uniprot",
                        "ontology_prefix": uprefix, "needs_review": False, "synonyms": usynonyms}

    # ── Priority 5: OLS4 universal search ──────────────────────────────────────
    ontologies = _TYPE_TO_ONTOLOGIES.get(entity_type, [])
    if entity_type in ("GENE", "PROTEIN") and "hgnc" in ontologies \
            and _ensembl_species_slug(species) != "human":
        # Exclude HGNC for non-human species — it's human-only.
        ontologies = [o for o in ontologies if o != "hgnc"]
    ols = _ols4_search(text, ontologies)

    match_query = text
    if (not ols or not ols[3]) and context and len(text.split()) <= _CONTEXT_EXPAND_MAX_WORDS:
        expanded = _context_expand(text, entity_type, context)
        if expanded.strip().lower() != text.strip().lower() and _rewrite_fits(text, expanded):
            expanded_ols = _ols4_search(expanded, ontologies)
            if expanded_ols and expanded_ols[3]:
                ols = expanded_ols
                match_query = expanded

    # Zero results may mean a misspelling — retry with an LLM-corrected spelling.
    if not ols:
        corrected = _llm_spell_correct(text, entity_type, context)
        if corrected.strip().lower() != text.strip().lower() and _rewrite_fits(text, corrected):
            corrected_ols = _ols4_search(corrected, ontologies)
            if corrected_ols:
                ols = corrected_ols
                match_query = corrected

    if ols and _wrong_organism(ols[1], species):
        ols = None
    if ols:
        ols_id, ols_label, ols_prefix, ols_exact, ols_synonyms, ols_iri = ols
        # A label mismatch alone isn't confidently wrong if a synonym matches instead
        # (e.g. query "Hutchinson-Gilford syndrome" vs preferred label "Progeria").
        quality = match_quality(match_query, ols_label, ols_synonyms)
        # Close matches need LLM confirmation
        if quality == EXACT or ols_exact or (
                quality != NONE and _confirm_close(text, entity_type, context, ols_id, ols_label, ols_synonyms)):
            fuzzy_risk = False
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
        taxon = _ncbi_taxon_search(text)
        if taxon:
            tid, tname, t_review = taxon
            return {"canonical_id": tid, "canonical_name": tname, "id_source": "ncbi_taxon",
                    "ontology_prefix": "ncbitaxon", "needs_review": t_review}

    if entity_type == "SMALL_MOLECULE":
        pub = _pubchem_search(text)
        if pub:
            pub_id, pub_name = pub
            return {"canonical_id": pub_id, "canonical_name": pub_name, "id_source": "pubchem", "ontology_prefix": "", "needs_review": False}

    # ── Priority 9: OLS4 without ontology filter — always, not just typed ────
    ols_broad = _ols4_search(text, [])
    if ols_broad and _wrong_organism(ols_broad[1], species):
        ols_broad = None
    if ols_broad:
        ob_id, ob_label, ob_prefix, ob_exact, ob_synonyms, ob_iri = ols_broad
        ob_quality = match_quality(text, ob_label, ob_synonyms)
        if ob_quality == EXACT or ob_exact or (
                ob_quality != NONE and _confirm_close(text, entity_type, context, ob_id, ob_label, ob_synonyms)):
            fuzzy_risk = False
            return {"canonical_id": ob_id, "canonical_name": ob_label, "id_source": "ols4_broad",
                    "ontology_prefix": ob_prefix, "needs_review": fuzzy_risk, "synonyms": ob_synonyms,
                    "iri": ob_iri}

    # ── Priority 10: Composite decomposition — "PICALM loci" → PICALM ──────────
    core = _try_core_term(text, entity_type, species)
    if core and not _shares_no_tokens(text, core[1]) and not _wrong_organism(core[1], species):
        core_id, core_label, core_prefix, core_exact = core
        if core_exact or _confirm_close(text, entity_type, context, core_id, core_label, ""):
            return {"canonical_id": core_id, "canonical_name": core_label, "id_source": "ols4_core",
                    "ontology_prefix": core_prefix, "needs_review": False}

    # ── Priority 10b: loose OLS4 candidates, one confirmed against the sentence ──
    verified = _verified_candidate(text, entity_type, context, ontologies, species)
    if verified:
        return verified

    # ── Priority 11: Wikidata — broad fallback covering anything not in above ──
    wd = _wikidata_search(text, entity_type)
    if wd:
        wd_id, wd_label = wd
        if not _is_confidently_wrong(text, wd_label):
            return {"canonical_id": wd_id, "canonical_name": "", "id_source": "wikidata", "ontology_prefix": "", "needs_review": False}

    # ── Priority 12: LLM-proposed name, database ID ──
    if allow_llm_name and entity_type in _TYPE_TO_NS_HINT:
        name = _llm_standard_name(text, entity_type, context)
        if name and term_key(name) != term_key(text):
            res = _normalize_entity_core(name, entity_type, None, species, "", allow_llm_name=False)
            if (not res["canonical_id"].startswith("TEXT:") and res["canonical_id"] != "NEEDS_REVIEW"
                    and _proposed_name_fits(text, name, res)):
                res["proposed_name"] = name          # audit: the name the database matched
                return res

    # ── Priority 13: TEXT:slug — last resort, never bare text ────────────────
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
    e0 = _net_errors()
    try:
        resp = _http_get(f"https://id.nlm.nih.gov/mesh/{mesh_id}.json", timeout=timeout)
        if resp.ok:
            data = resp.json()
            raw = data.get("treeNumber") or []
            if isinstance(raw, str):        # single tree number
                raw = [raw]
            trees = [t.rsplit("/", 1)[-1] for t in raw]
    except Exception as e:
        logger.warning("MeSH tree lookup failed for %r: %s", mesh_id, e)
    if _net_errors() == e0:
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


# Disease-like entity types.
_DISEASE_LIKE_TYPES = {"DISEASE", "CANCER", "DISEASE_OUTCOME", "PATHOLOGICAL_PROCESS",
                       "CLINICAL_FINDING", "SYMPTOM", "PHENOTYPE"}
_ORGANISM_TYPES = {"ORGANISM", "ORGANISM_TAXON", "INDIVIDUAL_ORGANISM", "VIRUS", "POPULATION"}


# Ontologies typed by term namespace.
_MIXED_VOCAB_PREFIXES = {"fbcv"}
_NAMESPACE_TYPE = {
    "biological_process": "BIOLOGICAL_PROCESS", "molecular_function": "MOLECULAR_FUNCTION",
    "cellular_component": "CELLULAR_COMPONENT", "phenotypic_class": "PHENOTYPE", "dpo": "PHENOTYPE",
}
_NAMESPACE_COMPATIBLE = {
    "BIOLOGICAL_PROCESS": {"BIOLOGICAL_PROCESS", "PHYSIOLOGICAL_PROCESS", "PATHOLOGICAL_PROCESS", "PATHWAY", "REACTION"},
    "MOLECULAR_FUNCTION": {"MOLECULAR_FUNCTION", "MOLECULAR_INTERACTION"},
    "CELLULAR_COMPONENT": {"CELLULAR_COMPONENT", "MACROMOLECULAR_COMPLEX"},
    "PHENOTYPE": _DISEASE_LIKE_TYPES,
}
_POPULATION_WORDS = re.compile(r"\b(population|strain|stock|cohort)s?\b", re.I)
_ols_term_cache: dict = {}


def _ols_term_info(canonical_id: str, timeout: int = _TIMEOUT) -> tuple:
    """(namespace, label) of an OBO term from OLS4, cached; ('', '') if unavailable."""
    if canonical_id in _ols_term_cache:
        return _ols_term_cache[canonical_id]
    prefix, local = canonical_id.split(":", 1)
    iri = quote(quote(f"http://purl.obolibrary.org/obo/{prefix}_{local}", safe=""), safe="")
    for attempt in (1, 2):
        try:
            r = _http_get(f"{_OLS_BASE}/ontologies/{prefix.lower()}/terms/{iri}", timeout=timeout)
            if not r.ok:
                break
            d = r.json()
            ns = (d.get("annotation") or {}).get("has_obo_namespace") or [d.get("obo_namespace") or ""]
            info = ((ns[0] if isinstance(ns, list) and ns else str(ns or "")).strip().lower(), d.get("label") or "")
            _ols_term_cache[canonical_id] = info
            return info
        except Exception as e:
            if attempt == 2:
                logger.warning("OLS term lookup failed for %r: %s", canonical_id, e)
    return ("", "")             # not cached — a later mention can retry


def _type_from_namespace(declared_type: str, canonical_id: str, prefix: str) -> str:
    """Type from the GO/FBcv term namespace."""
    namespace, label = _ols_term_info(canonical_id)
    ns_type = _NAMESPACE_TYPE.get(namespace)
    if ns_type:
        return declared_type if declared_type in _NAMESPACE_COMPATIBLE.get(ns_type, {ns_type}) else ns_type
    if prefix in _MIXED_VOCAB_PREFIXES and namespace:
        if _POPULATION_WORDS.search(label):
            return "POPULATION"
        return "OTHER"          # not a phenotype or cell type — leave to human review
    return declared_type


def _ontology_prefix_of(canonical_id: str, ontology_prefix: str = "") -> str:
    if ontology_prefix:
        return ontology_prefix.lower()
    return canonical_id.split(":", 1)[0].lower() if ":" in canonical_id else ""


def _correct_type_from_ontology(declared_type: str, canonical_id: str, ontology_prefix: str = "") -> str:
    """Ontology type overrides a contradicting LLM type."""
    if not canonical_id or canonical_id.startswith("TEXT:") or canonical_id == "NEEDS_REVIEW":
        return declared_type
    prefix = _ontology_prefix_of(canonical_id, ontology_prefix)

    if prefix == "ncbitaxon":
        return declared_type if declared_type in _ORGANISM_TYPES else "ORGANISM"
    if prefix in ("mondo", "doid"):
        return declared_type if declared_type in _DISEASE_LIKE_TYPES else "DISEASE"
    if prefix in ("hp", "mp"):
        return declared_type if declared_type in _DISEASE_LIKE_TYPES else "PHENOTYPE"
    if prefix == "go" or prefix in _MIXED_VOCAB_PREFIXES:
        return _type_from_namespace(declared_type, canonical_id, prefix)
    if prefix == "mesh":
        trees = _mesh_tree_numbers(canonical_id.split(":", 1)[1])
        if not trees:
            return declared_type
        is_disease = any(t.startswith("C") for t in trees)
        is_process = all(t.startswith("G") for t in trees)
        if is_disease and declared_type not in _DISEASE_LIKE_TYPES:
            return "DISEASE"
        if is_process and declared_type in _DISEASE_LIKE_TYPES:
            return "BIOLOGICAL_PROCESS"
    return declared_type


# ── Non-canonical entities ──
_GENERIC_WORDS = {
    "gene", "protein", "mutant", "mutation", "allele", "variant", "transcript", "mrna", "rna", "dna",
    "snp", "qtl", "locus", "genome", "site", "product", "sequence", "element", "region", "factor",
    "molecule", "compound", "substance", "cell", "tissue", "organism", "individual", "sample",
    "function", "functional", "character", "trait", "mechanism", "process", "system", "level", "type",
    "form", "structure", "component", "group", "population", "line", "strain", "stock", "cohort",
}
_FILLER_WORDS = {
    "several", "many", "these", "those", "some", "all", "other", "various", "different", "specific",
    "particular", "certain", "multiple", "numerous", "key", "major", "minor", "novel", "new",
    "candidate", "genomic", "genetic", "molecular", "physiological", "biological", "relevant",
}
_GROUP_HEADS = {
    "population", "line", "strain", "stock", "group", "cohort", "colony", "culture", "fly", "animal",
    "individual", "subject", "mutant", "allele", "gene", "character", "sample", "replicate",
    "generation", "treatment", "control",
}


_GROUP_MODIFIERS = {"control", "treated", "untreated", "experimental", "selected", "unselected", "sham"}
_SUBJECT_NOUNS = {"rodent", "mouse", "rat", "fly", "worm", "patient", "female", "male", "larva", "organism",
                  "cell", "plant", "participant", "volunteer", "nematode", "beetle", "fish"}


def _non_canonical_reason(text: str, canonical_id: str, canonical_name: str = "") -> str:
    """Category word or study group reason, else ''."""
    toks = [t for t in tokens(text) if not t.isdigit()]
    if (len(toks) >= 2 and toks[0] in _GROUP_MODIFIERS       # "control rodents"
            and toks[-1] in _GROUP_HEADS | _SUBJECT_NOUNS):
        return "study-specific group label"
    core = [t for t in toks if t not in _FILLER_WORDS]
    if toks and all(t in _GENERIC_WORDS for t in core):
        return "generic category term"
    unresolved = not canonical_id or canonical_id.startswith("TEXT:") or canonical_id == "NEEDS_REVIEW"
    # Ignored short label ("O populations" → Population)
    label_toks = set(tokens(canonical_name)) if canonical_name else set()
    long_core = [t for t in core if len(t) > 2 or t in label_toks]
    if not unresolved and label_toks and long_core and len(long_core) < len(core) \
            and all(t in _GENERIC_WORDS for t in long_core):
        return "generic category term"
    if unresolved and toks and toks[-1] in _GROUP_HEADS:
        return "study-specific group label"
    return ""


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
    subject_type = _correct_type_from_ontology(subject_type, subj_norm["canonical_id"],
                                               subj_norm.get("ontology_prefix", ""))
    object_type  = _correct_type_from_ontology(object_type, obj_norm["canonical_id"],
                                               obj_norm.get("ontology_prefix", ""))

    subj_nc = _non_canonical_reason(subject_name, subj_norm["canonical_id"], subj_norm.get("canonical_name", ""))
    obj_nc  = _non_canonical_reason(object_name, obj_norm["canonical_id"], obj_norm.get("canonical_name", ""))

    review_reason = ""
    if subj_norm["needs_review"]:
        review_reason += f"subject '{subject_name}' not normalized; "
    if obj_norm["needs_review"]:
        review_reason += f"object '{object_name}' not normalized"

    return {
        **record,
        "species":            normalize_species(species),
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
        "subject_non_canonical": subj_nc,
        "object_non_canonical":  obj_nc,
        "subject_network_degraded": bool(subj_norm.get("network_degraded")),
        "object_network_degraded":  bool(obj_norm.get("network_degraded")),
    }


def _resolved(cid: str) -> bool:
    return bool(cid) and not cid.startswith("TEXT:") and cid != "NEEDS_REVIEW"


# What an ID is, judged by its database — more reliable than the extracted entity type.
_PREFIX_FAMILY = {
    "ensembl": "gene", "hgnc": "gene", "ncbi_gene": "gene", "fb": "gene", "wb": "gene",
    "chebi": "chemical", "pubchem": "chemical", "hmdb": "chemical", "rxnorm": "chemical",
    "uniprotkb": "protein", "uniprot": "protein", "pr": "protein", "ncbitaxon": "organism",
}


def merge_synonym_concepts(records: list) -> list:
    """Merge concepts the databases list as synonyms."""
    fields = ("id", "canonical_name", "id_source", "source_url", "synonyms", "type")
    info: dict = {}
    for r in records:
        for side in ("subject", "object"):
            cid = r.get(f"{side}_id", "")
            if _resolved(cid) and cid not in info:
                syns = r.get(f"{side}_synonyms") or ""
                syns = syns.split("; ") if isinstance(syns, str) else list(syns)
                prefix = _ontology_prefix_of(cid)
                info[cid] = {"fam": _PREFIX_FAMILY.get(prefix) or _TYPE_FAMILY.get(r.get(f"{side}_type"), "concept"),
                             "prefix": prefix,
                             "label": r.get(f"{side}_canonical_name") or "",
                             "syns": [x for x in syns if x],
                             "rank": _ontology_rank(_ontology_prefix_of(cid)),
                             "rec": {f: r.get(f"{side}_{f}") for f in fields}}
    target: dict = {}
    for a, ia in info.items():
        for b, ib in info.items():
            if (a == b or ia["prefix"] == ib["prefix"] or ia["fam"] != ib["fam"] or not ia["label"]
                    or match_quality(ia["label"], ib["label"], ib["syns"]) != EXACT):
                continue                                       # reorderings like "control rodents" aren't enough
            keep, drop = (a, b) if ia["rank"] < ib["rank"] else (b, a)
            target.setdefault(drop, keep)
    for a in list(target):                                         # follow chains
        seen = {a}
        while target[a] in target and target[a] not in seen:
            seen.add(target[a]); target[a] = target[target[a]]
    if not target:
        return records
    for r in records:
        for side in ("subject", "object"):
            b = target.get(r.get(f"{side}_id", ""))
            if b:
                for f, v in info[b]["rec"].items():
                    r[f"{side}_{f}"] = v
    logger.info("Merged %d synonym concept(s): %s", len(target), target)
    return records


def normalize_records_parallel(records: list, chunk_for, workers: int = 8, progress=None) -> list:
    """Normalize records concurrently, then retry once the ones that hit network errors.

    Abbreviated organism names run after full names so the paper's genera are known.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    abbrev = lambda r: any(_ABBREV_BINOMIAL_RE.match(str(r.get(f"{s}_name", "")).strip())
                           for s in ("subject", "object"))
    out: list = [None] * len(records)
    done = [0]
    named = collections.Counter(r.get("species") for r in records if r.get("species"))
    if named:
        _doc().species = named.most_common(1)[0][0]
    for chunk_text in {r.get("_chunk_text", "") for r in records}:
        for short, long in find_abbreviations(chunk_text).items():
            _doc().abbrevs.setdefault(short, long)
    if _doc().abbrevs:
        logger.info("Abbreviations defined in this paper: %s", _doc().abbrevs)

    def run(indices):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(in_document(normalize_record), records[i], chunk_for(records[i])): i for i in indices}
            for f in as_completed(futs):
                out[futs[f]] = f.result()
                done[0] += 1
                if progress:
                    progress(done[0], len(records))

    run([i for i, r in enumerate(records) if not abbrev(r)])
    run([i for i, r in enumerate(records) if abbrev(r)])

    retry = [i for i, r in enumerate(out)
             if r.get("subject_network_degraded") or r.get("object_network_degraded")]
    if retry:
        logger.info("Retrying %d record(s) whose lookups hit network errors", len(retry))
        reset_network_state()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, res in zip(retry, ex.map(in_document(lambda i: normalize_record(records[i], chunk_for(records[i]))), retry)):
                out[i] = res
    return merge_synonym_concepts(out)
