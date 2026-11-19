"""Layer 9 PLN, run after a paper is committed: revision across papers, forward chaining
(A→B + B→C ⇒ A→C) and contradiction resolution. Formulas from PeTTa lib_pln.metta."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

_REPO     = Path(__file__).resolve().parents[2]
_DB_NEO4J = _REPO / "data" / "triple_store_neo4j.db"
_DB_METTA = _REPO / "data" / "triple_store_metta.db"


# ── Standard PLN formulas (Python port of lib_pln.metta) ─────────────────────

def truth_c2w(c: float) -> float:
    """Convert confidence to weight:  w = c / (1 - c)"""
    if c >= 1.0:
        return 1e9
    return c / (1.0 - c)


def truth_w2c(w: float) -> float:
    """Convert weight to confidence:  c = w / (w + 1)"""
    return w / (w + 1.0)


def truth_revision(s1: float, c1: float, s2: float, c2: float) -> tuple[float, float]:
    """PLN revision (PLN Book p.116): w = c/(1-c) per input; f = Σw·s / Σw; c = Σw / (Σw + 1)."""
    w1 = truth_c2w(c1)
    w2 = truth_c2w(c2)
    w  = w1 + w2
    if w == 0:
        return 0.5, 0.0
    f = (w1 * s1 + w2 * s2) / w
    c = truth_w2c(w)
    return min(1.0, f), min(1.0, max(c, c1, c2))


def truth_deduction(
    ps: float, qs: float, rs: float,
    pqs: float, qrs: float,
    pc: float, qc: float, rc: float,
    pqc: float, qrc: float,
) -> tuple[float, float]:
    """PLN deduction (PLN Book p.15), A→B + B→C ⇒ A→C:
    s = PQs·QRs + (1-PQs)(Rs - Qs·QRs)/(1-Qs); c = min of the input confidences."""
    if qs >= 0.9999:
        s = rs
    else:
        s = pqs * qrs + ((1 - pqs) * (rs - qs * qrs)) / (1 - qs)
    c = min(pc, qc, rc, pqc, qrc)
    return max(0.0, min(1.0, s)), max(0.0, min(1.0, c))


def truth_negation(s: float, c: float) -> tuple[float, float]:
    """Negation: (stv s c) → (stv 1-s c)"""
    return 1.0 - s, c


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db(fmt: str, db_path: str = None) -> Path:
    """DB to use: db_path, else TRIPLE_STORE_PATH, else the format's main DB."""
    import os as _os
    if db_path:
        return Path(db_path)
    env = _os.getenv("TRIPLE_STORE_PATH", "")
    if env and env not in ("data/triple_store.db", "data/triple_store_neo4j.db", "data/triple_store_metta.db"):
        return Path(env)
    return _DB_NEO4J if fmt == "neo4j" else _DB_METTA


# ── Step 2: Revision from verify_kg results ──────────────────────────────────

def apply_revision_from_verification(
    verifications: list[dict],
    fmt: str = "neo4j",
    db_path: str = None,
) -> int:
    """Revise each triple's STV from verification: CONFIRMED (1.0, 0.5), MISMATCH (0.0, 0.5).
    Returns the number updated."""
    _db = _get_db(fmt, db_path)
    if not _db.exists():
        return 0

    conn = sqlite3.connect(str(_db))
    conn.row_factory = sqlite3.Row
    updated = 0

    for v in verifications:
        status = v.get("overall_status", "UNVERIFIABLE")
        if status not in ("CONFIRMED", "MISMATCH"):
            continue

        subject_id = v.get("subject_id", "")
        object_id  = v.get("object_id",  "")
        relation   = v.get("relation",   "")
        if not subject_id or not object_id or not relation:
            continue

        row = conn.execute(
            "SELECT id, confidence FROM triples "
            "WHERE subject_id=? AND relation=? AND object_id=?",
            (subject_id, relation, object_id),
        ).fetchone()
        if not row:
            continue

        s_cur = 0.5
        c_cur = float(row["confidence"])
        s_ev  = 1.0 if status == "CONFIRMED" else 0.0
        c_ev  = 0.5

        s_new, c_new = truth_revision(s_cur, c_cur, s_ev, c_ev)

        conn.execute(
            "UPDATE triples SET confidence=? WHERE id=?",
            (round(c_new, 4), row["id"]),
        )
        updated += 1

    conn.commit()
    conn.close()
    return updated


