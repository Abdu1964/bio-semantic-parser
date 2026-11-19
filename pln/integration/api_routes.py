"""PLN API: /api/pln-run, /api/pln-query, /api/pln-facts, /api/pln-inferred, /api/run_pln (legacy)."""
import os
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()

# Module-level PLNModule instance (lazy-initialized per process)
_pln_module = None
# Unified atomspace — loaded from all committed papers, kept alive for queries
_unified_pln_module = None


def _get_pln():
    global _pln_module
    if _pln_module is None:
        from pln import PLNModule
        _pln_module = PLNModule.from_env()
    return _pln_module


def _paper_pln():
    """A fresh atomspace for one paper, so papers never mix; later queries use it."""
    global _pln_module
    from pln import PLNModule
    _pln_module = PLNModule.from_env()
    return _pln_module


_TS_PREFIX_RE = __import__('re').compile(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_')


def _latest_run_dirs(kg_root: Path) -> list[Path]:
    """Return one run dir per unique paper (latest timestamp wins) that has edge MeTTa files."""
    best: dict[str, Path] = {}
    for run_dir in sorted(kg_root.iterdir()):
        if run_dir.name in ("unified_metta", "unified_neo4j"):
            continue
        metta_dir = run_dir / "metta"
        if not metta_dir.exists() or not any(metta_dir.rglob("edges_*.metta")):
            continue
        paper_key = _TS_PREFIX_RE.sub('', run_dir.name) or run_dir.name
        # sorted() gives chronological order → later entry wins
        best[paper_key] = run_dir
    return sorted(best.values())


def _get_unified_pln(force_reload: bool = False):
    """Return the unified PLN module, lazy-loading one run dir per unique paper."""
    global _unified_pln_module
    if _unified_pln_module is not None and not force_reload:
        return _unified_pln_module
    from pln import PLNModule
    from pln.config import PLNConfig
    cfg = PLNConfig.from_env()
    if not cfg.enabled:
        return None
    pln = PLNModule(cfg)
    kg_root = Path("data/kg_output")
    if kg_root.exists():
        for run_dir in _latest_run_dirs(kg_root):
            pln.load_paper(run_dir=run_dir)
    _unified_pln_module = pln
    return _unified_pln_module


class PLNRunRequest(BaseModel):
    run_dir:    str
    staging_db: str | None = None
    paper_id:   str = ''


class PLNQueryRequest(BaseModel):
    question:   str
    run_dir:    str | None = None
    staging_db: str | None = None


@router.post("/api/pln-run")
async def pln_run(req: PLNRunRequest):
    """Load a paper into PLN, run forward chaining, generate vis.js graph HTML."""
    try:
        pln = _paper_pln()
        if not pln.enabled:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        n_atoms = pln.load_paper(run_dir=req.run_dir, staging_db=req.staging_db)
        if n_atoms == 0:
            return {"ok": False, "error": "No atoms loaded — check run_dir / staging_db path"}

        html_path = pln.generate_visualization(
            run_dir=req.run_dir,
            paper_id=req.paper_id or Path(req.run_dir).name,
        )
        return {
            "ok":          True,
            "atoms_loaded": n_atoms,
            "inferred":    len(pln.inferred()),
            "pln_html":    str(html_path) if html_path else "",
        }
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:600]}


@router.post("/api/pln-query")
async def pln_query(req: PLNQueryRequest):
    """Answer a NL question over the KG using PLN forward+backward chaining."""
    try:
        pln = _paper_pln() if (req.run_dir or req.staging_db) else _get_pln()
        if not pln.enabled:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        if req.run_dir or req.staging_db:
            pln.load_paper(run_dir=req.run_dir or "", staging_db=req.staging_db)

        result = pln.ask(req.question)

        from pln.query.answer_generator import generate_json
        return {"ok": True, **generate_json(result)}
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:500]}


@router.get("/api/pln-facts")
async def pln_facts(limit: int = Query(default=200, le=2000)):
    """Return all atoms currently in the PLN atomspace."""
    try:
        pln = _get_pln()
        if not pln.enabled:
            return {"ok": False, "error": "PLN disabled"}
        facts = pln.all_facts()[:limit]
        return {"ok": True, "count": len(facts), "facts": facts}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/pln-inferred")
