"""PLN API: /api/pln-run, /api/pln-query, /api/pln-facts, /api/pln-inferred, /api/run_pln (legacy)."""
import functools
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()

# Module-level PLNModule instance (lazy-initialized per process)
_pln_module = None
# Unified atomspace — loaded from all committed papers, kept alive for queries
_unified_pln_module = None
_pln_lock = threading.Lock()


def _serial(fn):
    """PLN routes run in worker threads, so the rest of the API keeps answering; PeTTa is not
    thread-safe, so one PLN job at a time."""
    @functools.wraps(fn)
    def run(*a, **k):
        with _pln_lock:
            return fn(*a, **k)
    return run


def _get_pln():
    global _pln_module
    if _pln_module is None:
        from pln import PLNModule
        _pln_module = PLNModule.from_env()
    return _pln_module


_paper_key = None


def _paper_state(run_dir: str | None, staging_db: str | None) -> tuple:
    """A paper and the last change to its facts; a new value means its atomspace must be rebuilt."""
    stamps = []
    for p in (staging_db, Path(run_dir) / "metta" if run_dir else None):
        try:
            stamps.append(os.path.getmtime(p) if p else 0)
        except OSError:
            stamps.append(0)
    return (run_dir or "", staging_db or "", *stamps)


def _paper_pln(run_dir: str | None = None, staging_db: str | None = None):
    """(atomspace, fresh) for one paper, so papers never mix. Asking about the same unchanged
    paper again reuses it, so forward chaining runs once per paper."""
    global _pln_module, _paper_key
    key = _paper_state(run_dir, staging_db)
    if _pln_module is not None and key == _paper_key:
        return _pln_module, False
    from pln import PLNModule
    _pln_module, _paper_key = PLNModule.from_env(), key
    return _pln_module, True