def collect_all_stv(fmt: str = "neo4j", db_path: str = None) -> list[dict]:
    """Current STV of every triple: strength 0.5 prior; confidence N/(N+1) from its paper count."""
    _db = _get_db(fmt, db_path)
    if not _db.exists():
        return []
    conn = sqlite3.connect(str(_db))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT subject_id, subject_name, subject_type, relation, "
        "object_id, object_name, object_type, negated, "
        "confidence, source_papers, reasoning, species, tissue, condition, effect_size "
        "FROM triples WHERE flagged_for_review=0"
    ).fetchall()]
    conn.close()

    result = []
    for r in rows:
        import json as _j, re as _re
        sources = _j.loads(r.get("source_papers") or "[]")

        # One paper may appear as a SHA-256 hash or a PMC ID / PMID / DOI: normalise before counting
        def _canonical_key(src: str) -> str:
            s = src.strip().lower()
            s = s.replace("pmc", "").replace("pmid:", "").replace("pmid", "")
            s = s.strip(": /")
            # SHA256 hashes are already unique per paper — keep as-is
            if _re.match(r'^[0-9a-f]{60,}$', s): return s
            return s

        unique_keys = list(dict.fromkeys(_canonical_key(s) for s in sources if s))
        n = max(1, len(unique_keys))   # N = unique independent papers
        # Truth_w2c: c = N/(N+1) over unique papers
        c = round(n / (n + 1), 4)
        result.append({
            "subject_id":    r["subject_id"],
            "subject_name":  r.get("subject_name", ""),
            "subject_type":  r.get("subject_type", "concept"),
            "relation":      r["relation"],
            "object_id":     r["object_id"],
            "object_name":   r.get("object_name", ""),
            "object_type":   r.get("object_type", "concept"),
            "negated":       bool(r.get("negated", 0)),
            "stv_strength":  0.5,
            "stv_confidence": c,   # recomputed from source count, never inflated
            "reasoning":     r.get("reasoning", "") or "",
            "source_papers": sources,
            "species":       r.get("species", "") or "",
            "tissue":        r.get("tissue", "") or "",
            "condition":     r.get("condition", "") or "",
            "effect_size":   r.get("effect_size", "") or "",
        })
    return result


# ── Step 3: Forward Chaining Inference ───────────────────────────────────────

def run_forward_chaining(
    fmt: str = "neo4j",
    min_confidence: float = 0.5,
    db_path: str = None,
) -> list[dict]:
    """Triples derived by deduction from premises above min_confidence (not written to the DB)."""
    _db = _get_db(fmt, db_path)
    if not _db.exists():
        return []

    conn = sqlite3.connect(str(_db))
    conn.row_factory = sqlite3.Row
    triples = [dict(r) for r in conn.execute(
        "SELECT * FROM triples WHERE confidence >= ? AND flagged_for_review=0",
        (min_confidence,),
    ).fetchall()]
    conn.close()

    # Build index: object_id → list of triples where it's the subject
    by_subject: dict = {}
    for t in triples:
        sid = t.get("subject_id", "")
        by_subject.setdefault(sid, []).append(t)

    derived = []
    seen: set = set()

    for t_ab in triples:
        b_id = t_ab.get("object_id", "")
        # Find all B→C triples
        for t_bc in by_subject.get(b_id, []):
            c_id = t_bc.get("object_id", "")
            if c_id == t_ab.get("subject_id", ""):
                continue   # avoid circular

            key = (t_ab["subject_id"], "inferred", c_id)
            if key in seen:
                continue
            seen.add(key)

            # Apply deduction formula
            # Treating P=A, Q=B, R=C, PQ=A→B, QR=B→C
            s, c = truth_deduction(
                ps=0.5, qs=0.5, rs=0.5,
                pqs=0.5, qrs=0.5,
                pc=float(t_ab["confidence"]),
                qc=0.5,
                rc=0.5,
                pqc=float(t_ab["confidence"]),
                qrc=float(t_bc["confidence"]),
            )

            if c < min_confidence:
                continue

            derived.append({
                "subject_id":   t_ab["subject_id"],
                "subject_name": t_ab.get("subject_name", ""),
                "subject_type": t_ab.get("subject_type", ""),
                "relation":     "inferred_relation",
                "object_id":    c_id,
                "object_name":  t_bc.get("object_name", ""),
                "object_type":  t_bc.get("object_type", ""),
                "confidence":   round(c, 4),
                "stv_strength": round(s, 4),
                "stv_confidence": round(c, 4),
                "derived_from": f"{t_ab['subject_id']}→{t_ab['relation']}→{b_id}→{t_bc['relation']}→{c_id}",
                "is_inferred":  True,
            })

    return derived


