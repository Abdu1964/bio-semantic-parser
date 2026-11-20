#!/usr/bin/env python3
"""Check every KG triple against its paper: supporting sentence, reasoning and status
(CONFIRMED / UNVERIFIABLE / MISMATCH), plus precision, recall and F1.
Writes verification_report.html and .json. Run: python tools/verify_kg.py [--fetch]"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import requests

_DB_PATH      = Path("data/triple_store.db")          # legacy fallback
_DB_NEO4J     = Path("data/triple_store_neo4j.db")
_DB_METTA     = Path("data/triple_store_metta.db")
# Default output paths go into the format-specific unified dir, not the root
_OUT_HTML     = Path("data/kg_output/unified_neo4j/verification_report.html")
_OUT_JSON     = Path("data/kg_output/unified_neo4j/verification_report.json")


def _db_for_format(fmt: str) -> Path:
    """Return the DB path for the given format, falling back to legacy."""
    if fmt == "neo4j":
        return _DB_NEO4J if _DB_NEO4J.exists() else _DB_PATH
    if fmt == "metta":
        return _DB_METTA if _DB_METTA.exists() else _DB_PATH
    # "both" or unspecified — prefer neo4j, fall back to legacy
    if _DB_NEO4J.exists(): return _DB_NEO4J
    if _DB_METTA.exists(): return _DB_METTA
    return _DB_PATH

# Cache for fetched paper texts {pmid/pmcid: text}
_TEXT_CACHE: dict = {}


# ── Text fetchers ─────────────────────────────────────────────────────────────

def _fetch_pubmed(doc_id: str) -> str:
    """Fetch abstract text from PubMed."""
    pmid = doc_id.replace("PMID:", "").strip()
    try:
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "xml", "rettype": "abstract"},
            timeout=15,
        )
        # Extract AbstractText
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)
        texts = [el.text or "" for el in root.iter("AbstractText")]
        return " ".join(t for t in texts if t)
    except Exception:
        return ""


def _fetch_pmc(doc_id: str) -> str:
    """Fetch full text from PMC, collecting all text including nested elements and tails."""
    pmcid = doc_id if doc_id.startswith("PMC") else f"PMC{doc_id}"
    try:
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pmc", "id": pmcid, "retmode": "xml"},
            timeout=30,
        )
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)

        # Skip reference list and metadata — only body + abstract
        skip_tags = {"ref-list", "element-citation", "back"}
        parts = []

        def _collect(el: ET.Element) -> None:
            if el.tag in skip_tags:
                return
            if el.text:
                parts.append(el.text)
            for child in el:
                _collect(child)
                if child.tail:
                    parts.append(child.tail)

        # Prefer body + abstract to avoid author list noise
        for section in ("body", "abstract", "front"):
            node = root.find(f".//{section}")
            if node is not None:
                _collect(node)

        text = " ".join(p.strip() for p in parts if p.strip())
        return text if text else ""
    except Exception:
        return ""


def _get_pdf_text_from_checkpoint(doc_id: str) -> str:
    """Text of a local PDF paper (SHA-256 or DOI ID) from its Layer 3 checkpoint."""
    import re as _re
    import json as _json
    from pathlib import Path as _Path

    ckpt_root = _Path("data/checkpoints")

    # Case 1: direct SHA256 hash
    if len(doc_id) == 64 and _re.match(r'^[0-9a-f]+$', doc_id):
        ckpt_file = ckpt_root / doc_id / "layer3_chunks.json"
        if ckpt_file.exists():
            try:
                chunks = _json.loads(ckpt_file.read_text(encoding="utf-8"))
                return " ".join(c.get("text", "") for c in chunks if c.get("text"))
            except Exception:
                pass
        return ""

    # Case 2: DOI (10.XXXX/...) or dataset ID — was originally a local PDF
    # The DOI extractor replaced the hash; search all PDF checkpoints for this ID
    if doc_id.startswith("10.") or "/" in doc_id:
        if not ckpt_root.exists():
            return ""
        for ckpt_file in ckpt_root.rglob("layer3_chunks.json"):
            try:
                chunks = _json.loads(ckpt_file.read_text(encoding="utf-8"))
                combined = " ".join(c.get("text", "") for c in chunks if c.get("text"))
                # Confirm this PDF contains the DOI/ID
                if doc_id.replace("/", "").replace(".", "")[:12].lower() in combined.lower().replace("/", "").replace(".", ""):
                    return combined
                # Also check for the actual string
                if doc_id[:15] in combined:
                    return combined
            except Exception:
                continue
        return ""

    return ""


def get_paper_text(doc_id: str) -> str:
    """Paper text, cached: local PDF checkpoint, then PMC full text, then PubMed full text or abstract."""
    if not doc_id:
        return ""
    if doc_id in _TEXT_CACHE:
        return _TEXT_CACHE[doc_id]

    text = ""

    # Local PDF (SHA-256 or DOI ID): text from the Layer 3 checkpoint
    import re as _re
    _is_hash = len(doc_id) == 64 and _re.match(r'^[0-9a-f]+$', doc_id)
    _is_doi  = doc_id.startswith("10.") and "/" in doc_id

    # Always try checkpoint first — covers hashes, DOIs, and title-style IDs
    # like "Alzheimer-PIIS0092867423009741" from local PDF uploads.
    _ckpt_direct = __import__('pathlib').Path("data/checkpoints") / doc_id / "layer3_chunks.json"
    if _ckpt_direct.exists():
        try:
            import json as _json_ckpt
            _chunks = _json_ckpt.loads(_ckpt_direct.read_text(encoding="utf-8"))
            text = " ".join(c.get("text", "") for c in _chunks if c.get("text"))
            if text:
                _TEXT_CACHE[doc_id] = text
                return text
        except Exception:
            pass

    if _is_hash or _is_doi:
        text = _get_pdf_text_from_checkpoint(doc_id)
        if text:
            _TEXT_CACHE[doc_id] = text
            return text
        if _is_hash:
            # No checkpoint → unverifiable
            _TEXT_CACHE[doc_id] = ""
            return ""
        # For DOIs, fall through to try network fetch

    if doc_id.startswith("PMC"):
        # Full text available
        text = _fetch_pmc(doc_id)
    else:
        pmid = doc_id.replace("PMID:", "").strip()
        if pmid.isdigit():
            # linkname=pubmed_pmc gives this paper's PMC version; without it NCBI returns related papers
            try:
                r = requests.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi",
                    params={
                        "dbfrom": "pubmed", "db": "pmc",
                        "id": pmid, "retmode": "json",
                        "linkname": "pubmed_pmc",
                    },
                    timeout=10,
                )
                data = r.json()
                link_sets = data.get("linksets", [{}])[0].get("linksetdbs", [])
                pmc_ids = []
                for ls in link_sets:
                    if ls.get("dbto") == "pmc":
                        pmc_ids = ls.get("links", [])
                        break
                if pmc_ids:
                    text = _fetch_pmc(f"PMC{pmc_ids[0]}")
            except Exception:
                pass
            # Fallback to abstract if PMC not available
            if not text:
                text = _fetch_pubmed(pmid)

    _TEXT_CACHE[doc_id] = text
    if text:
        time.sleep(0.3)  # NCBI rate limit
    return text


# ── Flexible entity matching ─────────────────────────────────────────────────

_NOISE_WORDS = {"the", "and", "for", "with", "from", "into", "that", "this",
                "are", "was", "were", "has", "had", "its", "their",
                "factor", "subunit", "protein", "receptor", "kinase"}

_STRIP_PREFIXES = (
    "mammalian ", "human ", "mouse ", "murine ", "yeast ",
    "bacterial ", "viral ", "canonical ", "mitochondria-located ",
)

_GREEK_WORDS = {
    "α": "alpha", "β": "beta",  "γ": "gamma", "δ": "delta",
    "ε": "epsilon","κ": "kappa", "λ": "lambda","μ": "mu",
    "ν": "nu",     "ω": "omega", "σ": "sigma", "τ": "tau",
    "φ": "phi",    "χ": "chi",   "ψ": "psi",   "ρ": "rho",
    "π": "pi",
}
_ASCII_TO_GREEK = {v: k for k, v in _GREEK_WORDS.items()}


def _normalize_greek(s: str) -> str:
    """Replace Greek letters with their spelled-out names (both directions)."""
    # Greek letter → spelled out: κ → kappa, ω → omega
    for ch, name in _GREEK_WORDS.items():
        s = s.replace(ch, name)
    return s


def _ascii_to_greek_norm(s: str) -> str:
    """Replace spelled-out Greek names with the letter: kappa → κ, omega → ω."""
    for name, ch in _ASCII_TO_GREEK.items():
        s = re.sub(r'\b' + name + r'\b', ch, s)
    return s


def _abbrev_forms(entity_lo: str) -> list:
    """Other written forms of a name: genus abbreviations, chemical commas, last symbol token."""
    forms = []
    words = entity_lo.split()

    # Two-word Latin species → abbreviated (Genus species → G. species)
    if len(words) == 2 and len(words[0]) > 3 and words[0][0].isalpha():
        forms.append(words[0][0] + ". " + words[1])

    # Hyphen between digits: 2-4-X → 2,4-X
    dehyphen = re.sub(r'(\d)-(\d)', r'\1,\2', entity_lo)
    if dehyphen != entity_lo:
        forms.append(dehyphen)

    # Last significant token (handles "GTPase CDC42" → "cdc42", "DAF-16" etc.)
    tokens = [t for t in re.split(r"[\s]+", entity_lo) if len(t) >= 3]
    if len(tokens) >= 2:
        last = tokens[-1]
        # Only use if it looks like a gene/protein symbol (has digits or is all caps)
        if re.search(r'\d', last) or last.upper() == last or re.match(r'[a-z]+\d', last):
            forms.append(last)

    return forms

# Runtime cache: {canonical_id → set of known names from the database}
_ALIAS_CACHE: dict = {}


def _fetch_aliases(canonical_id: str) -> set:
    """All names of an entity from its database (NCBI Gene, MeSH, Taxonomy), lowercased."""
    if canonical_id in _ALIAS_CACHE:
        return _ALIAS_CACHE[canonical_id]

    aliases: set = set()

    try:
        if canonical_id.startswith("NCBI_GENE:") or canonical_id.isdigit():
            gene_id = canonical_id.replace("NCBI_GENE:", "").strip()
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "gene", "id": gene_id, "retmode": "json"},
                timeout=8,
            )
            data = r.json().get("result", {}).get(gene_id, {})
            for field in ("name", "description", "otheraliases", "nomenclaturesymbol"):
                val = data.get(field, "")
                if val:
                    for part in val.split(","):
                        p = part.strip().lower()
                        if p:
                            aliases.add(p)

        elif canonical_id.startswith("NCBITaxon:"):
            taxon_id = canonical_id.replace("NCBITaxon:", "").strip()
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "taxonomy", "id": taxon_id, "retmode": "json"},
                timeout=8,
            )
            data = r.json().get("result", {}).get(taxon_id, {})
            for field in ("scientificname", "commonname"):
                val = data.get(field, "")
                if val:
                    aliases.add(val.lower())

        elif canonical_id.startswith("MESH:"):
            mesh_id = canonical_id.replace("MESH:", "").strip()
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "mesh", "id": mesh_id, "retmode": "json"},
                timeout=8,
            )
            data = r.json().get("result", {}).get(mesh_id, {})
            for field in ("ds_meshterms", "ds_scopenote"):
                val = data.get(field, "")
                if isinstance(val, list):
                    aliases.update(v.lower() for v in val if v)
                elif val:
                    aliases.add(val.lower()[:60])

    except Exception:
        pass

    _ALIAS_CACHE[canonical_id] = aliases
    if aliases:
        time.sleep(0.1)
    return aliases


def _entity_in_text(entity: str, text_lo: str, canonical_id: str = "") -> bool:
    """Whether an entity is in the text: exact or Greek-normalised forms, its tokens, abbreviated
    species, alternate forms, then database aliases."""
    if not entity or not text_lo:
        return False

    e_lo = entity.lower().strip()

    def _check(term: str) -> bool:
        return bool(term) and len(term) > 2 and term in text_lo

    # 1. Exact + Greek-normalized form
    if _check(e_lo):
        return True
    e_greek_norm = _normalize_greek(e_lo)         # κ → kappa, ω → omega
    e_ascii_norm = _ascii_to_greek_norm(e_lo)      # kappa → κ
    if _check(e_greek_norm) or _check(e_ascii_norm):
        return True

    # Also normalize text to catch entity "NF-κB" when text says "NF-kB"
    text_greek_norm = _normalize_greek(text_lo)
    if _check(e_lo) or e_greek_norm in text_greek_norm:
        return True

    # 2. Strip LLM-added prefixes
    for prefix in _STRIP_PREFIXES:
        if e_lo.startswith(prefix):
            shorter = e_lo[len(prefix):]
            if _check(shorter) or shorter in text_lo:
                return True

    # 3. All significant tokens present
    tokens = [t for t in re.split(r"[\s\-/\(\)]+", e_lo)
              if len(t) > 2 and t not in _NOISE_WORDS]
    if len(tokens) >= 2 and all(t in text_lo for t in tokens):
        return True
    # Also try with greek-normalized text
    if len(tokens) >= 2 and all(t in text_greek_norm for t in tokens):
        return True

    # 4. First and last gene/protein token (word boundary)
    for token in ([tokens[0]] if tokens else []) + ([tokens[-1]] if len(tokens) >= 2 else []):
        if len(token) >= 3 and re.search(r'\b' + re.escape(token) + r'\b', text_lo):
            return True

    # 5 & 6. Abbreviated forms and other variants
    for alt in _abbrev_forms(e_lo):
        if _check(alt):
            return True
        alt_gnorm = _normalize_greek(alt)
        if alt_gnorm != alt and _check(alt_gnorm):
            return True

    # 7. Database aliases (live lookup — no hardcoding)
    if canonical_id and not canonical_id.startswith("TEXT:"):
        for alias in _fetch_aliases(canonical_id):
            a = alias.lower()
            if len(a) > 2 and (a in text_lo or _normalize_greek(a) in text_greek_norm):
                return True
            # Also generate abbreviated forms of each alias
            for alt in _abbrev_forms(a):
                if len(alt) > 2 and alt in text_lo:
                    return True

    return False


# ── Supporting sentence finder ─────────────────────────────────────────────────

def find_supporting_sentence(text: str, subject: str, obj: str,
                              s_id: str = "", o_id: str = "",
                              window: int = 2) -> str:
    """Find the sentence(s) most likely supporting the relation using flexible + alias matching."""
    if not text:
        return ""
    sentences = re.split(r'(?<=[.!?])\s+', text)

    relevant_idx = set()
    for i, sent in enumerate(sentences):
        sl = sent.lower()
        if _entity_in_text(subject, sl, s_id) or _entity_in_text(obj, sl, o_id):
            for j in range(max(0, i - window), min(len(sentences), i + window + 1)):
                relevant_idx.add(j)

    if relevant_idx:
        return " ".join(sentences[i] for i in sorted(relevant_idx))[:500]

    return text[:200] + "…" if text else ""


def verify_triple(triple: dict, fetch_text: bool = False) -> dict:
    """Verify one triple against its source paper(s)."""
    subject = triple.get("subject_name", "")
    obj     = triple.get("object_name",  "")
    sources = []
    try:
        sources = json.loads(triple.get("source_papers", "[]"))
    except Exception:
        src = triple.get("source_papers", "")
        sources = [src] if src else []
    sources = [s for s in sources if s]

    results = []
    for doc_id in sources:
        text = ""
        if fetch_text:
            text = get_paper_text(doc_id)

        text_lo  = text.lower()
        s_id     = triple.get("subject_id", "")
        o_id     = triple.get("object_id",  "")
        s_found  = _entity_in_text(subject, text_lo, s_id)
        o_found  = _entity_in_text(obj,     text_lo, o_id)

        if not text:
            status   = "UNVERIFIABLE"
            note     = "Paper text not fetched (run with --fetch to retrieve)"
            sentence = ""
        elif s_found and o_found:
            status   = "CONFIRMED"
            note     = f"Both '{subject}' and '{obj}' found in paper text"
            sentence = find_supporting_sentence(text, subject, obj, s_id, o_id)
        elif s_found:
            status   = "PARTIAL"
            note     = f"Subject '{subject}' found — object '{obj}' not matched in text"
            sentence = find_supporting_sentence(text, subject, obj, s_id, o_id)
        elif o_found:
            status   = "PARTIAL"
            note     = f"Object '{obj}' found — subject '{subject}' not matched in text"
            sentence = find_supporting_sentence(text, subject, obj, s_id, o_id)
        else:
            status   = "MISMATCH"
            note     = f"Neither '{subject}' nor '{obj}' found — check if paper is open access"
            sentence = ""

        results.append({
            "doc_id":    doc_id,
            "status":    status,
            "note":      note,
            "sentence":  sentence,
        })

    return {
        "id":           triple.get("id"),
        "subject":      subject,
        "subject_id":   triple.get("subject_id", ""),
        "relation":     triple.get("relation", ""),
        "object":       obj,
        "object_id":    triple.get("object_id", ""),
        "negated":      bool(triple.get("negated", 0)),
        "confidence":   triple.get("confidence", 0.0),
        "reasoning":    triple.get("reasoning", ""),
        "is_contradiction": bool(triple.get("is_contradiction", 0)),
        "sources":      results if results else [{"doc_id": "none", "status": "UNVERIFIABLE",
                                                   "note": "No source paper recorded", "sentence": ""}],
        "overall_status": (
            "CONFIRMED"     if any(r["status"] == "CONFIRMED" for r in results) else
            "PARTIAL"       if any(r["status"] == "PARTIAL"   for r in results) else
            "UNVERIFIABLE"  if all(r["status"] == "UNVERIFIABLE" for r in results) else
            "MISMATCH"
        ) if results else "UNVERIFIABLE",
    }


# ── Accuracy: the paper's answer key, precision and recall ───────────────────
# Answer key: the distinct findings the whole paper states, in the pipeline's scope, each with the sentence
# that states it. Recall: key findings the KG contains. Precision: KG facts the paper states or directly
# implies, judged on the paper's sentences that name both entities. Names match through the paper's own
# abbreviations and the KG's canonical names and synonyms; an LLM judges wording differences.

_KEY_WINDOW = int(os.getenv("ACCURACY_WINDOW_CHARS", "10000"))
_JUDGE_BATCH = 25
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


def _key_system() -> str:
    try:
        from src.schema.taxonomy import RelationType
        types_str = " | ".join(sorted(r.value for r in RelationType))
    except Exception:
        types_str = ""
    return (
        "You list the distinct findings a scientific paper states, to check a knowledge graph built from it.\n"
        f"Relation values (use only these): {types_str}\n\n"
        "A finding links two named biological or clinical entities (gene, protein, chemical, drug, disease,\n"
        "phenotype, biological process, pathway, cell type, tissue, organism, intervention, health outcome).\n"
        "- Include the paper's own results and findings it reports from earlier research; negative findings\n"
        "  with negated=true.\n"
        "- One entry per finding: when a sentence states the same finding for several entities (A, B and C\n"
        "  inhibit X), give one entry with the entities as written.\n"
        "- Leave out definitions and classifications (X is a type of Y), methods and study design, statistics\n"
        "  that are not a relation, software, apps, ratings or usage counts, and anything the text does not state.\n"
        "Return JSON: {\"relations\": [{\"subject\": \"...\", \"relation\": \"...\", \"object\": \"...\",\n"
        "\"negated\": false, \"sentence\": \"the exact sentence from the text\"}]}"
    )


def _windows(text: str, size: int) -> list:
    """The text in consecutive windows of about `size` characters, cut at sentence ends."""
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind(". ", start + size // 2, end)
            end = cut + 1 if cut > 0 else end
        out.append(text[start:end])
        start = end
    return [w for w in out if w.strip()]


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _key_facts(text: str) -> list:
    """The paper's distinct findings, each kept only if its sentence is in the text."""
    from src.llm_client import call_llm, parse_json
    from src.postextraction.name_matching import term_key
    system, plain, facts, seen = _key_system(), _squash(text), [], set()
    for window in _windows(text, _KEY_WINDOW):
        try:
            raw = call_llm([{"role": "system", "content": system},
                            {"role": "user", "content": f"TEXT:\n{window}"}], max_tokens=4096)
            found = parse_json(raw).get("relations", [])
        except Exception:
            continue
        for f in found if isinstance(found, list) else []:
            if not isinstance(f, dict) or not all(f.get(k) for k in ("subject", "relation", "object")):
                continue
            sentence = _squash(f.get("sentence", ""))
            if not sentence or sentence[:60] not in plain:           # not stated in the text
                continue
            key = (term_key(f["subject"]), str(f["relation"]).lower(), term_key(f["object"]), bool(f.get("negated")))
            if key not in seen:
                seen.add(key)
                facts.append(f)
    return facts


