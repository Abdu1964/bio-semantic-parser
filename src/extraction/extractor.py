from __future__ import annotations
"""Layer 6 — LLM extraction engine. Builds prompts, calls Gemma, validates via Pydantic."""
import concurrent.futures
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from pydantic import ValidationError
from openai import OpenAI
import httpx

from src.schema.taxonomy import TAXONOMY, ENTITY_TAXONOMY, RelationType, EntityType
from src.schema.pydantic_model import BiologicalRelation, ExtractionResult

logger = logging.getLogger(__name__)

# ── Config from .env ──────────────────────────────────────────────────────────
_BASE_URL    = os.getenv("LLM_BASE_URL",    "http://localhost:11434/v1")
_API_KEY     = os.getenv("LLM_API_KEY",     "ollama")
_MODEL       = os.getenv("LLM_MODEL",       "gemma2:27b")
_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))
_MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
_QUEUE_PATH  = Path(os.getenv("REJECTED_QUEUE", "data/rejected.jsonl"))
# Room for a dense chunk's answer: a cut-off answer is split and asked again, which costs far more
_OUT_RESERVE      = int(os.getenv("LLM_EXTRACTION_OUTPUT_TOKENS", "5120"))
_WALL_TIMEOUT     = int(os.getenv("LLM_WALL_TIMEOUT",  "300"))
_WALL_TIMEOUT     = max(_WALL_TIMEOUT, _OUT_RESERVE // 10 + 60)   # a full answer at 10 tokens/s still fits
_HEALTH_TIMEOUT   = float(os.getenv("LLM_HEALTH_TIMEOUT", "8"))   # raise for slow networks
_COMPACT_TAXONOMY = os.getenv("LLM_COMPACT_TAXONOMY", "0").strip().lower() in ("1", "true", "yes")
_MAX_ENTITIES     = int(os.getenv("LLM_MAX_ENTITIES", "0"))
_CACHED_SYSTEM_PROMPT: str = ""

def _get_system_prompt() -> str:
    global _CACHED_SYSTEM_PROMPT
    if not _CACHED_SYSTEM_PROMPT:
        _CACHED_SYSTEM_PROMPT = _system_prompt()
    return _CACHED_SYSTEM_PROMPT

def _client() -> OpenAI:
    # Fresh client per call — keepalive disabled to prevent silent stale-connection hangs.
    return OpenAI(
        api_key    = _API_KEY,
        base_url   = _BASE_URL,
        http_client = httpx.Client(
            limits  = httpx.Limits(
                max_keepalive_connections = 0,
                max_connections           = 1,
            ),
            timeout = httpx.Timeout(600.0, connect=10.0),
        ),
    )


# ── JSON schema block (given to LLM in system prompt) ────────────────────────

_RELATION_VALUES = " | ".join(r.value for r in RelationType)
_ENTITY_VALUES   = " | ".join(e.value for e in EntityType)

_JSON_SCHEMA = """\
Required JSON output format — return ONLY this structure, nothing else:
{
  "relations": [
    {
      "extraction_viable": true,
      "subject_name":  "<verbatim entity text>",
      "subject_type":  "<EntityType value>",
      "relation":      "<RelationType value>",
      "object_name":   "<verbatim entity text>",
      "object_type":   "<EntityType value>",
      "negated":       false,
      "confidence":    0.0,
      "reasoning":     "<one sentence of 12–25 words: why this relation type, quoting the supporting words>",
      "species":       "<e.g. Homo sapiens>",
      "tissue":        "<e.g. hippocampus>",
      "condition":     "<e.g. caloric restriction>",
      "effect_size":   "<e.g. 40% reduction>",
      "source":        "<cited — only for earlier work the paper cites>",
      "evidence":      "<predicted — only for activity inferred by an analysis>"
    }
  ]
}

Leave out species, tissue, condition and effect_size when the text does not state them, source when it is the paper's own result, and evidence when it was measured — keep answers short.

If no relation can be extracted, return:
{
  "relations": [
    {
      "extraction_viable": false,
      "subject_name": "", "subject_type": "OTHER", "relation": null,
      "object_name": "", "object_type": "OTHER", "negated": false,
      "confidence": 0.0, "reasoning": "<explain why no relation found — min 50 chars>",
      "species": "", "tissue": "", "condition": "", "effect_size": ""
    }
  ]
}"""


# ── Taxonomy block ────────────────────────────────────────────────────────────

def _taxonomy_block() -> str:
    lines = ["RELATION TYPES — use ONLY values from this list:\n"]
    for rel, entry in TAXONOMY.items():
        if _COMPACT_TAXONOMY:
            not_this = entry.get("not_this", "")
            not_clause = f" [NOT: {not_this}]" if not_this else ""
            lines.append(f"  {rel.value}: {entry['definition']}{not_clause}")
        else:
            lines.append(
                f"  {rel.value}\n"
                f"    → {entry['definition']}\n"
                f"    EXAMPLE: {entry['example']}\n"
                f"    NOT THIS: {entry['not_this']}\n"
            )
    return "\n".join(lines)


def _entity_taxonomy_block() -> str:
    lines = ["ENTITY TYPES — use ONLY values from this list:\n"]
    for etype, entry in ENTITY_TAXONOMY.items():
        if _COMPACT_TAXONOMY:
            not_this = entry.get("not_this", "")
            not_clause = f" [NOT: {not_this}]" if not_this else ""
            lines.append(f"  {etype.value}: {entry['definition']}{not_clause}")
        else:
            lines.append(
                f"  {etype.value}\n"
                f"    → {entry['definition']}\n"
                f"    EXAMPLE: {entry['example']}\n"
                f"    NOT THIS: {entry['not_this']}\n"
            )
    return "\n".join(lines)


# ── Full system prompt ────────────────────────────────────────────────────────
# Shape the LLM's strategy before it starts —
# how to think about the task, not just what to put in each field.
def _system_prompt() -> str:
    return (
        "You are a biomedical relation extraction expert.\n"
        "Extract biological relations from text and return ONLY valid JSON.\n\n"
        + _taxonomy_block()
        + "\n"
        + _entity_taxonomy_block()
        + f"\nVALID relation values: {_RELATION_VALUES}\n"
        + f"VALID entity type values: {_ENTITY_VALUES}\n\n"
        + _JSON_SCHEMA
        + "\n\nRULES:\n"
        "1. Decide extraction_viable FIRST — true if a valid relation exists, false otherwise. "
        "   If false, stop: leave all other fields at defaults.\n"
        "2. relation MUST be one of the VALID relation values above — never invent a new type. "
        "   The relation types are the formal predicates from Biolink Model, Hetionet, and OpenBioLink. "
        "   These schemas are domain-agnostic: their definitions apply equally to molecular biology, "
        "   clinical medicine, epidemiology, and outcomes research. "
        "   When selecting a relation type, reason from the formal definition alone — "
        "   the example is one illustrative instance, not a restriction on subject or domain. "
        "   The not_this field distinguishes between similar relation types; it does not restrict "
        "   the domain, subject type, or context in which the relation can apply. "
        "   Only set extraction_viable=false if the formal definition genuinely does not apply "
        "   to the relation described in the text, after reading all definitions carefully.\n"
        "3. subject_name and object_name MUST come from the PRE-TAGGED ENTITIES list. "
        "   Only entities that appear in the PRE-TAGGED ENTITIES list are valid subjects or objects. "
        "   If a relation involves entities NOT in the pre-tagged list, set extraction_viable=false. "
        "   Use the exact verbatim text as it appears in the list. "
        "   Do NOT add species prefixes ('mammalian', 'human') unless the text says so.\n"
        "4. Produce exactly one JSON object per distinct subject–relation–object triple. "
        "   Do not merge different triples into one.\n"
        "5. Return ONLY the JSON — no explanation, no markdown fences, no extra text.\n"
        "6. subject_type and object_type MUST be one of the VALID entity type values above, chosen "
        "   by checking the ENTITY TYPES definitions — not by guessing from the bare name. "
        "   Each pre-tagged entity below shows a 'type=' hint from Layer 4 NER — use it as a signal, "
        "   but it may use different vocabulary than the entity types above (e.g. it might say "
        "   'CHEMICAL' or 'Gene' instead of a listed value); when it doesn't exactly match a VALID "
        "   entity type value, translate it to the closest fitting type using the definitions, don't "
        "   copy it verbatim. Only use OTHER after checking every definition above and genuinely "
        "   finding no match — it should be rare, not a default for uncertainty.\n"
        "7. When the text attributes an effect to a gene generically — 'mutations of GENE', "
        "   'variants in GENE', 'GENE deficiency' — and does NOT name one specific, identifiable "
        "   variant (an rsID, HGVS notation like c.467G>A, or a specific named mutation), use GENE "
        "   itself as the subject/object with entity_type=GENE. Do not invent a separate collective "
        "   entity like 'GENE mutations' typed as GENOMIC_VARIANT — that type is for one specific, "
        "   identifiable variant, not a generic reference to the class of mutations affecting a gene. "
        "   Only use GENOMIC_VARIANT/SEQUENCE_VARIANT when a specific variant is actually named.\n"
        "8. HAS_INHERITANCE_MODE exception to Rule 3: when the text states an inheritance pattern "
        "   ('autosomal recessive', 'autosomal dominant', 'X-linked', 'mitochondrial', etc.), produce "
        "   the triple even if the pattern is not in PRE-TAGGED ENTITIES. Subject=disease, "
        "   object=pattern phrase from text, entity_type=PHENOTYPE.\n"
        "9. Do not collapse similar clinical terms — 'developmental delay' and 'intellectual disability' "
        "   are distinct. Extract each named entity separately; never suppress a triple because a "
        "   similar one already exists.\n"
        "10. Subjects and objects must be specific, named biological entities. Never use a bare "
        "   category word ('gene', 'mutant', 'allele', 'transcripts', 'SNP', 'functional') or a "
        "   study-specific group label ('O lines', 'delayed-breeding populations', '156 genes', "
        "   'treated group'). Use the specific entity the text refers to (e.g. the organism, gene or "
        "   phenotype); if there is none, set extraction_viable=false for that relation.\n"
        "11. Negative findings are relations too. When the text reports that A did NOT change, affect, "
        "   regulate or associate with B (B was unaffected, unaltered or not elevated), extract that "
        "   relation with negated=true — even when Layer 4 flagged no negation.\n"
        "12. Use a directional relation (upregulates/downregulates, activates/inhibits) only when the text "
        "   states the direction for that entity. If it says levels were reversed, restored or normalized, "
        "   the direction is opposite to the change the text reports for that entity; if no such change is "
        "   stated, use regulates.\n"
        "13. When the text reports a change in an entity's level, extract the change (with the tissue in the "
        "   tissue field), not an additional expressed_in relation.\n"
        "14. Extract biological findings only — never how the experiment was done (reference genes used for "
        "   normalization, reagents, kits, instruments, cell lines used as tools).\n"
        "15. Findings the paper attributes to earlier work (a citation, 'previously', 'has been shown') are "
        "   relations too: extract them with source=cited. The paper's own results have source=study.\n"
        "16. Activity predicted by an analysis (upstream regulator or pathway analysis, z-scores, enrichment) "
        "   is not a measured change in amount: choose the relation whose definition covers activity, with "
        "   evidence=predicted; relations about abundance are for measured mRNA or protein levels. The condition "
        "   or treatment the analysis was run on is the subject, and each regulator it lists is its own relation.\n"
        "17. Keep the scope the text gives a finding — time point, group (e.g. healthy vs diseased), cell or "
        "   model system — in condition and tissue. A negated finding without its scope becomes false: if "
        "   the text limits it, the scope is required.\n"
        "18. Expand lists into one relation per member, with full names: 'MMPs 3, 8 and 13' → MMP3, MMP8, "
        "   MMP13; 'IL-6 and IL-8 protein' → IL-6, IL-8. Never return a list or a fragment as one entity.\n"
        "19. A comparison is a directional finding: 'X is higher/more accessible/enriched in A than in B' — "
        "   A is the subject, X the object, B in condition, and the relation is the one whose definition carries "
        "   that direction. Never drop the direction by choosing a relation without one, and never relate two "
        "   cell types or groups because they share a statistic.\n"
        "20. Extract findings — this paper's results and the specific results it cites. Skip textbook "
        "   background: what a disease or cell type is, its usual symptoms, where a molecule is normally found.\n"
        "21. Classify a finding by what was measured, then choose the relation whose definition fits it: an "
        "   enrichment or over-representation is a statistical association, not a change in expression "
        "   (evidence=predicted); something found in a tissue or place is a part–whole relation; a variant that "
        "   disrupts or creates a binding site acts on that factor, with the effect in condition.\n"
    )


def _user_message(chunk: dict) -> str:
    text     = chunk.get("text", "")
    section  = chunk.get("section", "unknown")
    entities = chunk.get("entities", [])
    negated  = chunk.get("negated_entities", [])

    if _MAX_ENTITIES > 0 and len(entities) > _MAX_ENTITIES:
        entities = entities[:_MAX_ENTITIES]

    entity_block = ""
    if entities:
        entity_block = "\n\nPRE-TAGGED ENTITIES FROM LAYER 4:\n" + "\n".join(
            f"  [{e['text']}]  type={e.get('label', '?')}  "
            f"{'[NEGATED — set negated=true for any relation involving this entity]' if e.get('negated') else ''}"
            for e in entities
        )

    negation_block = ""
    if negated:
        negation_block = (
            f"\n\nNEGATION FLAG: Layer 4 detected negation in this chunk. "
            f"The following entities are ABSENT: "
            + ", ".join(f"[{e['text']}]" for e in negated)
            + "\nSet negated=true for any relation involving these entities."
        )

    return (
        f"SECTION: {section}\n\n"
        f"TEXT:\n{text}"
        + entity_block
        + negation_block
        + "\n\nExtract ALL biological relations from the text above — be exhaustive, "
          "do not skip any. Return JSON only."
    )


# ── JSON parser ───────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```\s*$", "", raw)
    return json.loads(raw.strip())


# ── Rejected queue ────────────────────────────────────────────────────────────

def _log_rejected(chunk: dict, error: str, attempts: int) -> None:
    _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "document_id": chunk.get("document_id", ""),
        "section":     chunk.get("section", ""),
        "text":        chunk.get("text", "")[:500],
        "error":       error,
        "attempts":    attempts,
        "model":       _MODEL,
        "base_url":    _BASE_URL,
    }
    with open(_QUEUE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_truncation_error(error: str) -> bool:
    """Return True if the error is a JSON truncation (token limit hit mid-response)."""
    truncation_signals = [
        "Unterminated string", "Expecting ',' delimiter",
        "Expecting property name", "Expecting value", "Extra data",
    ]
    return any(s.lower() in error.lower() for s in truncation_signals)

_MAX_SPLIT_DEPTH = 2           # halves, then quarters


def _valid_relations(raw_relations: list) -> list:
    """Every relation that validates; one bad relation no longer loses the rest.
    Raises the first error when none are valid, so the reply is retried."""
    relations, first_error = [], None
    for r in raw_relations:
        try:
            relations.append(BiologicalRelation(**r))
        except (ValidationError, TypeError) as e:
            first_error = first_error or e
    if raw_relations and not relations and first_error:
        raise first_error
    return relations


def _salvage_relations(raw: str) -> list:
    """The complete relations of a reply cut off at the output limit."""
    start = raw.find("[", raw.find('"relations"'))
    if start < 0:
        return []
    decoder, found, i = json.JSONDecoder(), [], start + 1
    while True:
        while i < len(raw) and raw[i] in " \t\r\n,":
            i += 1
        try:
            obj, i = decoder.raw_decode(raw, i)
        except ValueError:
            break
        if isinstance(obj, dict):
            found.append(obj)
    try:
        return _valid_relations(found)
    except (ValidationError, TypeError):
        return []


def _split_chunk(chunk: dict) -> list:
    """Split a chunk's text in half at a sentence boundary, returning two sub-chunks (with other metadata)."""
    text = chunk.get("text", "")
    sentences = text.split(". ")
    mid = len(sentences) // 2
    half1 = ". ".join(sentences[:mid]) + ("." if sentences[:mid] else "")
    half2 = ". ".join(sentences[mid:])
    base = {k: v for k, v in chunk.items() if k != "text"}
    return [
        {**base, "text": half1, "_sub_chunk": "1/2"},
        {**base, "text": half2, "_sub_chunk": "2/2"},
    ]


# ── Core extraction ───────────────────────────────────────────────────────────

_NEGATION_PASS = os.getenv("NEGATION_PASS", "true").lower() == "true"

_NEGATION_ASK = (
    "List ONLY the negative findings in the text above: statements that an entity did NOT change, "
    "affect, regulate or associate with another (B was unaffected by A; X was not elevated in C; "
    "A had no effect on B). Return them as relations with negated=true, using the same JSON format. "
    "Return an empty relations list if there are none. Return JSON only."
)


# English negation words (grammar, not domain): a negated fact needs one in a sentence naming both entities
_NEGATION_WORDS = re.compile(r"\b(not|no|nor|neither|never|without|absence|absent|lack(?:s|ed|ing)?|fail(?:s|ed)?|"
                             r"un(?:affected|changed|altered|detectable|detected)|insignificant|nonsignificant)\b"
                             r"|n't\b", re.I)


def _text_negates(relation, chunk: dict) -> bool:
    """A sentence naming both entities states a negative finding; a positive finding marked negated is
    dropped. Kept when no sentence names both."""
    subj, obj = relation.subject_name.lower(), relation.object_name.lower()
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", chunk.get("text", "")) if subj in x.lower() and obj in x.lower()]
    return not sentences or any(_NEGATION_WORDS.search(x) for x in sentences)


def _negative_findings(chunk: dict) -> list:
    """A second, small pass for negative findings, which the main pass tends to skip."""
    messages = [{"role": "system", "content": _get_system_prompt()},
                {"role": "user", "content": f"SECTION: {chunk.get('section', 'unknown')}\n\nTEXT:\n"
                                            f"{chunk.get('text', '')}\n\n{_NEGATION_ASK}"}]
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        response = pool.submit(lambda: _client().chat.completions.create(
            model=_MODEL, messages=messages, temperature=_TEMPERATURE, max_tokens=_OUT_RESERVE,
            response_format={"type": "json_object"})).result(timeout=_WALL_TIMEOUT)
        raw = response.choices[0].message.content or ""
        try:
            relations = _valid_relations(_parse_json(raw).get("relations", []))
        except (json.JSONDecodeError, ValidationError, TypeError, AttributeError):
            relations = _salvage_relations(raw)
        return [r for r in relations if r.negated and r.relation and r.extraction_viable
                and _text_negates(r, chunk)]
    except Exception as e:
        logger.warning("Negation pass skipped for a chunk: %s", e)
        return []
    finally:
        pool.shutdown(wait=False)


_MEMBER_LIST = re.compile(r"^([A-Z][A-Z-]+?)(s?)\s+(\d+[A-Za-z]?(?:\s*(?:,|and|&|,\s*and)\s*\d+[A-Za-z]?)*)$")


_SLASH_LIST = re.compile(r"^([A-Z][A-Z0-9-]*?[A-Z-])(\d+[A-Z]?)((?:/\d+[A-Z]?)+)$")


def _members(name: str) -> list:
    """A family with numbered members, one name each: 'MMPs 3, 8 and 13' → ['MMP3', 'MMP8', 'MMP13'];
    'NFKB1/2' → ['NFKB1', 'NFKB2']."""
    slash = _SLASH_LIST.match((name or "").strip())
    if slash:
        return [slash.group(1) + n for n in [slash.group(2)] + slash.group(3).strip("/").split("/")]
    m = _MEMBER_LIST.match((name or "").strip())
    numbers = re.findall(r"\d+[A-Za-z]?", m.group(3)) if m else []
    if not m or not (m.group(2) or len(numbers) > 1):        # 'COVID 19', 'HLA-B 27' stay as written
        return [name]
    return [m.group(1) + n for n in numbers]


def _expand_lists(relations: list) -> list:
    """One relation per member when the model returned a numbered list as one entity."""
    out = []
    for r in relations:
        subs, objs = _members(r.subject_name), _members(r.object_name)
        if len(subs) == 1 and len(objs) == 1:
            out.append(r)
            continue
        out += [r.model_copy(update={"subject_name": s, "object_name": o}) for s in subs for o in objs]
    return out


def extract(chunk: dict, _depth: int = 0) -> ExtractionResult:
    """Extract relations from one chunk, then add the negative findings a second pass finds."""
    result = _extract_main(chunk, _depth)
    # A negation the text never states (no negating word where both entities are named) is the model's error:
    # the finding stands, positive, as the text gives it
    relations = [r.model_copy(update={"negated": False}) if r.negated and not _text_negates(r, chunk) else r
                 for r in _expand_lists(result.relations)]
    result = result.model_copy(update={"relations": relations})
    if _depth or result.rejected or not _NEGATION_PASS:     # after the main pass: the LLM server holds few long requests
        return result
    key = lambda r: (r.subject_name.lower(), r.relation.value if r.relation else "", r.object_name.lower())
    seen = {(*key(r), bool(r.negated)) for r in result.relations}
    extra = [r for r in _negative_findings(chunk)
             if r.relation and (*key(r), True) not in seen]                    # non-viable ones have no relation
    return ExtractionResult(relations=result.relations + extra) if extra else result


def _extract_main(chunk: dict, _depth: int = 0) -> ExtractionResult:
    """
    Extract biological relations from one annotated chunk.

    If the LLM response is truncated (token limit hit mid-JSON), the chunk
    is automatically split in half and each half is extracted separately.
    """
    system  = _get_system_prompt()
    user    = _user_message(chunk)
    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]

    last_error = ""
    last_raw   = ""

    # Health-check once before the retry loop
    _server_root = _BASE_URL.rstrip("/").removesuffix("v1").rstrip("/")
    try:
        httpx.get(f"{_server_root}/health", timeout=_HEALTH_TIMEOUT)
    except Exception:
        try:
            httpx.get(f"{_server_root}/v1/models",
                      headers={"Authorization": f"Bearer {_API_KEY}"}, timeout=_HEALTH_TIMEOUT)
        except Exception as _he:
            raise ConnectionError(f"vLLM server unreachable at {_BASE_URL} — {_he}")

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # Pass messages explicitly to avoid closure-capture-by-reference bug
            def _call(msgs=messages):
                return _client().chat.completions.create(
                    model           = _MODEL,
                    messages        = msgs,
                    temperature     = _TEMPERATURE,
                    max_tokens      = _OUT_RESERVE,
                    response_format = {"type": "json_object"},
                )

            # Wall-clock timeout via ThreadPoolExecutor — fires even when the
            # server sends TCP keepalive bytes (unlike httpx read timeout).
            _pool   = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            _future = _pool.submit(_call)
            try:
                response = _future.result(timeout=_WALL_TIMEOUT)
                last_raw = response.choices[0].message.content or ""
            except concurrent.futures.TimeoutError:
                _pool.shutdown(wait=False)
                raise TimeoutError(
                    f"LLM did not respond within {_WALL_TIMEOUT}s — server overloaded"
                )
            finally:
                _pool.shutdown(wait=False)

            data          = _parse_json(last_raw)
            raw_relations = data.get("relations", [data])
            return ExtractionResult(relations=_valid_relations(raw_relations))

        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            last_error = str(exc)

            if _is_truncation_error(last_error):
                # Keep what the cut-off reply finished, then read the chunk in smaller parts
                all_relations = _salvage_relations(last_raw)
                if _depth < _MAX_SPLIT_DEPTH and ". " in chunk.get("text", ""):
                    for sc in _split_chunk(chunk):
                        all_relations.extend(extract(sc, _depth=_depth + 1).relations)
                seen, unique = set(), []
                for r in all_relations:
                    key = (getattr(r, "subject_name", "").lower(),
                           r.relation.value if r.relation else "",
                           getattr(r, "object_name",  "").lower())
                    if key not in seen:
                        seen.add(key)
                        unique.append(r)
                if unique:
                    return ExtractionResult(relations=unique)

            if attempt < _MAX_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append({
                    "role":    "user",
                    "content": (
                        f"Your output failed validation (attempt {attempt}/{_MAX_RETRIES}).\n"
                        f"Error: {last_error}\n\n"
                        f"Valid relation values: {_RELATION_VALUES}\n"
                        f"Valid entity type values: {_ENTITY_VALUES}\n\n"
                        "Fix the specific field mentioned and return corrected JSON only."
                    ),
                })

        except TimeoutError as exc:
            last_error = str(exc)
            if attempt < _MAX_RETRIES:
                time.sleep(5 * attempt)
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ]
            else:
                break

        except Exception as exc:
            last_error = str(exc)
            break

    _log_rejected(chunk, last_error, _MAX_RETRIES)
    return ExtractionResult(
        relations        = [],
        rejected         = True,
        rejection_reason = last_error,
    )