_TS_PREFIX_RE = __import__('re').compile(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_')


def _latest_run_dirs(kg_root: Path) -> list[Path]:
    """Return one run dir per unique paper (latest timestamp wins) that has edge MeTTa files."""
    best: dict[str, Path] = {}
    for run_dir in sorted(kg_root.iterdir()):
        if run_dir.name.startswith("unified_"):                  # merged outputs, not papers
            continue
        metta_dir = run_dir / "metta"
        if not metta_dir.exists() or not any(metta_dir.rglob("edges_*.metta")):
            continue
        paper_key = _TS_PREFIX_RE.sub('', run_dir.name) or run_dir.name
        # sorted() gives chronological order → later entry wins
        best[paper_key] = run_dir
    return sorted(best.values())


_unified_state: dict[str, tuple] = {}         # paper → (run folder, last change to its facts) as loaded
_unified_sync: dict = {}                       # what the last sync did


def _doc_ids(run_dir: Path) -> list[str]:
    """Document IDs a run's facts name in their (source …) lines; staging DBs are named after them."""
    found = []
    for f in (run_dir / "metta").rglob("edges_*.metta"):
        for m in re.finditer(r'^\(source \(.*\) (\S+)\)$', f.read_text(encoding="utf-8"), re.M):
            if m.group(1) not in found:
                found.append(m.group(1))
    return found


def _facts_stamp(run_dir: Path) -> tuple:
    """A run folder and the last change to its fact files."""
    stamps = [f.stat().st_mtime for f in (run_dir / "metta").rglob("edges_*.metta")]
    return (run_dir.name, max(stamps, default=0), len(stamps))


def _current_state() -> dict:
    """paper → (run folder, stamp of its facts) on disk, and the triple store's stamp; read from files only."""
    kg_root = Path("data/kg_output")
    current = {}
    for d in (_latest_run_dirs(kg_root) if kg_root.exists() else []):
        current[_TS_PREFIX_RE.sub('', d.name) or d.name] = (d, _facts_stamp(d))
    from src.postextraction.deduplication import db_path_for_format
    store = db_path_for_format("metta")
    current["(triple store)"] = (None, (store.name, store.stat().st_mtime if store.exists() else 0, 0))
    return current


def _get_unified_pln(force_reload: bool = False):
    """The all-papers atomspace. Each call loads only papers it does not hold yet; a paper whose facts
    changed or that is gone means a rebuild, since atoms cannot be taken back out one by one."""
    global _unified_pln_module, _unified_state, _unified_sync
    from pln import PLNModule
    from pln.config import PLNConfig
    cfg = PLNConfig.from_env()
    if not cfg.enabled:
        return None
    current = _current_state()
    from src.postextraction.deduplication import db_path_for_format
    store = db_path_for_format("metta")
    changed = [k for k, st in _unified_state.items() if k not in current or current[k][1] != st]
    if changed == ["(triple store)"] and any(k not in _unified_state and current[k][0] is not None for k in current):
        changed = []        # a commit of new papers: load them; facts they share keep their loaded confidence
    rebuilt = ''
    if force_reload or changed or _unified_pln_module is None:
        rebuilt = ('requested' if force_reload else
                   f"{len(changed)} paper(s) changed or removed: {', '.join(changed[:5])}" if changed else 'first load')
        _unified_pln_module, _unified_state = PLNModule(cfg), {}
    added = [k for k in current if k not in _unified_state and current[k][0] is not None]
    _unified_state["(triple store)"] = current["(triple store)"][1]       # truth values read from it
    t0 = time.perf_counter()
    for k in added:
        # truth values: strength from the paper's own staging DB, paper count from the unified store
        dbs = [db for doc in _doc_ids(current[k][0])
               for db in sorted(Path("data/staging").glob(f"{doc.replace('/', '_').replace(':', '_')}_*.db"))] + [store]
        _unified_pln_module.load_paper(run_dir=current[k][0], staging_db=[str(d) for d in dbs])
        _unified_state[k] = current[k][1]
    if rebuilt:
        _restore_found(_unified_pln_module, _UNIFIED_PLN_DIR / "pln_backward.json", sorted(_unified_state.items()))
    _unified_sync = {'papers': len(_unified_state) - 1, 'added': added, 'rebuilt': rebuilt,
                     'ms': round((time.perf_counter() - t0) * 1000)}
    return _unified_pln_module


def _write_pln_page(pln, run_dirs: list[Path], out: Path) -> None:
    """The all-papers page in the Unified KG viewer: each paper's facts, plus the triples PLN inferred
    (purple dashed), each shown with a paper that names its subject (else its object)."""
    from tools.visualize_graph import build_graph_data, render_html
    from pln.chaining.forward_chainer import _parse_result, _proof_parts
    from pln.bridge.metta_adapter import _id_key
    nodes, edges = build_graph_data([d / "neo4j" for d in run_dirs])
    by_key = {_id_key(n): n for n in nodes}
    paper_of: dict = {}
    for e in edges:
        for side in ("source_id", "target_id"):
            paper_of.setdefault(e[side], e["_paper"])
    seen = {(e["source_id"], e["relation"], e["target_id"]) for e in edges}
    derivations = pln._forward.derivations()
    backward = {tuple((_parse_result(v["atom"]) or [None] * 4)[1:4]): v for v in pln.backward_found().values()}
    meaning = {r["name"]: r.get("meaning", "") for r in pln._forward.rule_info()}
    name_of = lambda key: nodes.get(by_key.get(key, ""), {}).get("name") or key
    papers_of = lambda fact: sorted({_TS_PREFIX_RE.sub('', p) for p in pln._fact_papers.get(fact, [])})

    inferred = pln.inferred()
    by_proof = {}                                   # a derived premise's proof → its own statement and STV
    for atom in inferred:
        r = _parse_result(atom)
        if r and (derivations.get(r[0]) or {}).get("proof"):
            by_proof[derivations[r[0]]["proof"]] = (f"{r[2]} —[{r[1]}]→ {r[3]}", f"(stv {float(r[4]):.2f} {float(r[5]):.2f})")

    def premise(fact: str, label: str = "", stv: str = "") -> dict:
        """One premise for the audit table: the statement with entity names, its STV and the papers stating it;
        a premise PLN inferred in an earlier step is shown as that inferred statement."""
        derived = fact in by_proof and (not label or label == "derived relation")
        if derived:
            label, stv = by_proof[fact]
        m = re.match(r"(\S+) —\[(\w+)\]→ (\S+)", label or "")
        text = f"{name_of(m.group(1))} —[{m.group(2)}]→ {name_of(m.group(3))}" if m else (label or fact)
        ends = {"from": by_key.get(m.group(1), ""), "to": by_key.get(m.group(3), ""),
                "rel": re.sub(r'(?<!^)(?=[A-Z])', '_', m.group(2)).lower()} if m else {}
        return {"text": text + (" (inferred)" if derived else ""), "stv": stv, "papers": papers_of(fact), **ends}
    for atom in inferred:
        r = _parse_result(atom)
        if not r:
            continue
        proof, pred, a, b, strength = r[0], r[1], r[2], r[3], r[4]
        sid, tid = by_key.get(a), by_key.get(b)
        rel = re.sub(r'(?<!^)(?=[A-Z])', '_', pred).lower()
        if not sid or not tid or (sid, rel, tid) in seen:
            continue
        seen.add((sid, rel, tid))
        d, bw = derivations.get(proof) or {}, backward.get((pred, a, b))
        rule = d.get("rule") or (bw or {}).get("rule", "")
        names = (_proof_parts(d.get("proof", "")) or ("", []))[1] if d else (bw or {}).get("premises", [])
        prem = ([premise(n, d.get(f"premise{i + 1}", ""), d.get(f"premise{i + 1}_stv", "")) for i, n in enumerate(names[:2])]
                if d else [premise(n, pln._forward.premise_label(n)) for n in names])
        trace = {"rule": rule, "meaning": meaning.get(rule, ""),
                 "rule_stv": f"(stv {d['rule_s']:.2f} {d['rule_c']:.2f})" if d else "",
                 "derived_stv": f"(stv {float(r[4]):.2f} {float(r[5]):.2f})", "premises": prem,
                 "question": (bw or {}).get("question", "")}
        paper = paper_of.get(sid) or paper_of.get(tid, "")
        edges.append({"id": f"pln-{len(edges)}", "source_id": sid, "target_id": tid, "relation": rel,
                      "source_name": nodes[sid].get("name", ""), "target_name": nodes[tid].get("name", ""),
                      "confidence": float(strength or 0), "negated": False, "inferred": True,
                      "inferred_via": "both" if bw and d else "backward" if bw else "forward", "trace": trace,
                      "source_paper": paper, "_paper": paper,
                      "reasoning": f"Inferred by PLN{' with rule ' + rule if rule else ''}; no paper states it."})
    render_html(nodes, edges, out, title="PLN Reasoning · PeTTaChainer", fast_physics=True)


def _keep_found(pln, path: Path, state) -> None:
    """Save the triples backward chaining proved, with the state of the facts they were proved from."""
    found = pln.backward_found()
    if found:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"state": state, "found": found}))


