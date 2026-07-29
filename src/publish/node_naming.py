"""Shared node display-name resolution for Layer 8 writers and the unified exporter."""
import os
from collections import Counter

_COMPRESS_ENABLED   = os.getenv("NODE_NAME_COMPRESS_ENABLED", "true").strip().lower() in ("1", "true", "yes")
_COMPRESS_MAX_WORDS = int(os.getenv("NODE_NAME_COMPRESS_MAX_WORDS", "7"))
_LLM_BASE_URL       = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
_LLM_API_KEY        = os.getenv("LLM_API_KEY", "ollama")
_LLM_MODEL          = os.getenv("LLM_MODEL", "gemma2:27b")

_compress_cache: dict = {}
_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL, timeout=15.0)
    return _client


def _truncate(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"


def _compress_name(text: str) -> str:
    """Shorten a sentence-like surface form to a node label via LLM; falls back to truncation."""
    if text in _compress_cache:
        return _compress_cache[text]
    short = _truncate(text, _COMPRESS_MAX_WORDS)
    if _COMPRESS_ENABLED:
        try:
            resp = _get_client().chat.completions.create(
                model=_LLM_MODEL,
                temperature=0.0,
                max_tokens=16,
                messages=[{"role": "user", "content": (
                    "Reduce the following biomedical text to a short, concise "
                    "entity name (2-5 words, a noun phrase, no punctuation or "
                    "explanation) suitable as a knowledge-graph node label. "
                    "Return ONLY the name.\n\n"
                    f"Text: {text}"
                )}],
            )
            candidate = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
            if candidate and len(candidate.split()) <= _COMPRESS_MAX_WORDS + 2:
                short = candidate
        except Exception:
            pass
    _compress_cache[text] = short
    return short


def _resolve_display(names: list, canonical_names: list = None, is_uncertain: bool = False) -> tuple:
    """Returns (display_name, full_name). Prefers ontology canonical name unless is_uncertain or name is too long."""
    canon   = [c for c in (canonical_names or []) if c]
    surface = [n for n in (names or []) if n]

    best_canon   = Counter(canon).most_common(1)[0][0] if canon else ""
    best_surface = Counter(surface).most_common(1)[0][0] if surface else ""

    if best_canon and not is_uncertain and len(best_canon.split()) <= _COMPRESS_MAX_WORDS:
        return best_canon, ""

    full = best_canon or best_surface
    if not full:
        return "", ""

    if best_surface and best_surface.strip().lower() != full.strip().lower():
        if is_uncertain or len(best_surface.split()) <= _COMPRESS_MAX_WORDS:
            return best_surface, full

    if len(full.split()) > _COMPRESS_MAX_WORDS:
        short = _compress_name(full)
        if short and short.strip().lower() != full.strip().lower():
            return short, full

    return full, ""


def pick_node_name(names: list, canonical_names: list = None, is_uncertain: bool = False) -> str:
    """Pick the best display name for a canonical entity ID from all its mentions."""
    name, _ = _resolve_display(names, canonical_names, is_uncertain)
    return name


def pick_node_full_name(names: list, canonical_names: list = None, is_uncertain: bool = False) -> str:
    """Original uncompressed text — non-empty only when pick_node_name returned a compressed stand-in."""
    _, full = _resolve_display(names, canonical_names, is_uncertain)
    return full


def pick_node_source_url(source_urls: list) -> str:
    """Pick the most common source URL across all mentions of a canonical entity ID."""
    urls = [u for u in (source_urls or []) if u]
    if urls:
        return Counter(urls).most_common(1)[0][0]
    return ""


def merge_node_synonyms(synonym_strings: list) -> str:
    """Union all synonyms across mentions of a canonical entity ID into one deduplicated list."""
    seen: dict = {}
    for s in synonym_strings or []:
        if not s:
            continue
        for syn in s.split("; "):
            syn = syn.strip()
            if syn and syn.lower() not in seen:
                seen[syn.lower()] = syn
    return "; ".join(seen.values())


def pick_node_entity_type(entity_types: list) -> str:
    """Pick entity_type by majority vote across all mentions of a canonical entity ID."""
    types = [t for t in (entity_types or []) if t]
    if types:
        return Counter(types).most_common(1)[0][0]
    return "OTHER"


def merge_node_evidence(evidence_strings: list) -> str:
    """Union all evidence sentences across mentions of a canonical entity ID."""
    seen: dict = {}
    for s in evidence_strings or []:
        s = (s or "").strip()
        if s and s.lower() not in seen:
            seen[s.lower()] = s
    return "\n".join(seen.values())