def extract_batch(chunks: list) -> list:
    """
    Extract relations from a list of annotated chunks in parallel.

    Chunks are processed concurrently up to LLM_CHUNK_CONCURRENCY workers
    (default 4, capped by the global LLM_GLOBAL_CONCURRENCY semaphore).

    Saves a per-chunk progress file so the batch can resume from cached
    results if the pipeline restarts mid-run.

    Progress file: data/checkpoints/{doc_id}_layer6_progress.jsonl
    Delete it to force a full re-extraction.
    """
    import json as _json
    import threading as _threading
    import concurrent.futures as _cf

    _WORKERS = int(os.getenv("LLM_CHUNK_CONCURRENCY", "4"))

    doc_id   = (chunks[0].get("document_id", "") if chunks else "")
    _safe_id = doc_id.replace("/", "_").replace(":", "_") if doc_id else "unknown"
    _ckpt_dir = Path(os.getenv("CHECKPOINTS_DIR", "data/checkpoints"))
    _ckpt_dir.mkdir(parents=True, exist_ok=True)
    _progress_path = _ckpt_dir / f"{_safe_id}_layer6_progress.jsonl"
    _file_lock = _threading.Lock()
    _completed: dict = {}
    if _progress_path.exists():
        try:
            for line in _progress_path.read_text(encoding="utf-8").splitlines():
                entry = _json.loads(line)
                _completed[entry["chunk_index"]] = entry["result"]
        except Exception:
            pass

    def _process_one(args):
        i, chunk = args
        chunk_index = chunk.get("chunk_index", i)

        if chunk_index in _completed:
            cached    = _completed[chunk_index]
            relations = [BiologicalRelation(**r) for r in cached.get("relations", [])]
            return i, ExtractionResult(
                relations=relations,
                rejected=cached.get("rejected", False),
                rejection_reason=cached.get("rejection_reason"),
            )

        result = extract(chunk)

        try:
            entry = {
                "chunk_index": chunk_index,
                "result": {
                    "relations":        [r.model_dump() for r in result.relations],
                    "rejected":         result.rejected,
                    "rejection_reason": result.rejection_reason,
                }
            }
            with _file_lock:
                with open(_progress_path, "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(entry) + "\n")
        except Exception:
            pass

        return i, result

    indexed_results = []
    with _cf.ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for i, result in pool.map(_process_one, enumerate(chunks)):
            indexed_results.append((i, result))

    indexed_results.sort(key=lambda x: x[0])
    results = [r for _, r in indexed_results]

    try:
        _progress_path.unlink(missing_ok=True)
    except Exception:
        pass

    return results