# ── Step 4: Contradiction Resolution ─────────────────────────────────────────

def resolve_contradictions(fmt: str = "neo4j", db_path: str = None) -> list[dict]:
    """Opposing triples for the same pair: the higher confidence wins, the other is flagged."""
    _db = _get_db(fmt, db_path)
    if not _db.exists():
        return []

    conn = sqlite3.connect(str(_db))
    conn.row_factory = sqlite3.Row

    # Find all (subject_id, object_id) pairs with more than one relation
    pairs = conn.execute("""
        SELECT subject_id, object_id, COUNT(DISTINCT relation) as cnt
        FROM triples
        WHERE flagged_for_review=0
        GROUP BY subject_id, object_id
        HAVING cnt > 1
    """).fetchall()

    resolved = []
    for pair in pairs:
        sid, oid = pair["subject_id"], pair["object_id"]
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM triples WHERE subject_id=? AND object_id=? AND flagged_for_review=0",
            (sid, oid),
        ).fetchall()]

        if len(rows) < 2:
            continue

        # Sort by confidence descending — highest confidence wins
        rows.sort(key=lambda r: r.get("confidence", 0.0), reverse=True)
        winner = rows[0]
        losers = rows[1:]

        for loser in losers:
            # Apply negation to the loser's truth value
            s_neg, c_neg = truth_negation(0.5, float(loser["confidence"]))
            resolved.append({
                "winner": {
                    "subject_id": winner["subject_id"],
                    "relation":   winner["relation"],
                    "object_id":  winner["object_id"],
                    "confidence": winner["confidence"],
                },
                "loser": {
                    "subject_id": loser["subject_id"],
                    "relation":   loser["relation"],
                    "object_id":  loser["object_id"],
                    "confidence": loser["confidence"],
                    "negated_stv": (round(s_neg, 4), round(c_neg, 4)),
                },
            })

    conn.close()
    return resolved


# ── Write PLN updates — same folder/naming convention as Layer 8 ─────────────

def _slug(s: str) -> str:
    """Lowercase underscore slug matching Layer 8 metta_writer convention."""
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "_", (s or "other").lower()).strip("_") or "other"