def _findings(key: list) -> list:
    """Key entries from one sentence with the same relation and a shared subject or object are one finding."""
    from src.postextraction.name_matching import term_key
    parent = list(range(len(key)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    first: dict = {}
    for i, k in enumerate(key):
        sent, rel = _squash(k.get("sentence", "")), str(k["relation"]).lower()
        for side in ("subject", "object"):
            j = first.setdefault((sent, rel, side, term_key(k[side])), i)
            parent[root(i)] = root(j)
    groups: dict = {}
    for i in range(len(key)):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def _members(name: str) -> list:
    """The entities of a listed name ('A, B and C' → [A, B, C]); [name] otherwise."""
    parts = [p.strip() for p in re.split(r",\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+", name or "") if p.strip()]
    return parts if len(parts) > 1 else [name]


def _forms(name: str, abbrevs: dict) -> set:
    """A name, its expansion if the paper abbreviates it, and the abbreviation the paper gives it."""
    return {f for f in {name, abbrevs.get((name or "").strip(), "")} |
            {s for s, long in abbrevs.items() if _squash(long) == _squash(name)} if f}


def _same_entity(a: str, names: list, abbrevs: dict) -> bool:
    """A name matches an entity: exact or close name, one name contained in the other, or an abbreviation."""
    from src.postextraction.name_matching import match_quality, tokens, NONE
    for f in _forms(a, abbrevs):
        ft = set(tokens(f))
        for n in filter(None, names):
            nt = set(tokens(n))
            if match_quality(f, n) != NONE or (ft and nt and (ft <= nt or nt <= ft)):
                return True
    return False


def _in_sentence(names: list, sentence_tokens: set, sentence: str, abbrevs: dict) -> bool:
    """Whether a sentence names an entity (any of its names or forms)."""
    from src.postextraction.name_matching import tokens
    for n in filter(None, names):
        for f in _forms(n, abbrevs):
            ft = set(tokens(f))
            if (ft and ft <= sentence_tokens) or (len(f) <= 6 and re.search(rf"\b{re.escape(f)}\b", sentence)):
                return True
    return False


def _kg_names(fact: dict, side: str) -> list:
    syns = fact.get(f"{side}_synonyms") or ""
    syns = syns.split(";") if isinstance(syns, str) else list(syns)
    return [fact.get(f"{side}_name", ""), fact.get(f"{side}_canonical_name", "")] + [s.strip() for s in syns]


def _evidence(fact: dict, sentences: list, abbrevs: dict) -> str:
    """The paper's sentences that name both entities of a KG fact (up to 3)."""
    s_names, o_names = _kg_names(fact, "subject"), _kg_names(fact, "object")
    hits = [s for s, toks in sentences
            if _in_sentence(s_names, toks, s, abbrevs) and _in_sentence(o_names, toks, s, abbrevs)]
    return " … ".join(hits[:3])[:1500]


def _judge(items: list, prompt: str) -> list:
    """LLM yes/no for each item, in batches; False when the LLM is unavailable."""
    from src.llm_client import call_llm, parse_json
    out = []
    for b in range(0, len(items), _JUDGE_BATCH):
        batch = items[b:b + _JUDGE_BATCH]
        listing = "\n".join(f"{i}. {x}" for i, x in enumerate(batch, 1))
        try:
            raw = call_llm([{"role": "user", "content":
                             f"{prompt}\n\n{listing}\n\nReturn JSON: {{\"yes\": [numbers of the items that are yes]}}"}],
                           max_tokens=1024)
            yes = {int(x) for x in parse_json(raw).get("yes", [])}
        except Exception:
            yes = set()
        out += [i in yes for i in range(1, len(batch) + 1)]
    return out


def _fact_text(f: dict) -> str:
    neg = "NOT " if f.get("negated") else ""
    s = f.get("subject") or f.get("subject_name", "")
    o = f.get("object") or f.get("object_name", "")
    return f"{s} {neg}{str(f.get('relation', '')).replace('_', ' ')} {o}"


def _accuracy(kg_facts: list) -> dict | None:
    """Precision and recall of the KG facts against each paper in _TEXT_CACHE."""
    from src.postextraction.entity_normalization import find_abbreviations
    from src.postextraction.name_matching import tokens
    total_key = found = n_kg = 0
    supported: set = set()
    missed: list = []
    for doc_id, text in _TEXT_CACHE.items():
        if not text:
            continue
        kg = [f for f in kg_facts if doc_id in (f.get("source_papers") or "")]
        key = _key_facts(text)
        abbrevs = find_abbreviations(text)
        sentences = [(s, set(tokens(s))) for s in _SENTENCE_END.split(text) if len(s) > 20]

        # Recall: each key finding the KG contains — same names and relation, else judged on candidates
        hit = [False] * len(key)
        candidates: list = []
        for i, k in enumerate(key):
            subs, objs = _members(k["subject"]), _members(k["object"])
            ranked = []
            for f in kg:
                if bool(k.get("negated")) != bool(f.get("negated")):
                    continue
                s_ok = any(_same_entity(m, _kg_names(f, "subject"), abbrevs) for m in subs)
                o_ok = any(_same_entity(m, _kg_names(f, "object"), abbrevs) for m in objs)
                if s_ok and o_ok and str(k["relation"]).lower() == str(f.get("relation", "")).lower():
                    hit[i] = True
                    supported.add(f.get("id"))
                    break
                if s_ok or o_ok:
                    ranked.append((s_ok and o_ok, f))
            if not hit[i]:
                ranked.sort(key=lambda x: not x[0])
                candidates += [(i, f) for _, f in ranked[:3]]
        verdicts = _judge([f"Paper finding: {_fact_text(key[i])}  |  KG fact: {_fact_text(f)}" for i, f in candidates],
                          "For each item, does the KG fact express the paper's finding (the same entities, or a "
                          "listed member of them, in the same direction of effect)? Different wording counts.")
        for (i, f), same in zip(candidates, verdicts):
            if same:
                hit[i] = True
                supported.add(f.get("id"))

        # Precision: each KG fact the paper states or directly implies
        rest = [f for f in kg if f.get("id") not in supported]
        evid = [_evidence(f, sentences, abbrevs) for f in rest]
        stated = _judge([f"Text: \"{e or '(no sentence names both entities)'}\"  |  "
                         f"Reasoning given: \"{(f.get('reasoning') or '')[:300]}\"  |  Fact: {_fact_text(f)}"
                         for f, e in zip(rest, evid)],
                        "For each item, does the text state or directly imply the fact? Judge the text, not the "
                        "reasoning; the reasoning only shows where the fact came from.")
        supported |= {f.get("id") for f, ok in zip(rest, stated) if ok}

        groups = _findings(key)
        total_key += len(groups)
        found += sum(any(hit[i] for i in g) for g in groups)
        n_kg += len(kg)
        missed += [key[g[0]] for g in groups if not any(hit[i] for i in g)]
    if not total_key or not n_kg:
        return None
    precision = len(supported) / n_kg
    recall = found / total_key
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision":   round(precision, 4),
        "recall":      round(recall, 4),
        "f1":          round(f1, 4),
        "tp":          found,                 # paper findings the KG contains
        "fp":          n_kg - len(supported), # KG facts the paper does not state
        "fn":          total_key - found,     # key findings the KG misses
        "supported":   len(supported),
        "kg_facts":    n_kg,
        "key_facts":   total_key,
        "fn_examples": missed[:10],
    }


# ── HTML report ───────────────────────────────────────────────────────────────

def render_html(verifications: list, output: Path, node_stats: dict = None,
                eval_metrics: dict = None) -> None:
    total   = len(verifications)
    confirm = sum(1 for v in verifications if v["overall_status"] == "CONFIRMED")
    partial = sum(1 for v in verifications if v["overall_status"] == "PARTIAL")
    unver   = sum(1 for v in verifications if v["overall_status"] == "UNVERIFIABLE")
    mismatch= sum(1 for v in verifications if v["overall_status"] == "MISMATCH")

    # Node stats (passed from run_verification, or computed here as fallback)
    if node_stats is None:
        all_n, conf_n = set(), set()
        for v in verifications:
            for k in ("subject_id", "object_id"):
                nid = v.get(k, "")
                if nid:
                    all_n.add(nid)
                    if v["overall_status"] == "CONFIRMED":
                        conf_n.add(nid)
        node_stats = {"total": len(all_n), "confirmed": len(conf_n)}

    status_color = {
        "CONFIRMED":    "#238636",
        "PARTIAL":      "#d29922",
        "UNVERIFIABLE": "#8b949e",
        "MISMATCH":     "#f85149",
    }

    rows = ""
    for v in verifications:
        sc     = status_color.get(v["overall_status"], "#888")
        neg    = " [NEGATED]" if v["negated"] else ""
        contra = " ⚡ CONTRADICTION" if v["is_contradiction"] else ""
        conf   = v["confidence"]

        src_html = ""
        for s in v["sources"]:
            sc2 = status_color.get(s["status"], "#888")
            sent = s.get("sentence", "")
            src_html += f"""
            <div style="margin-top:8px;padding:8px;background:#21262d;border-radius:6px;border-left:3px solid {sc2}">
              <span style="color:{sc2};font-weight:600;font-size:11px">{s['status']}</span>
              <span style="color:#8b949e;font-size:11px;margin-left:8px">{s['doc_id']}</span>
              <div style="color:#c9d1d9;font-size:12px;margin-top:4px">{s['note']}</div>
              {f'<div style="color:#79c0ff;font-size:12px;margin-top:6px;font-style:italic">"' + sent + '"</div>' if sent else ''}
            </div>"""

        rows += f"""
        <details style="background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:8px;padding:12px">
          <summary style="cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px">
            <span style="background:{sc}33;color:{sc};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;flex-shrink:0">{v['overall_status']}</span>
            <span style="font-size:13px">
              <strong style="color:#79c0ff">{v['subject']}</strong>
              <span style="color:{status_color.get(v['relation'], '#f39c12')}"> →{v['relation'].replace('_',' ')}→ </span>
              <strong style="color:#79c0ff">{v['object']}</strong>
              <span style="color:#f85149">{neg}</span>
              <span style="color:#f39c12;font-size:11px">{contra}</span>
            </span>
            <span style="color:#8b949e;font-size:11px;margin-left:auto" title="Confidence score assigned by LLM during extraction (Layer 5)">LLM extraction: {int(v["confidence"]*100)}%</span>
          </summary>

          <div style="margin-top:12px;font-size:12px">
            <div style="color:#8b949e;margin-bottom:4px">CANONICAL IDs</div>
            <code style="color:#79c0ff">{v['subject_id']}</code>
            <span style="color:#8b949e"> → </span>
            <code style="color:#79c0ff">{v['object_id']}</code>
          </div>

          {f'<div style="margin-top:10px;padding:8px;background:#21262d;border-radius:6px;color:#c9d1d9;font-size:12px;font-style:italic">Reasoning: "{v["reasoning"]}"</div>' if v["reasoning"] else ''}

          <div style="margin-top:10px">
            <div style="color:#8b949e;font-size:12px;margin-bottom:4px">SOURCE PAPER EVIDENCE</div>
            {src_html}
          </div>
        </details>"""

    # ── Accuracy metrics (Precision always available; Recall/F1 from eval_metrics) ──
    em          = eval_metrics or {}
    decidable   = confirm + mismatch
    precision   = em["precision"] if "supported" in em else (confirm / decidable if decidable > 0 else 0.0)
    prec_pct    = f"{precision*100:.1f}%"
    prec_color  = "#238636" if precision >= 0.90 else ("#d29922" if precision >= 0.70 else "#f85149")

    recall_val  = eval_metrics.get("recall")  if eval_metrics else None
    f1_val      = eval_metrics.get("f1")      if eval_metrics else None
    fn_count    = eval_metrics.get("fn")      if eval_metrics else None

    rec_pct     = f"{recall_val*100:.1f}%"   if recall_val is not None else "N/A"
    f1_pct      = f"{f1_val*100:.1f}%"       if f1_val     is not None else "N/A"
    rec_color   = ("#238636" if (recall_val or 0) >= 0.90 else
                   "#d29922" if (recall_val or 0) >= 0.70 else
                   "#f85149") if recall_val is not None else "#8b949e"
    f1_color    = ("#238636" if (f1_val or 0) >= 0.90 else
                   "#d29922" if (f1_val or 0) >= 0.70 else
                   "#f85149") if f1_val is not None else "#8b949e"
    fn_display  = str(fn_count) if fn_count is not None else "—"
    new_metrics = "supported" in em                     # answer-key metrics; older reports have names only
    tp_display  = em.get("tp", confirm) if new_metrics else confirm
    fp_display  = em.get("fp", mismatch) if new_metrics else mismatch
    key_display = em.get("key_facts", "—")
    prec_basis  = ("facts the paper states / all KG facts" if new_metrics else "entity names found in the paper")

    recall_hint = (
        "" if recall_val is not None
        else "<div style='color:#8b949e;font-size:11px;margin-top:16px'>"
             "Recall &amp; F1 need the paper text — run verification with <code>--fetch</code></div>"
    )

    # F1 verdict label
    if f1_val is not None:
        verdict = ("Excellent" if f1_val >= 0.90 else "Good" if f1_val >= 0.75
                   else "Fair" if f1_val >= 0.60 else "Needs improvement")
        f1_verdict = f"<span style='font-size:11px;color:{f1_color}'>{verdict}</span>"
    elif precision >= 0.90:
        f1_verdict = "<span style='font-size:11px;color:#238636'>High precision</span>"
    else:
        f1_verdict = ""

    _show_accuracy = os.getenv("ENABLE_ACCURACY_METRICS", "false").lower() == "true"
    accuracy_panel = "" if not _show_accuracy else f"""
<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px 24px;margin:20px 0 28px">
  <div style="font-size:13px;font-weight:700;color:#e6edf3;margin-bottom:16px;letter-spacing:.03em">
    📊 KG ACCURACY METRICS
    <span style="font-weight:400;color:#8b949e;font-size:11px;margin-left:8px">Against an answer key of every finding the whole paper states, matched by meaning</span>
  </div>

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:16px">
    <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px 16px;text-align:center">
      <div style="font-size:26px;font-weight:700;color:{prec_color}">{prec_pct}</div>
      <div style="font-size:11px;color:#8b949e;margin-top:2px">PRECISION</div>
      <div style="font-size:10px;color:#8b949e">{prec_basis}</div>
    </div>
    <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px 16px;text-align:center">
      <div style="font-size:26px;font-weight:700;color:{rec_color}">{rec_pct}</div>
      <div style="font-size:11px;color:#8b949e;margin-top:2px">RECALL</div>
      <div style="font-size:10px;color:#8b949e">paper facts found in the KG / all paper facts</div>
    </div>
    <div style="background:#0d1117;border:2px solid {f1_color}44;border-radius:8px;padding:12px 16px;text-align:center">
      <div style="font-size:26px;font-weight:700;color:{f1_color}">{f1_pct}</div>
      <div style="font-size:11px;color:#8b949e;margin-top:2px">F1 SCORE</div>
      <div style="font-size:10px;color:#8b949e">{f1_verdict}</div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px">
    <div style="background:#23863622;border:1px solid #23863644;border-radius:6px;padding:8px 12px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#238636">{tp_display}</div>
      <div style="font-size:10px;color:#8b949e">✓ Found — paper facts in the KG</div>
    </div>
    <div style="background:#f8514922;border:1px solid #f8514944;border-radius:6px;padding:8px 12px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#f85149">{fp_display}</div>
      <div style="font-size:10px;color:#8b949e">✗ Not stated — KG facts the paper does not state</div>
    </div>
    <div style="background:#d2992222;border:1px solid #d2992244;border-radius:6px;padding:8px 12px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#d29922">{fn_display}</div>
      <div style="font-size:10px;color:#8b949e">✗ Missed — paper facts not in the KG</div>
    </div>
    <div style="background:#a371f722;border:1px solid #a371f744;border-radius:6px;padding:8px 12px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#a371f7">{key_display}</div>
      <div style="font-size:10px;color:#8b949e">PAPER FACTS (answer key)</div>
    </div>
    <div style="background:#8b949e22;border:1px solid #8b949e44;border-radius:6px;padding:8px 12px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#8b949e">{unver}</div>
      <div style="font-size:10px;color:#8b949e">~ UNVERIFIABLE</div>
    </div>
    <div style="background:#58a6ff22;border:1px solid #58a6ff44;border-radius:6px;padding:8px 12px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#58a6ff">{total}</div>
      <div style="font-size:10px;color:#8b949e">TOTAL in KG</div>
    </div>
  </div>
  {recall_hint}
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>KG Verification Report</title>
<style>
  body {{ background:#0d1117; color:#e6edf3; font-family:'Segoe UI',system-ui,sans-serif; padding:24px; max-width:960px; margin:0 auto; }}
  h1 {{ color:#58a6ff; font-size:20px; margin-bottom:4px; }}
  .stats {{ display:flex; gap:16px; margin:16px 0 24px; flex-wrap:wrap; }}
  .stat {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:10px 16px; text-align:center; }}
  .stat-n {{ font-size:28px; font-weight:700; }}
  .stat-l {{ font-size:11px; color:#8b949e; }}
  .filter-row {{ display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }}
  .filter-btn {{ background:#21262d; border:1px solid #30363d; color:#c9d1d9; padding:4px 12px; border-radius:6px; cursor:pointer; font-size:12px; }}
  .filter-btn.active {{ background:#1f6feb; border-color:#388bfd; color:#fff; }}
  summary::-webkit-details-marker {{ display:none; }}
</style>
</head>
<body>
<h1>🧬 Knowledge Graph — Verification Report</h1>
<p style="color:#8b949e;font-size:13px">Each triple verified against its source paper. Click any row to expand the evidence.</p>

{accuracy_panel}

<div style="color:#8b949e;font-size:12px;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:.05em">
  Edges / Relations
</div>
<div class="stats">
  <div class="stat"><div class="stat-n" style="color:#238636">{confirm}</div><div class="stat-l">CONFIRMED</div></div>
  <div class="stat"><div class="stat-n" style="color:#d29922">{partial}</div><div class="stat-l">PARTIAL</div></div>
  <div class="stat"><div class="stat-n" style="color:#8b949e">{unver}</div><div class="stat-l">UNVERIFIABLE<br><span style="font-size:9px">(run --fetch)</span></div></div>
  <div class="stat"><div class="stat-n" style="color:#f85149">{mismatch}</div><div class="stat-l">MISMATCH</div></div>
  <div class="stat"><div class="stat-n" style="color:#58a6ff">{total}</div><div class="stat-l">TOTAL EDGES</div></div>
</div>

<div style="color:#8b949e;font-size:12px;margin:16px 0 8px;font-weight:600;text-transform:uppercase;letter-spacing:.05em">
  Nodes / Entities  <span style="font-size:10px;font-weight:400">(an entity is confirmed if it appears in at least one confirmed triple)</span>
</div>
<div class="stats" style="margin-bottom:24px">
  <div class="stat"><div class="stat-n" style="color:#238636">{node_stats["confirmed"]}</div><div class="stat-l">CONFIRMED</div></div>
  <div class="stat"><div class="stat-n" style="color:#8b949e">{node_stats["total"] - node_stats["confirmed"]}</div><div class="stat-l">UNCONFIRMED</div></div>
  <div class="stat"><div class="stat-n" style="color:#58a6ff">{node_stats["total"]}</div><div class="stat-l">TOTAL NODES</div></div>
</div>

<div class="filter-row">
  <button class="filter-btn active" onclick="filterAll()">All</button>
  <button class="filter-btn" onclick="filterBy('CONFIRMED')">Confirmed</button>
  <button class="filter-btn" onclick="filterBy('PARTIAL')">Partial</button>
  <button class="filter-btn" onclick="filterBy('MISMATCH')">Mismatch / Human Review</button>
  <button class="filter-btn" onclick="filterBy('UNVERIFIABLE')">Unverifiable</button>
</div>

<div id="rows">{rows}</div>

<script>
function filterAll() {{
  document.querySelectorAll('#rows details').forEach(d => d.style.display='');
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
}}
function filterBy(status) {{
  document.querySelectorAll('#rows details').forEach(d => {{
    const badge = d.querySelector('summary span:first-child');
    d.style.display = badge && badge.textContent.trim() === status ? '' : 'none';
  }});
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
}}
</script>
</body>
</html>"""
    output.write_text(html, encoding="utf-8")


# ── Main ───────────────────────────────────────────────────────────────────────

def run_verification(
    fetch_text: bool = True,
    min_confidence: float = 0.0,
    relation_filter=None,
    doc_id_filter=None,
    out_html: Path = _OUT_HTML,
    out_json: Path = _OUT_JSON,
    progress_cb=None,
    db_path=None,          # explicit override (e.g. staging DB before commit)
    formats: str = "neo4j",  # which format DB to read from when db_path not set
    include_human_review: bool = None,  # None = auto: True only for unified DB, False for staging
) -> dict:
    """Verify the KG's triples; returns counts (total, confirmed, partial, mismatch, unverifiable),
    pct, results and the report paths."""
    # Clear per-run caches so re-runs in the same process get fresh data
    _TEXT_CACHE.clear()
    _ALIAS_CACHE.clear()

    # DB: db_path, else TRIPLE_STORE_PATH, else the format's DB, else triple_store.db
    import os as _os
    env_path = _os.getenv("TRIPLE_STORE_PATH", "")
    active_db = Path(
        db_path or env_path or str(_db_for_format(formats))
    )

    if not active_db.exists():
        return {"total": 0, "confirmed": 0, "partial": 0, "mismatch": 0,
                "unverifiable": 0, "pct": 0, "results": [],
                "html_path": out_html, "json_path": out_json}

    # If the DB file exists but has no 'triples' table (e.g. a fresh/empty staging
    # DB when resuming from Layer 8), fall back to the format-specific main DB.
    def _has_triples_table(db: Path) -> bool:
        try:
            c = sqlite3.connect(str(db))
            r = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='triples'"
            ).fetchone()
            c.close()
            return r is not None
        except Exception:
            return False

    if not _has_triples_table(active_db):
        fallback = _db_for_format(formats)
        if fallback.exists() and _has_triples_table(fallback):
            active_db = fallback
        else:
            # Last resort: legacy DB
            if _DB_PATH.exists() and _has_triples_table(_DB_PATH):
                active_db = _DB_PATH
            else:
                return {"total": 0, "confirmed": 0, "partial": 0, "mismatch": 0,
                        "unverifiable": 0, "pct": 0, "results": [],
                        "html_path": out_html, "json_path": out_json}

    conn = sqlite3.connect(str(active_db))
    conn.row_factory = sqlite3.Row
    # Main query: only auto-approved triples (not flagged) for text verification
    query  = "SELECT * FROM triples WHERE confidence >= ? AND flagged_for_review = 0"
    params = [min_confidence]
    if relation_filter:
        query  += " AND relation = ?"
        params.append(relation_filter)
    if doc_id_filter:
        query  += " AND source_papers LIKE ?"
        params.append(f"%{doc_id_filter}%")
    query += " ORDER BY confidence DESC"
    triples = [dict(r) for r in conn.execute(query, params).fetchall()]

    # Flagged triples: semantic REJECT → MISMATCH; REVIEW or TEXT: IDs → PARTIAL
    _sem_params = [min_confidence]
    _sem_q = (
        "SELECT * FROM triples WHERE confidence >= ? AND flagged_for_review = 1 "
        "AND (review_reason LIKE 'SEMANTIC_REJECT%' "
        "     OR review_reason LIKE 'SEMANTIC_REVIEW%' "
        "     OR review_reason LIKE '%has no canonical ontology ID%')"
    )
    if doc_id_filter:
        _sem_q += " AND source_papers LIKE ?"
        _sem_params.append(f"%{doc_id_filter}%")
    _sem_triples = [dict(r) for r in conn.execute(_sem_q, _sem_params).fetchall()]
    conn.close()
    # A paper's staging DB keeps rows from its earlier runs: report rejected rows only from this run
    _run_rows = [t.get("updated_at") or t.get("created_at") or "" for t in triples]
    _run_start = min((x for x in _run_rows if x), default="")
    if _run_start:
        _sem_triples = [t for t in _sem_triples if (t.get("updated_at") or t.get("created_at") or "") >= _run_start]

    # Tag each flagged triple with its pre-determined verification status
    for _st in _sem_triples:
        _reason = (_st.get("review_reason") or "")
        if _reason.startswith("SEMANTIC_REJECT"):
            _st["_pre_status"] = "MISMATCH"
        elif "has no canonical ontology ID" in _reason:
            _st["_pre_status"] = "PARTIAL"   # valid relation, entity ID needs correction
        else:
            _st["_pre_status"] = "PARTIAL"   # SEMANTIC_REVIEW
        _st["_flag_reason"] = _reason

    # Human-review triples: unified DB only, so other papers' triples stay out of a staging check
    _load_hr = include_human_review if include_human_review is not None else (db_path is None)
    _hr_triples: list = []
    if _load_hr:
        _hr_root = Path("data/kg_output")
        _hr_seen: set = set()   # deduplicate across multiple runs of the same paper
        for _hr_path in sorted(_hr_root.glob("*/human_review.jsonl")):
            try:
                for _line in _hr_path.read_text(encoding="utf-8").splitlines():
                    _line = _line.strip()
                    if not _line:
                        continue
                    _rec = json.loads(_line)
                    if not _rec.get("subject_name") or not _rec.get("object_name"):
                        continue
                    if doc_id_filter and doc_id_filter not in (_rec.get("document_id", "")):
                        continue
                    # Skip already-approved records — they made it into the main DB
                    if _rec.get("status") == "APPROVED":
                        continue
                    # Deduplicate same triple across multiple pipeline runs
                    _hr_key = (_rec.get("subject_name",""), _rec.get("relation",""),
                               _rec.get("object_name",""), _rec.get("document_id",""))
                    if _hr_key in _hr_seen:
                        continue
                    _hr_seen.add(_hr_key)
                    _hr_triples.append({
                        "subject_name":  _rec.get("subject_name", ""),
                        "subject_type":  _rec.get("subject_type", ""),
                        "subject_id":    _rec.get("subject_id", "NEEDS_REVIEW"),
                        "relation":      _rec.get("relation", ""),
                        "object_name":   _rec.get("object_name", ""),
                        "object_type":   _rec.get("object_type", ""),
                        "object_id":     _rec.get("object_id", "NEEDS_REVIEW"),
                        "negated":       _rec.get("negated", False),
                        "confidence":    _rec.get("confidence", 0.0),
                        "source_papers": json.dumps([_rec.get("document_id", "")]),
                        "reasoning":     _rec.get("reasoning", ""),
                        "is_contradiction": _rec.get("is_contradiction", False),
                        "_human_review": True,
                        "_review_reason": _rec.get("review_reason", ""),
                    })
            except Exception:
                continue

    results = []
    all_triples = triples + _sem_triples + _hr_triples
    for i, t in enumerate(all_triples, 1):
        if t.get("_pre_status"):
            # Pipeline-rejected/review triples: categorized by semantic verdict, not text search.
            _src_papers = t.get("source_papers", "[]")
            try:
                _doc_id = json.loads(_src_papers)[0] if _src_papers else ""
            except Exception:
                _doc_id = ""
            v = {
                "id":             t.get("id"),
                "subject":        t["subject_name"],
                "subject_id":     t.get("subject_id", ""),
                "relation":       t["relation"],
                "object":         t["object_name"],
                "object_id":      t.get("object_id", ""),
                "negated":        bool(t.get("negated", False)),
                "confidence":     float(t.get("confidence", 0.0)),
                "reasoning":      t.get("reasoning", ""),
                "is_contradiction": bool(t.get("is_contradiction", False)),
                "overall_status": t["_pre_status"],
                "sources":        [{"doc_id": _doc_id,
                                    "status": t["_pre_status"],
                                    "note":   t.get("_flag_reason", ""),
                                    "sentence": ""}],
            }
        elif t.get("_human_review"):
            # Human review triples get MISMATCH status — they failed the semantic gate.
            # They are shown separately in the HTML so humans can distinguish them.
            v = {
                "id":             None,
                "subject":        t["subject_name"],
                "subject_id":     t["subject_id"],
                "relation":       t["relation"],
                "object":         t["object_name"],
                "object_id":      t["object_id"],
                "negated":        bool(t.get("negated", False)),
                "confidence":     float(t.get("confidence", 0.0)),
                "reasoning":      t.get("reasoning", ""),
                "is_contradiction": bool(t.get("is_contradiction", False)),
                "overall_status": "MISMATCH",
                "sources":        [{"doc_id": json.loads(t.get("source_papers","[]"))[0]
                                              if t.get("source_papers") else "",
                                    "status": "MISMATCH",
                                    "note":   t.get("_review_reason", "Failed semantic gate"),
                                    "sentence": ""}],
            }
        else:
            v = verify_triple(t, fetch_text=fetch_text)
        v["in_kg"] = not (t.get("_pre_status") or t.get("_human_review"))   # rejected/review rows are not in the KG
        results.append(v)
        if progress_cb:
            progress_cb(i, len(all_triples), v["overall_status"],
                        v["subject"], v["relation"], v["object"])

    # The report and its numbers describe the knowledge graph; rejected/review facts get their own report
    rejected = [v for v in results if not v.get("in_kg")]
    results  = [v for v in results if v.get("in_kg")]
    by_status = {}
    for v in results:
        by_status.setdefault(v["overall_status"], []).append(v)

    confirmed    = len(by_status.get("CONFIRMED",    []))
    partial      = len(by_status.get("PARTIAL",      []))
    mismatch     = len(by_status.get("MISMATCH",     []))
    unverifiable = len(by_status.get("UNVERIFIABLE", []))
    total        = len(results)
    pct          = confirmed * 100 // total if total else 0

    # A node is confirmed when it is in at least one CONFIRMED triple
    all_nodes:       set = set()
    confirmed_nodes: set = set()
    for v in results:
        sid, oid = v.get("subject_id", ""), v.get("object_id", "")
        for nid in (sid, oid):
            if nid:
                all_nodes.add(nid)
                if v["overall_status"] == "CONFIRMED":
                    confirmed_nodes.add(nid)

    out_html.parent.mkdir(parents=True, exist_ok=True)

    # Recall: LLM re-extraction of the cached texts, else the saved eval_report.json
    _eval_metrics = None
    _eval_path    = out_json.parent / "eval_report.json"

    if fetch_text and _TEXT_CACHE:
        _eval_metrics = _accuracy(triples)
        if _eval_metrics:
            try:
                _eval_path.write_text(json.dumps(_eval_metrics, indent=2))
            except Exception:
                pass

    if _eval_metrics is None and _eval_path.exists():
        try:
            _eval_metrics = json.loads(_eval_path.read_text())
        except Exception:
            pass

    render_html(results, out_html,
                node_stats={"total": len(all_nodes), "confirmed": len(confirmed_nodes)},
                eval_metrics=_eval_metrics)
    out_json.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    rej_html = out_html.with_name("verification_rejected.html")
    rej_json = out_json.with_name("verification_rejected.json")
    rej_json.write_text(json.dumps(rejected, indent=2, default=str), encoding="utf-8")
    if rejected:
        render_html(rejected, rej_html, node_stats={"total": 0, "confirmed": 0})

    return {
        "total":             total,
        "confirmed":         confirmed,
        "partial":           partial,
        "mismatch":          mismatch,
        "unverifiable":      unverifiable,
        "pct":               pct,
        "total_nodes":       len(all_nodes),
        "confirmed_nodes":   len(confirmed_nodes),
        "results":           results,
        "html_path":         out_html,
        "json_path":         out_json,
        "rejected":          len(rejected),            # rejected/review facts, not in the KG
        "rejected_json_path": rej_json,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify KG triples against source papers.")
    parser.add_argument("--fetch",          action="store_true",
                        help="Fetch paper texts from PubMed/PMC (requires internet)")
    parser.add_argument("--relation",       default=None,
                        help="Filter by relation type (e.g. inhibits)")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--output-html",    default=str(_OUT_HTML))
    parser.add_argument("--output-json",    default=str(_OUT_JSON))
    args = parser.parse_args()

    if not _DB_PATH.exists():
        print("Triple store not found. Run the pipeline first.")
        return

    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM triples WHERE confidence >= ?"
    params = [args.min_confidence]
    if args.relation:
        query += " AND relation = ?"
        params.append(args.relation)
    query += " ORDER BY confidence DESC"
    triples = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    # Load human_review triples — they count as FP until approved
    _hr_triples_cli: list = []
    _hr_root_cli = Path("data/kg_output")
    for _hr_path in sorted(_hr_root_cli.glob("*/human_review.jsonl")):
        try:
            for _line in _hr_path.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if not _line:
                    continue
                _rec = json.loads(_line)
                if not _rec.get("subject_name") or not _rec.get("object_name"):
                    continue
                _hr_triples_cli.append({
                    "subject_name":   _rec.get("subject_name", ""),
                    "subject_type":   _rec.get("subject_type", ""),
                    "subject_id":     _rec.get("subject_id", "NEEDS_REVIEW"),
                    "relation":       _rec.get("relation", ""),
                    "object_name":    _rec.get("object_name", ""),
                    "object_type":    _rec.get("object_type", ""),
                    "object_id":      _rec.get("object_id", "NEEDS_REVIEW"),
                    "negated":        _rec.get("negated", False),
                    "confidence":     _rec.get("confidence", 0.0),
                    "source_papers":  json.dumps([_rec.get("document_id", "")]),
                    "reasoning":      _rec.get("reasoning", ""),
                    "is_contradiction": _rec.get("is_contradiction", False),
                    "_human_review":  True,
                    "_review_reason": _rec.get("review_reason", ""),
                })
        except Exception:
            continue

    all_triples_cli = triples + _hr_triples_cli
    print(f"\n  Verifying {len(triples)} committed + {len(_hr_triples_cli)} human-review triple(s)…")
    if args.fetch:
        print("  Fetching paper texts from PubMed/PMC (this takes a moment)…")

    results = []
    for i, t in enumerate(all_triples_cli, 1):
        if t.get("_human_review"):
            v = {
                "id":             None,
                "subject":        t["subject_name"],
                "subject_id":     t["subject_id"],
                "relation":       t["relation"],
                "object":         t["object_name"],
                "object_id":      t["object_id"],
                "negated":        bool(t.get("negated", False)),
                "confidence":     float(t.get("confidence", 0.0)),
                "reasoning":      t.get("reasoning", ""),
                "is_contradiction": bool(t.get("is_contradiction", False)),
                "overall_status": "MISMATCH",
                "sources":        [{"doc_id": (json.loads(t.get("source_papers","[]") or "[]") or [""])[0],
                                    "status": "MISMATCH",
                                    "note":   t.get("_review_reason", "Failed semantic gate"),
                                    "sentence": ""}],
            }
        else:
            v = verify_triple(t, fetch_text=args.fetch)
        results.append(v)
        status = v["overall_status"]
        print(f"  [{i:3}/{len(all_triples_cli)}] {status:<15} "
              f"[{v['subject'][:20]}] -{v['relation']}→ [{v['object'][:20]}]")

    # Summary
    by_status = {}
    for v in results:
        by_status.setdefault(v["overall_status"], []).append(v)

    print(f"\n  ── Summary ──────────────────────────────")
    for status, items in sorted(by_status.items()):
        print(f"  {status:<15} {len(items)}")
    print(f"  Total          {len(results)}")

    if args.fetch:
        confirmed = len(by_status.get("CONFIRMED", []))
        total     = len(results)
        print(f"\n  Verified {confirmed}/{total} triples against source paper text "
              f"({confirmed*100//total if total else 0}%)")

    # Write outputs
    out_html = Path(args.output_html)
    out_json = Path(args.output_json)
    out_html.parent.mkdir(parents=True, exist_ok=True)

    _eval_metrics2 = None
    _eval_path2 = out_html.parent / "eval_report.json"
    if _eval_path2.exists():
        try:
            _eval_metrics2 = json.loads(_eval_path2.read_text())
        except Exception:
            pass
    render_html(results, out_html, eval_metrics=_eval_metrics2)
    out_json.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    print(f"\n  ✓ HTML report: {out_html}")
    print(f"  ✓ JSON report: {out_json}")
    print(f"\n  To open: open {out_html}")
    print(f"\n  Tip: run with --fetch to verify against actual paper text")


if __name__ == "__main__":
    main()