async def pln_inferred(
    run_dir: str = Query(default=""),
    limit:   int = Query(default=200, le=2000),
):
    """Return inferred-only atoms (derived by PLN rules, not from raw triples)."""
    try:
        pln = _get_pln()
        if not pln.enabled:
            return {"ok": False, "error": "PLN disabled"}
        p = Path(run_dir) / "pln_inferred.json" if run_dir else None
        if p and p.exists():
            import json
            data = json.loads(p.read_text())
            atoms = data.get("atoms", [])[:limit]
            return {"ok": True, "count": len(atoms), "atoms": atoms, "source": "file"}
        facts = pln.inferred()[:limit]                         # from the live atomspace
        return {"ok": True, "count": len(facts), "atoms": facts, "source": "live"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/pln-papers")
async def pln_papers():
    """List all papers that have a PLN graph HTML file."""
    kg_root = Path("data/kg_output")
    papers = []
    if kg_root.exists():
        for run_dir in sorted(kg_root.iterdir()):
            if run_dir.name in ("unified_metta", "unified_neo4j"):
                continue
            html = run_dir / "pln_graph.html"
            if html.exists():
                papers.append({
                    "id": run_dir.name,
                    "path": str(html),
                    "size_bytes": html.stat().st_size,
                })
    return {"ok": True, "papers": papers}


_UNIFIED_PLN_DIR = Path("data/kg_output/unified_pln")


@router.get("/api/pln-unified-status")
async def pln_unified_status():
    """Check if unified PeTTaChainer PLN graph exists; return path + basic stats."""
    pln_html = _UNIFIED_PLN_DIR / "pln_atomspace.html"
    if pln_html.exists():
        metta    = _UNIFIED_PLN_DIR / "pln_atomspace.metta"
        manifest = _UNIFIED_PLN_DIR / "manifest.json"
        return {
            "ok": True, "exists": True,
            "path": str(pln_html), "size_bytes": pln_html.stat().st_size,
            "metta_files": {
                "full":     str(metta) if metta.exists() else None,
                "inferred": str(_UNIFIED_PLN_DIR / "pln_inferred.metta") if (_UNIFIED_PLN_DIR / "pln_inferred.metta").exists() else None,
                "original": str(_UNIFIED_PLN_DIR / "pln_original.metta") if (_UNIFIED_PLN_DIR / "pln_original.metta").exists() else None,
                "manifest": str(manifest) if manifest.exists() else None,
            },
        }
    return {"ok": True, "exists": False}


@router.post("/api/pln-unified-run")
async def pln_unified_run():
    """Load ALL committed MeTTa files, run PeTTaChainer forward chaining, generate unified PLN graph."""
    global _unified_pln_module
    try:
        from pln.config import PLNConfig
        cfg = PLNConfig.from_env()
        if not cfg.enabled:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        pln = _get_unified_pln(force_reload=True)
        if pln is None:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        total_atoms = len(pln.all_facts())
        kg_root = Path("data/kg_output")
        loaded_papers = [d.name for d in _latest_run_dirs(kg_root)] if kg_root.exists() else []

        if total_atoms == 0:
            return {"ok": False, "error": "No MeTTa atoms found in data/kg_output/ — run papers through Layer 8 first"}

        # All PLN output goes to unified_pln/ — HTML + flat metta + per-category metta/
        _UNIFIED_PLN_DIR.mkdir(parents=True, exist_ok=True)

        pln.generate_visualization(
            run_dir=str(_UNIFIED_PLN_DIR),
            paper_id=f"Unified ({len(loaded_papers)} papers)",
        )
        # generate_visualization writes pln_graph.html; rename to pln_atomspace.html
        pln_html    = _UNIFIED_PLN_DIR / "pln_atomspace.html"
        default_html = _UNIFIED_PLN_DIR / "pln_graph.html"
        if default_html.exists():
            import shutil
            shutil.move(str(default_html), str(pln_html))

        inferred  = len(pln.inferred())
        metta     = _UNIFIED_PLN_DIR / "pln_atomspace.metta"
        return {
            "ok":           True,
            "papers":       len(loaded_papers),
            "atoms_loaded": total_atoms,
            "inferred":     inferred,
            "pln_html":     str(pln_html) if pln_html.exists() else "",
            "pln_metta":    str(metta) if metta.exists() else "",
        }
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:600]}


@router.post("/api/pln-unified-query")
async def pln_unified_query(req: PLNQueryRequest):
    """Answer a NL question against the full unified atomspace (all committed papers)."""
    try:
        pln = _get_unified_pln()
        if pln is None:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        result = pln.ask(req.question)
        from pln.query.answer_generator import generate_json
        return {"ok": True, **generate_json(result)}
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:500]}


@router.post("/api/run_pln")
async def run_pln_endpoint():
    """Legacy — Layer 9 STV + contradiction analysis via layer9_pln."""
    try:
        os.environ.setdefault("TRIPLE_STORE_PATH", "data/triple_store.db")

        from pln.legacy.layer9_pln import run_pln, write_pln_updates
        from tools.visualize_pln import generate_pln_html

        kg_root  = Path("data/kg_output")
        pln_out  = kg_root / "unified_metta"

        ver_json      = pln_out / "verification_report.json"
        verifications = []
        if ver_json.exists():
            import json as _j
            try: verifications = _j.loads(ver_json.read_text())
            except Exception: pass

        result = run_pln(fmt="neo4j", verifications=verifications, verbose=False)
        write_pln_updates(result, pln_out / "pln_updates.metta")

        pln_html = pln_out / "pln_graph.html"
        generate_pln_html(result, pln_html)

        return {
            "ok":             True,
            "revised":        result.get("revised", 0),
            "inferred":       len(result.get("derived", [])),
            "contradictions": len(result.get("contradictions", [])),
            "pln_html":       str(pln_html) if pln_html.exists() else "",
        }
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:400]}
