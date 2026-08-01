"""Routes: Source grounding — find original text supporting a relation."""
import json
import re
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

router = APIRouter()


class GroundingRequest(BaseModel):
    doc_id: str
    subject_name: str
    object_name: str
    relation: Optional[str] = None


def _find_entity_spans(text: str, entity_name: str) -> list[dict]:
    """Find all character-offset spans of entity_name and its aliases in text (case-insensitive)."""
    if not text or not entity_name:
        return []
    aliases = {entity_name.strip()}
    if "(" in entity_name and ")" in entity_name:
        clean = re.sub(r'\s*\([^)]+\)', '', entity_name).strip()
        if len(clean) > 2:
            aliases.add(clean)
        for abbrev in re.findall(r'\(([^)]+)\)', entity_name):
            if len(abbrev.strip()) > 2:
                aliases.add(abbrev.strip())
    
    sorted_aliases = sorted([a for a in aliases if a], key=len, reverse=True)
    spans = []
    lower_text = text.lower()
    for alias in sorted_aliases:
        lower_alias = alias.lower()
        start = 0
        while True:
            idx = lower_text.find(lower_alias, start)
            if idx == -1:
                break
            end = idx + len(alias)
            if not any(s["start"] < end and s["end"] > idx for s in spans):
                spans.append({"start": idx, "end": end})
            start = idx + 1
    spans.sort(key=lambda s: s["start"])
    return spans


def _find_checkpoint_dir(doc_id: str) -> Optional[Path]:
    """Find the checkpoint directory for a document ID, checking direct name, case-insensitive name, and chunk metadata."""
    if not doc_id or ".." in doc_id or "/" in doc_id or "\\" in doc_id:
        return None
    checkpoints_root = _ROOT / "data" / "checkpoints"
    if not checkpoints_root.exists():
        return None
    # 1. Direct match
    direct = checkpoints_root / doc_id
    if direct.exists() and direct.is_dir():
        return direct
    # 2. Case-insensitive folder match
    for d in checkpoints_root.iterdir():
        if d.is_dir() and d.name.lower() == doc_id.lower():
            return d
    # 3. Scan checkpoint metadata (e.g. PMC ID stored inside PMID folder)
    for d in checkpoints_root.iterdir():
        if not d.is_dir():
            continue
        for fname in ("layer4_annotated.json", "layer3_chunks.json", "layer7_processed.json"):
            fpath = d / fname
            if fpath.exists():
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list) and data and isinstance(data[0], dict):
                            first = data[0]
                            doc_val = str(first.get("document_id", ""))
                            src_url = str(first.get("source_url", ""))
                            src_name = str(first.get("source_name", ""))
                            if doc_val.lower() == doc_id.lower() or doc_id.lower() in src_url.lower() or doc_id.lower() in src_name.lower():
                                return d
                except Exception:
                    pass
    return None