def write_pln_updates(pln_result: dict, out_path: Path) -> None:
    """Write inferred triples ({type}/edges_*.metta) and pln/pln_contradictions.metta,
    in Layer 8's folder layout."""
    from datetime import datetime, timezone
    header = (
        f"; PLN Layer 9 — {datetime.now(timezone.utc).isoformat()}\n"
        "; Generated by pln/legacy/layer9_pln.py\n"
        "; Standard formulas: trueagi-io/PeTTa lib_pln.metta\n\n"
    )

    # Current STV of every triple → pln/stv_updates.metta (overrides Layer 8's initial STV)
    stv_all = pln_result.get("stv_all", [])
    if stv_all:
        pln_root_early = out_path.parent / "pln"
        pln_root_early.mkdir(parents=True, exist_ok=True)
        stv_path = pln_root_early / "stv_updates.metta"
        lines = [
            header,
            "; Current stv for all extracted triples.",
            "; Overrides the initial (stv 0.5 0.50) written by Layer 8.",
            "; Load metta/ first, then pln/ — latest stv wins.",
            "",
        ]
        for t in stv_all:
            s_slug = _slug(t.get("subject_type", "concept"))
            o_slug = _slug(t.get("object_type",  "concept"))
            r_slug = _slug(t.get("relation",      "related_to"))
            sid    = t.get("subject_id", "")
            oid    = t.get("object_id",  "")
            s_val  = t.get("stv_strength",    0.5)
            c_val  = t.get("stv_confidence",  0.5)
            triple = f"({r_slug} ({s_slug} {sid}) ({o_slug} {oid}))"
            lines.append(f"(stv {triple} {s_val:.4f} {c_val:.4f})")
        with open(stv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    # ── Node files — same as metta/ nodes (nodes have no stv — only relations do) ──
    if stv_all:
        # Collect unique nodes by entity type
        _nodes_by_type: dict = {}
        for t in stv_all:
            for side in [
                (t.get("subject_id",""), t.get("subject_name",""), t.get("subject_type","concept")),
                (t.get("object_id",""),  t.get("object_name",""),  t.get("object_type","concept")),
            ]:
                nid, nname, ntype = side
                if not nid: continue
                nt_slug = _slug(ntype)
                if nt_slug not in _nodes_by_type:
                    _nodes_by_type[nt_slug] = {}
                if nid not in _nodes_by_type[nt_slug]:
                    _nodes_by_type[nt_slug][nid] = nname

        for nt_slug, nodes in _nodes_by_type.items():
            type_dir = (out_path.parent / "pln" / nt_slug)
            type_dir.mkdir(parents=True, exist_ok=True)
            lines = [header, f"; Nodes — {nt_slug}", ""]
            for nid, nname in nodes.items():
                lines.append(f"({nt_slug} {nid})")
                if nname and nname != nid:
                    lines.append(f"(name ({nt_slug} {nid}) \"{nname}\")")
            with open(type_dir / f"nodes_{nt_slug}.metta", "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    # Edge files with STV, mirroring metta/
    if stv_all:
        from collections import defaultdict as _dd2
        _groups: dict = _dd2(list)
        for t in stv_all:
            s_slug = _slug(t.get("subject_type", "concept"))
            r_slug = _slug(t.get("relation",      "related_to"))
            o_slug = _slug(t.get("object_type",   "concept"))
            _groups[(s_slug, r_slug, o_slug)].append(t)

        for (s_slug, r_slug, o_slug), triples in _groups.items():
            type_dir = (out_path.parent / "pln" / s_slug)
            type_dir.mkdir(parents=True, exist_ok=True)
            fname = f"edges_{s_slug}_{r_slug}_{o_slug}.metta"
            lines = [header,
                     f"; Extracted triples WITH PLN stv — {s_slug} {r_slug} {o_slug}", ""]
            for t in triples:
                sid   = t.get("subject_id", "")
                oid   = t.get("object_id",  "")
                s_val = t.get("stv_strength",   0.5)
                c_val = t.get("stv_confidence", 0.5)
                triple = f"({r_slug} ({s_slug} {sid}) ({o_slug} {oid}))"
                # Write triple + all properties + stv
                lines.append(triple)
                lines.append(f"(stv {triple} {s_val:.4f} {c_val:.4f})")
                sp = t.get("source_papers", [])
                if sp:
                    lines.append(f"(source {triple} {sp[0]})")
                neg = t.get("negated", False)
                if neg:
                    lines.append(f"(negated {triple} True)")
                reasoning = (t.get("reasoning","") or "").strip()[:200]
                if reasoning:
                    lines.append(f'(reasoning {triple} "{reasoning.replace(chr(34),chr(39))}")')
                lines.append("")
            with open(type_dir / fname, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    # PLN output goes to unified_metta/pln/ — separate from Layer 8 metta/
    pln_root = out_path.parent / "pln"

    # Inferred triples, in Layer 8's source_type/edges_source_rel_target.metta layout
    derived = pln_result.get("derived", [])
    if derived:
        from collections import defaultdict as _dd
        groups: dict = _dd(list)
        for t in derived:
            s_slug = _slug(t.get("subject_type", "concept"))
            r_slug = _slug(t.get("relation",     "inferred_relation"))
            o_slug = _slug(t.get("object_type",  "concept"))
            groups[(s_slug, r_slug, o_slug)].append(t)

        for (s_slug, r_slug, o_slug), triples in groups.items():
            type_dir = pln_root / s_slug
            type_dir.mkdir(parents=True, exist_ok=True)
            fname = f"edges_{s_slug}_{r_slug}_{o_slug}.metta"
            fpath = type_dir / fname
            lines = [header]
            for t in triples:
                sid   = t.get("subject_id", "")
                oid   = t.get("object_id",  "")
                s_val = t.get("stv_strength",   0.5)
                c_val = t.get("stv_confidence", 0.5)
                src   = t.get("derived_from",   "")
                triple = f"({r_slug} ({s_slug} {sid}) ({o_slug} {oid}))"
                lines.append(triple)
                lines.append(f"(stv {triple} {s_val:.4f} {c_val:.4f})")
                lines.append(f"(derived {triple} True)")
                if src:
                    lines.append(f'(derived_from {triple} "{src}")')
                lines.append("")
            # Append — PLN updates accumulate, not overwrite
            with open(fpath, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    # ── Contradiction flags — pln/pln_contradictions.metta ───────────────────
    contradictions = pln_result.get("contradictions", [])
    if contradictions:
        pln_dir = pln_root
        pln_dir.mkdir(parents=True, exist_ok=True)
        contra_path = pln_dir / "pln_contradictions.metta"
        lines = [header]
        for cr in contradictions:
            loser  = cr.get("loser",  {})
            winner = cr.get("winner", {})
            l_sid  = loser.get("subject_id",  "")
            l_oid  = loser.get("object_id",   "")
            l_rel  = _slug(loser.get("relation", ""))
            s_neg, c_neg = loser.get("negated_stv", (0.5, 0.5))
            w_rel  = _slug(winner.get("relation", ""))
            w_sid  = winner.get("subject_id", "")
            w_oid  = winner.get("object_id",  "")
            loser_triple  = f"({l_rel} (concept {l_sid}) (concept {l_oid}))"
            winner_triple = f"({w_rel} (concept {w_sid}) (concept {w_oid}))"
            lines.append(f"; contradiction loser")
            lines.append(f"(contradiction_loser {loser_triple} True)")
            lines.append(f"(stv {loser_triple} {s_neg:.4f} {c_neg:.4f})")
            lines.append(f"(contradicts_winner {winner_triple} {loser_triple})")
            lines.append("")
        with open(contra_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# The HTML report is made by tools/visualize_pln.py

def generate_pln_html(pln_result: dict, out_path: Path) -> Path:
    """Thin wrapper — actual implementation in tools/visualize_pln.py"""
    from tools.visualize_pln import generate_pln_html as _gen
    return _gen(pln_result, out_path)

# ── Main entry point ──────────────────────────────────────────────────────────

def run_pln(
    fmt: str = "neo4j",
    verifications: Optional[list] = None,
    min_confidence: float = 0.5,
    verbose: bool = True,
    db_path: str = None,
) -> dict:
    """Run all Layer 9 steps on db_path (default: TRIPLE_STORE_PATH or the main DB); returns a summary."""
    def log(msg: str):
        if verbose:
            print(f"  [Layer 9 PLN] {msg}")

    log("Starting PLN reasoning over bio-atomspace...")
    log("Formulas: Truth_Revision, Truth_Deduction, Truth_Negation (trueagi-io/PeTTa lib_pln.metta)")

    # Step 1: STV assigned at Layer 8
    log("Step 1 — TruthValue assignment done at Layer 8 (stv 0.5 N/(N+1))")

    # Step 2: revision from verification results
    revised = 0
    if verifications:
        log(f"Step 2 — PLN Revision from {len(verifications)} verify_kg result(s)...")
        log("  Why: verify_kg CONFIRMED/MISMATCH = evidence that updates triple strength")
        log("  How: Truth_Revision((stv s_cur c_cur), (stv 1.0/0.0 0.50)) → (stv s_new c_new)")
        revised = apply_revision_from_verification(verifications, fmt=fmt, db_path=db_path)
        log(f"  → {revised} triple(s) strength updated")
    else:
        log("Step 2 — PLN Revision skipped (no verify_kg results yet)")
        log("  Run verify_kg.py --fetch then re-commit to trigger revision")

    # Step 3: forward chaining by deduction
    log(f"Step 3 — PLN Forward Chaining (min_confidence={min_confidence})...")
    log("  Why: derives new relations not in any paper — A→B + B→C → A→C")
    log("  How: Truth_Deduction formula from PLN Book page 15")
    derived = run_forward_chaining(fmt=fmt, min_confidence=min_confidence, db_path=db_path)
    log(f"  → {len(derived)} new triple(s) inferred (marked derived=True in pln/ folder)")

    # Step 4: contradictions — the weaker triple is negated
    log("Step 4 — PLN Contradiction Resolution...")
    log("  Why: opposing triples (activates vs inhibits) resolved by truth value comparison")
    log("  How: Truth_Negation applied to lower-stv triple — both kept, weaker flagged")
    contradictions = resolve_contradictions(fmt=fmt, db_path=db_path)
    log(f"  → {len(contradictions)} contradiction(s) resolved")

    # Current STV of all triples, for pln/stv_updates.metta
    log("Collecting current stv for all triples → pln/stv_updates.metta...")
    stv_all = collect_all_stv(fmt=fmt, db_path=db_path)
    log(f"  → {len(stv_all)} triple(s) stv captured for pln/stv_updates.metta")

    return {
        "revised":        revised,
        "derived":        derived,
        "contradictions": contradictions,
        "stv_all":        stv_all,
    }
