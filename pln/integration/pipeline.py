"""PLN entry points: run() — legacy Layer 9 STV and contradictions; run_v2() — PeTTaChainer
pipeline (ENABLE_PLN=true)."""
from __future__ import annotations
from pathlib import Path


def run(
    db_path: Path | str | None = None,
    out_dir: Path | str | None = None,
    fmt:     str  = "neo4j",
) -> dict | None:
    """Run PLN reasoning and write stv MeTTa files + graph HTML."""
    from pln.legacy.layer9_pln       import run_pln, write_pln_updates
    from tools.visualize_pln      import generate_pln_html

    out_dir = Path(out_dir) if out_dir else Path("data/kg_output")
    db_path = Path(db_path) if db_path else None

    pln_result = run_pln(
        fmt     = fmt,
        verbose = False,
        db_path = str(db_path) if db_path else None,
    )

    # pln/ subfolder inside the run dir
    pln_dir      = out_dir / "pln"
    pln_dir.mkdir(parents=True, exist_ok=True)
    html_path    = pln_dir / "pln_graph.html"

    generate_pln_html(pln_result, html_path)

    # write_pln_updates creates its own pln/ inside out_path.parent
    write_pln_updates(pln_result, out_path=out_dir / "pln_updates.metta")

    return {
        "stv_all":        pln_result.get("stv_all",        []),
        "derived":        pln_result.get("derived",        []),
        "contradictions": pln_result.get("contradictions", []),
        "revised":        pln_result.get("revised",        0),
        "html_path":      html_path,
        "metta_dir":      pln_dir,
    }


def run_v2(
    run_dir:    Path | str | None = None,
    staging_db: Path | str | None = None,
    question:   str | None = None,
) -> dict:
    """PeTTaChainer-based PLN pipeline. Gated by ENABLE_PLN=true in .env."""
    from pln import PLNModule
    pln = PLNModule.from_env()
    if not pln.enabled:
        return {"enabled": False, "answers": [], "facts": 0}
    n = pln.load_paper(run_dir=run_dir or "", staging_db=staging_db)
    result = pln.ask(question) if question else None
    if run_dir:
        pln.save_inferred(run_dir)
    return {
        "enabled": True,
        "facts":   n,
        "answers": result.to_dict() if result else {},
        "config":  pln.config_dict(),
    }
