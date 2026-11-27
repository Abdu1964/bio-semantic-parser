"""PLN in the pipeline (ENABLE_PLN=true): Layer 8 loads the paper's facts into a PeTTaChainer atomspace,
forward-chains with the schema and learned rules, and writes the paper's PLN page to run/pln/."""
from __future__ import annotations
from pathlib import Path


def run(
    db_path: Path | str | None = None,
    out_dir: Path | str | None = None,
    fmt:     str  = "neo4j",
) -> dict | None:
    """The paper's PLN: facts, inferred triples and the page in the KG viewer; None when off or empty."""
    from pln import PLNModule
    from pln.integration.api_routes import _pln_lock, _write_pln_page   # one PLN job at a time

    run_dir = Path(out_dir) if out_dir else None
    if run_dir is None:
        return None
    with _pln_lock:
        pln = PLNModule.from_env()
        if not pln.enabled:
            return None
        if not pln.load_paper(run_dir=run_dir, staging_db=[str(db_path)] if db_path else None):
            return None
        html = run_dir / "pln" / "pln_graph.html"
        html.parent.mkdir(parents=True, exist_ok=True)
        _write_pln_page(pln, [run_dir], html)            # same viewer, filters and audit drawer as all papers
        inferred = pln.inferred()
        pln.save_inferred(run_dir)
        return {
            "stv_all":        list(pln._original_atoms),  # the paper's facts with their truth values
            "derived":        inferred,                   # what the rules add
            "contradictions": [],
            "revised":        0,
            "html_path":      html,
            "metta_dir":      html.parent,
        }