def _restore_found(pln, path: Path, state) -> None:
    """Triples proved for earlier questions come back while the facts are unchanged."""
    try:
        saved = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        saved = {}
    if saved.get("state") == json.loads(json.dumps(state)):
        pln.restore_backward(saved.get("found", {}))


def _sync_note() -> str:
    """One line on what the last sync loaded."""
    s = _unified_sync
    if not s.get('added'):
        return f"all {s.get('papers', 0)} paper(s) were already loaded; nothing new to load"
    how = f"rebuilt ({s['rebuilt']})" if s.get('rebuilt') else "kept the papers already loaded"
    return f"loaded {len(s['added'])} new paper(s) in {s['ms']} ms; {how}"


class PLNRunRequest(BaseModel):
    run_dir:    str
    staging_db: str | None = None
    paper_id:   str = ''


class PLNQueryRequest(BaseModel):
    question:   str
    run_dir:    str | None = None
    staging_db: str | None = None
    fresh:      bool = False          # reason again instead of returning the saved answer


def _saved_answers(run_dir: str | None) -> Path | None:
    """Answers already reasoned for this paper, kept with its run."""
    return Path(run_dir) / "pln_answers.json" if run_dir and Path(run_dir).is_dir() else None


def _answer_key(question: str, run_dir: str | None, staging_db: str | None) -> str:
    """Same question about the same, unchanged facts → same key."""
    q = re.sub(r'\s+', ' ', question.strip().lower()).rstrip('?').strip()
    return json.dumps([q, *_paper_state(run_dir, staging_db)[2:]])


