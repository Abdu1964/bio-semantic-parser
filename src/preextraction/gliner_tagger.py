"""GLiNER-BioMed Base — zero-shot NER supplement to the scispaCy ensemble."""
from __future__ import annotations
import os
import threading as _threading
from typing import Optional

# Zero-shot labels for GLiNER — natural-language descriptions it matches against directly.
GLINER_LABELS = [
    "gene", "protein", "RNA transcript or mRNA isoform", "exon",
    "non-coding RNA such as miRNA lncRNA or circRNA",
    "genomic variant or SNP", "sequence variant or point mutation",
    "structural variant such as CNV deletion or translocation",
    "regulatory region", "enhancer element", "super-enhancer",
    "gene promoter", "transcription factor binding site",
    "epigenomic feature such as histone mark or methylation",
    "sequence motif or transcription factor motif",
    "topologically associating domain TAD",
    "small molecule drug or metabolite",
    "molecular interaction or protein-protein interaction",
    "macromolecular complex",
    "haplotype", "genotype",
    "disease", "cancer or tumour type",
    "phenotype", "clinical symptom",
    "biological pathway",
    "biochemical reaction",
    "biological process",
    "molecular function",
    "cellular component or organelle",
    "anatomical structure",
    "tissue type",
    "cell type",
    "cell line",
    "developmental stage or life stage",
    "experimental condition or treatment",
    "3D genome structure or chromatin loop",
    "organism or species",
]

GLINER_MAP = {
    "gene": "GENE",
    "protein": "PROTEIN",
    "RNA transcript or mRNA isoform": "TRANSCRIPT",
    "exon": "EXON",
    "non-coding RNA such as miRNA lncRNA or circRNA": "NON_CODING_RNA",
    "genomic variant or SNP": "GENOMIC_VARIANT",
    "sequence variant or point mutation": "SEQUENCE_VARIANT",
    "structural variant such as CNV deletion or translocation": "STRUCTURAL_VARIANT",
    "regulatory region": "REGULATORY_REGION",
    "enhancer element": "ENHANCER",
    "super-enhancer": "SUPER_ENHANCER",
    "gene promoter": "PROMOTER",
    "transcription factor binding site": "TRANSCRIPTION_FACTOR_BINDING_SITE",
    "epigenomic feature such as histone mark or methylation": "EPIGENOMIC_FEATURE",
    "sequence motif or transcription factor motif": "MOTIF",
    "topologically associating domain TAD": "TAD",
    "small molecule drug or metabolite": "SMALL_MOLECULE",
    "molecular interaction or protein-protein interaction": "MOLECULAR_INTERACTION",
    "macromolecular complex": "MACROMOLECULAR_COMPLEX",
    "haplotype": "HAPLOTYPE",
    "genotype": "GENOTYPE",
    "disease": "DISEASE",
    "cancer or tumour type": "CANCER",
    "phenotype": "PHENOTYPE",
    "clinical symptom": "SYMPTOM",
    "biological pathway": "PATHWAY",
    "biochemical reaction": "REACTION",
    "biological process": "BIOLOGICAL_PROCESS",
    "molecular function": "MOLECULAR_FUNCTION",
    "cellular component or organelle": "CELLULAR_COMPONENT",
    "anatomical structure": "ANATOMY",
    "tissue type": "TISSUE",
    "cell type": "CELL_TYPE",
    "cell line": "CELL_LINE",
    "developmental stage or life stage": "DEVELOPMENTAL_STAGE",
    "experimental condition or treatment": "EXPERIMENTAL_FACTOR",
    "3D genome structure or chromatin loop": "THREE_D_GENOME_STRUCTURE",
    "organism or species": "ORGANISM",
}

_model: Optional[object] = None
_model_id: str = ""
_model_lock = _threading.Lock()


def _get_model():
    """Lazy-load GLiNER-BioMed Base (cached after first call)."""
    global _model, _model_id
    model_id = os.getenv("GLINER_MODEL", "Ihor/gliner-biomed-base-v1.0")
    if _model is None or _model_id != model_id:
        import torch
        from gliner import GLiNER

        model = GLiNER.from_pretrained(model_id)
        if torch.cuda.is_available():
            model = model.to("cuda")

        with _model_lock:
            _model    = model
            _model_id = model_id
    return _model


def should_run() -> bool:
    return os.getenv("GLINER_ENABLED", "true").lower() == "true"


def tag_entities(text: str) -> list[dict]:
    """Run GLiNER zero-shot NER; returns entity dicts in NERTagger.from_doc() format."""
    if not should_run():
        return []

    threshold = float(os.getenv("GLINER_THRESHOLD", "0.5"))

    try:
        model   = _get_model()
        results = model.predict_entities(
            text[:8000], GLINER_LABELS, threshold=threshold, flat_ner=True
        )
    except Exception:
        return []

    entities = []
    seen     = set()

    for r in results:
        word  = (r.get("text") or "").strip()
        label = r.get("label", "")
        score = float(r.get("score", 0.0))

        if not word or len(word) < 3:
            continue

        entity_type = GLINER_MAP.get(label, "OTHER")
        if entity_type == "OTHER":
            continue   # skip unmapped labels

        start = int(r.get("start", -1))
        end   = int(r.get("end", -1))
        if start < 0 or end <= start:
            continue

        key = word.lower()
        if key in seen:
            continue
        seen.add(key)

        entities.append({
            "text":       word,
            "normalized": key,
            "label":      entity_type,
            "start":      start,
            "end":        end,
            "negated":    False,
            "assertion":  "PRESENT",
            "confidence": round(score, 3),
            "source":     "gliner",
        })

    return entities