@router.post("/api/source-grounding")
async def source_grounding(req: GroundingRequest):
    """Find text chunks supporting a relation and highlight entity spans."""
    checkpoint_dir = _find_checkpoint_dir(req.doc_id)

    if not checkpoint_dir or not checkpoint_dir.exists():
        return JSONResponse(
            {"error": f"No checkpoint data for doc_id '{req.doc_id}'"},
            status_code=404,
        )

    # Load chunks (prefer annotated with NER, fall back to raw)
    annotated_path = checkpoint_dir / "layer4_annotated.json"
    chunks_path = checkpoint_dir / "layer3_chunks.json"
    chunks_file = annotated_path if annotated_path.exists() else chunks_path

    if not chunks_file.exists():
        return JSONResponse(
            {"error": f"No chunk data found for '{req.doc_id}'"},
            status_code=404,
        )

    try:
        chunks = json.loads(chunks_file.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse({"error": f"Failed to load chunks: {e}"}, status_code=500)

    source_url = ""
    matched_chunks = []

    target_chunk_indices = set()
    for layer_name in ("layer7_processed.json", "layer6_results.json", "layer5_relations.json"):
        lpath = checkpoint_dir / layer_name
        if lpath.exists():
            try:
                records = json.loads(lpath.read_text(encoding="utf-8"))
                for r in records:
                    s_name = str(r.get("subject_name", "")).lower()
                    o_name = str(r.get("object_name", "")).lower()
                    rel = str(r.get("relation", "")).lower()
                    s_match = (s_name == req.subject_name.lower() or req.subject_name.lower() in s_name or s_name in req.subject_name.lower())
                    o_match = (o_name == req.object_name.lower() or req.object_name.lower() in o_name or o_name in req.object_name.lower())
                    if s_match and o_match:
                        if not req.relation or rel == req.relation.lower():
                            if r.get("chunk_index") is not None:
                                target_chunk_indices.add(r["chunk_index"])
            except Exception:
                pass
            if target_chunk_indices:
                break

    for chunk in chunks:
        text = chunk.get("text", "")
        if not text:
            continue

        if not source_url:
            source_url = chunk.get("source_url", "")

        if target_chunk_indices and chunk.get("chunk_index") not in target_chunk_indices:
            continue

        # Find subject and object mentions in this chunk
        subject_spans = _find_entity_spans(text, req.subject_name)
        object_spans = _find_entity_spans(text, req.object_name)

        if subject_spans or object_spans:
            # Build sentence-grounded highlighted HTML
            highlighted, has_both = _build_sentence_highlighted_text(
                text, req.subject_name, req.object_name
            )
            matched_chunks.append({
                "text": text,
                "section": chunk.get("section", "unknown"),
                "chunk_index": chunk.get("chunk_index", 0),
                "subject_spans": subject_spans,
                "object_spans": object_spans,
                "highlighted_html": highlighted,
                "has_both": has_both,
            })

    # Sort: chunks containing both subject and object first, then by section order
    section_order = {"abstract": 0, "introduction": 1, "methods": 2,
                     "results": 3, "discussion": 4, "conclusion": 5}
    matched_chunks.sort(
        key=lambda c: (
            not c["has_both"],
            section_order.get(c["section"], 99),
            c["chunk_index"],
        )
    )

    return JSONResponse({
        "doc_id": req.doc_id,
        "source_url": source_url,
        "chunks": matched_chunks,
        "total_chunks": len(chunks),
        "matched_count": len(matched_chunks),
    })


def _build_sentence_highlighted_text(
    text: str,
    subject_name: str,
    object_name: str,
) -> tuple[str, bool]:
    """Identify the exact sentence(s) where the relation was extracted and highlight only those."""
    sents = list(re.finditer(r'[^.!?]+(?:[.!?]+(?:\s+|$)|\s*$)', text))
    if not sents:
        sents = [re.match(r'^.*$', text, re.DOTALL)]

    joint_sents = [
        m for m in sents
        if m and _find_entity_spans(m.group(), subject_name) and _find_entity_spans(m.group(), object_name)
    ]
    source_sents = joint_sents if joint_sents else [
        m for m in sents
        if m and (_find_entity_spans(m.group(), subject_name) or _find_entity_spans(m.group(), object_name))
    ]
    source_starts = {m.start() for m in source_sents if m}

    parts = []
    last_end = 0
    for m in sents:
        if not m:
            continue
        if m.start() > last_end:
            parts.append(_escape_html(text[last_end:m.start()]))
        st = m.group()
        if m.start() in source_starts:
            s_spans = _find_entity_spans(st, subject_name)
            o_spans = _find_entity_spans(st, object_name)
            sh = _build_highlighted_text(st, s_spans, o_spans)
            parts.append(f'<span class="sg-source-sentence">{sh}</span>')
        else:
            parts.append(_escape_html(st))
        last_end = m.end()

    if last_end < len(text):
        parts.append(_escape_html(text[last_end:]))

    return "".join(parts), len(joint_sents) > 0


def _build_highlighted_text(
    text: str,
    subject_spans: list[dict],
    object_spans: list[dict],
) -> str:
    """Build HTML with subject/object names highlighted inline."""
    # Merge all spans and sort by start position
    all_spans = []
    for s in subject_spans:
        all_spans.append({**s, "type": "subject"})
    for s in object_spans:
        all_spans.append({**s, "type": "object"})
    all_spans.sort(key=lambda s: s["start"])

    if not all_spans:
        return _escape_html(text)

    parts = []
    last_end = 0
    for span in all_spans:
        # Add text before this span
        if span["start"] > last_end:
            parts.append(_escape_html(text[last_end:span["start"]]))

        # Add highlighted span
        chunk_text = _escape_html(text[span["start"]:span["end"]])
        if span["type"] == "subject":
            parts.append(
                f'<span class="sg-subject">{chunk_text}</span>'
            )
        else:
            parts.append(
                f'<span class="sg-object">{chunk_text}</span>'
            )
        last_end = span["end"]

    # Add remaining text
    if last_end < len(text):
        parts.append(_escape_html(text[last_end:]))

    return "".join(parts)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