@router.post("/api/pln-run")
@_serial
def pln_run(req: PLNRunRequest):
    """Load a paper into PLN, run forward chaining, generate vis.js graph HTML."""
    try:
        global _paper_key
        _paper_key = None                                         # a run always reloads the paper
        pln, _ = _paper_pln(req.run_dir, req.staging_db)
        if not pln.enabled:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        n_atoms = pln.load_paper(run_dir=req.run_dir, staging_db=req.staging_db)
        if n_atoms == 0:
            return {"ok": False, "error": "No atoms loaded — check run_dir / staging_db path"}
        _restore_found(pln, Path(req.run_dir) / "pln_backward.json", _paper_state(req.run_dir, req.staging_db))

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
@_serial
def pln_query(req: PLNQueryRequest):
    """Answer a NL question over the KG using PLN forward+backward chaining."""
    try:
        paper = bool(req.run_dir or req.staging_db)
        pln, fresh = _paper_pln(req.run_dir, req.staging_db) if paper else (_get_pln(), False)
        if not pln.enabled:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        store = _saved_answers(req.run_dir)
        key = _answer_key(req.question, req.run_dir, req.staging_db)
        saved = json.loads(store.read_text()) if store and store.exists() else {}
        if key in saved and not req.fresh:
            return {**saved[key], "cached": True}

        t0 = time.perf_counter()
        if paper and fresh:
            pln.load_paper(run_dir=req.run_dir or "", staging_db=req.staging_db)
            if req.run_dir:
                _restore_found(pln, Path(req.run_dir) / "pln_backward.json", _paper_state(req.run_dir, req.staging_db))
        result = pln.ask(req.question)
        if req.run_dir:
            _keep_found(pln, Path(req.run_dir) / "pln_backward.json", _paper_state(req.run_dir, req.staging_db))

        from pln.query.answer_generator import generate_json
        reply = {"ok": True, **generate_json(result), "reasoned_ms": round((time.perf_counter() - t0) * 1000),
                 "saved_at": datetime.now().isoformat(timespec="seconds")}
        if store:
            saved[key] = reply
            store.write_text(json.dumps(saved))
        return {**reply, "cached": False}
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:500]}


@router.get("/api/pln-facts")
@_serial
def pln_facts(limit: int = Query(default=200, le=2000)):
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
@_serial
def pln_inferred(
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
@_serial
def pln_papers():
    """List all papers that have a PLN graph HTML file."""
    kg_root = Path("data/kg_output")
    papers = []
    if kg_root.exists():
        for run_dir in sorted(kg_root.iterdir()):
            if run_dir.name.startswith("unified_"):
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
def pln_unified_status():
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
@_serial
def pln_unified_run(reload: bool = False):
    """Load ALL committed MeTTa files, run PeTTaChainer forward chaining, generate unified PLN graph."""
    global _unified_pln_module
    try:
        from pln.config import PLNConfig
        cfg = PLNConfig.from_env()
        if not cfg.enabled:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        pln = _get_unified_pln(force_reload=reload)    # loads only papers not in it yet, unless reload
        if pln is None:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}

        total_atoms = len(pln.all_facts())
        kg_root = Path("data/kg_output")
        loaded_papers = [d.name for d in _latest_run_dirs(kg_root)] if kg_root.exists() else []

        if total_atoms == 0:
            return {"ok": False, "error": "No MeTTa atoms found in data/kg_output/ — run papers through Layer 8 first"}

        # All PLN output goes to unified_pln/ — HTML + flat metta + per-category metta/
        _UNIFIED_PLN_DIR.mkdir(parents=True, exist_ok=True)

        pln_html = _UNIFIED_PLN_DIR / "pln_atomspace.html"
        _write_pln_page(pln, _latest_run_dirs(kg_root), pln_html)

        inferred  = len(pln.inferred())
        metta     = _UNIFIED_PLN_DIR / "pln_atomspace.metta"
        return {
            "ok":           True,
            "papers":       len(loaded_papers),
            "atoms_loaded": total_atoms,
            "inferred":     inferred,
            "pln_html":     str(pln_html) if pln_html.exists() else "",
            "pln_metta":    str(metta) if metta.exists() else "",
            "sync":         _unified_sync,
        }
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:600]}


@router.get("/api/pln-overview")
@_serial
def pln_overview():
    """What the all-papers atomspace holds, and questions built from its own facts."""
    try:
        pln = _get_unified_pln()
        if pln is None:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}
        from pln.query.nl_to_pln import suggest_questions
        facts = pln.facts()
        store = _UNIFIED_PLN_DIR / "pln_answers.json"
        saved = json.loads(store.read_text()) if store.exists() else {}
        state = json.loads(json.dumps(sorted(_unified_state.items())))
        asked = [{"question": v["question"], "ms": v.get("reasoned_ms"), "saved_at": v.get("saved_at")}
                 for k, v in saved.items() if json.loads(k)[1] == state]
        names = pln.names()
        return {"ok": True, "papers": len(pln.papers()), "facts": len(facts),
                "entities": len(names), "relations": len({f[0] for f in facts}), "sync": _sync_note(),
                "asked": sorted(asked, key=lambda a: a["saved_at"] or "", reverse=True),
                **suggest_questions(facts, names, limit=3)}
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:500]}


@router.post("/api/pln-unified-query")
@_serial
def pln_unified_query(req: PLNQueryRequest):
    """Answer a question over the all-papers atomspace; a repeat question returns the saved answer."""
    try:
        t0 = time.perf_counter()
        store = _UNIFIED_PLN_DIR / "pln_answers.json"
        q = re.sub(r'\s+', ' ', req.question.strip().lower()).rstrip('?').strip()
        saved = json.loads(store.read_text()) if store.exists() else {}
        on_disk = json.dumps([q, sorted((k, v[1]) for k, v in _current_state().items())])
        if on_disk in saved and not req.fresh:          # a saved answer needs no atomspace, even after a restart
            return {**saved[on_disk], "cached": True}

        pln = _get_unified_pln()
        if pln is None:
            return {"ok": False, "error": "PLN disabled — set ENABLE_PLN=true"}
        key = json.dumps([q, sorted(_unified_state.items())])
        if key in saved and not req.fresh:
            return {**saved[key], "cached": True}

        result = pln.ask(req.question)
        _keep_found(pln, _UNIFIED_PLN_DIR / "pln_backward.json", sorted(_unified_state.items()))
        result.trace.insert(1, {'step': 'Sync', 'ms': _unified_sync.get('ms'), 'detail': _sync_note(), 'data': None,
                                'explain': ("Before reasoning, the atomspace is compared with the processed papers: only papers "
                                            "it does not hold yet are loaded. A paper whose facts changed means a rebuild, "
                                            "since atoms cannot be taken back out one by one.")})
        from pln.query.answer_generator import generate_json
        reply = {"ok": True, **generate_json(result), "papers": len(_unified_state),
                 "reasoned_ms": round((time.perf_counter() - t0) * 1000),
                 "saved_at": datetime.now().isoformat(timespec="seconds")}
        _UNIFIED_PLN_DIR.mkdir(parents=True, exist_ok=True)
        state = json.loads(key)[1]
        saved = {k: v for k, v in saved.items() if json.loads(k)[1] == state}   # answers for older paper sets go
        saved[key] = reply
        store.write_text(json.dumps(saved))
        return {**reply, "cached": False}
    except Exception as exc:
        import traceback
        return {"ok": False, "error": str(exc), "trace": traceback.format_exc()[:500]}


@router.post("/api/run_pln")
@_serial
def run_pln_endpoint():
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
