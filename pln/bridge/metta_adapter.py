"""Layer 8 MeTTa files → PeTTaChainer atoms: (binds_to (protein A) (protein B)) becomes
(: binds_to_a_b (Binds_To (protein A) (protein B)) (STV s c))."""
import re
from pathlib import Path
from typing import Iterator


# Relations where we flip to PascalCase predicate names
_TO_PASCAL = re.compile(r'(?:^|_)([a-zA-Z0-9])')


def _pascal(name: str) -> str:
    return _TO_PASCAL.sub(lambda m: m.group(1).upper(), name)


def _make_atom_name(predicate: str, subj: str, obj: str) -> str:
    """Unique atom name from predicate + subject + object, safe for MeTTa."""
    def _safe(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_]', '_', s).strip('_')[:30]
    return f"{_safe(predicate)}_{_safe(subj)}_{_safe(obj)}"


def _parse_triple(expr: str) -> tuple[str, str, str] | None:
    """(predicate, subject, object) of a triple expression, or None."""
    expr = expr.strip()
    # Must start with ( and not be a metadata annotation
    if not expr.startswith('('):
        return None
    # Strip outer parens
    inner = expr[1:-1].strip()
    # Split on top-level spaces respecting nested parens
    parts = _split_top_level(inner)
    if len(parts) < 3:
        return None
    predicate = parts[0]
    # Skip metadata predicates (they annotate the triple, not represent it)
    _META = {'negated', 'source', 'paper_source', 'paper_url', 'section',
             'species', 'tissue', 'condition', 'effect_size', 'reasoning',
             'confidence', 'stv', 'STV', 'inferred_from'}
    if predicate in _META or predicate.startswith(';'):
        return None
    subj = parts[1]
    obj  = parts[2]
    return predicate, subj, obj


def _split_top_level(s: str) -> list[str]:
    """Split string on spaces, respecting nested parentheses and quoted strings."""
    parts, depth, buf, in_quote = [], 0, [], False
    for ch in s:
        if ch == '"' and not in_quote:
            in_quote = True; buf.append(ch)
        elif ch == '"' and in_quote:
            in_quote = False; buf.append(ch)
        elif in_quote:
            buf.append(ch)
        elif ch == '(':
            depth += 1; buf.append(ch)
        elif ch == ')':
            depth -= 1; buf.append(ch)
        elif ch == ' ' and depth == 0:
            token = ''.join(buf).strip()
            if token: parts.append(token)
            buf = []
        else:
            buf.append(ch)
    token = ''.join(buf).strip()
    if token: parts.append(token)
    return parts


def _extract_id(expr: str) -> str:
    """Extract a short ID from an atom expression like (protein HGNC_17761) → HGNC_17761."""
    inner = expr.strip().strip('()')
    parts = inner.split()
    return parts[-1] if parts else expr


def _extract_type(expr: str) -> str:
    """Extract entity type from (protein HGNC_17761) → 'protein'. Returns '' if no type."""
    inner = expr.strip().strip('()')
    parts = inner.split()
    return parts[0] if len(parts) > 1 else ''


def _name_slug(text: str) -> str:
    """Lowercase underscore slug safe as a MeTTa identifier (max 40 chars)."""
    import re as _re
    return _re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')[:40]


class MeTTaAdapter:
    """Reads Layer 8 MeTTa files from a run directory and yields PeTTaChainer atom strings."""

    def __init__(self, confidence: float = 0.5, default_strength: float = 0.5):
        self.confidence       = confidence        # default STV confidence
        self.default_strength = default_strength  # default STV strength

    def load_run_dir(self, run_dir: str | Path) -> list[str]:
        """Load all MeTTa edge files from a run_dir and return atom strings."""
        run_dir = Path(run_dir)
        metta_root = run_dir / "metta"
        if not metta_root.exists():
            return []
        # id → name from this paper's nodes files (Layer 7 canonical names)
        id_to_name: dict[str, str] = {}
        for node_file in sorted(metta_root.rglob("nodes_*.metta")):
            for _, eid, label in _iter_node_labels(node_file):
                if eid not in id_to_name:
                    id_to_name[eid] = label
        atoms = []
        for metta_file in sorted(metta_root.rglob("edges_*.metta")):
            atoms.extend(self._parse_file(metta_file, id_to_name))
        return atoms

    def load_atom_meta(self, run_dir: str | Path) -> dict[str, dict]:
        """{atom_name: {reasoning, source, section, paper_url}} from edge files, for the graph's audit panel."""
        run_dir = Path(run_dir)
        metta_root = run_dir / "metta"
        if not metta_root.exists():
            return {}
        meta_map: dict[str, dict] = {}
        for metta_file in sorted(metta_root.rglob("edges_*.metta")):
            meta_map.update(self._parse_file_meta(metta_file))
        return meta_map

    def _parse_file_meta(self, path: Path) -> dict[str, dict]:
        """Parse one edge MeTTa file and return {atom_name: {reasoning, source, section, paper_url}}."""
        lines = path.read_text(encoding="utf-8").splitlines()
        exprs = list(_collect_exprs(lines))

        meta: dict[str, dict] = {}
        triples: list[tuple[str, str, str, str]] = []

        for expr in exprs:
            if expr.startswith(';') or not expr.startswith('('):
                continue
            parsed = _parse_triple(expr)
            if parsed is None:
                continue
            pred, subj, obj = parsed
            if subj.startswith('(') and _parse_triple(subj) is not None:
                triple_key = subj
                meta.setdefault(triple_key, {})[pred] = obj
            else:
                triples.append((expr, pred, subj, obj))

        result: dict[str, dict] = {}
        for expr, pred, subj, obj in triples:
            m = meta.get(expr, {})
            negated = m.get('negated', 'False').strip().lower() == 'true'
            if negated:
                continue
            subj_id = _extract_id(subj)
            obj_id  = _extract_id(obj)
            name    = _make_atom_name(pred, subj_id, obj_id)
            result[name] = {
                'reasoning': m.get('reasoning', '').strip('"'),
                'source':    m.get('source',    '').strip('"'),
                'section':   m.get('section',   '').strip('"'),
                'paper_url': m.get('paper_url', '').strip('"'),
            }
        return result

    def load_labels(self, run_dir: str | Path) -> dict[str, tuple[str, str]]:
        """Lowercase entity name or synonym → (type, id), from nodes_*.metta files."""
        run_dir = Path(run_dir)
        metta_root = run_dir / "metta"
        if not metta_root.exists():
            return {}
        label_map: dict[str, tuple[str, str]] = {}
        for node_file in sorted(metta_root.rglob("nodes_*.metta")):
            for etype, eid, label in _iter_node_labels(node_file):
                label_map[label.lower()] = (etype, eid)
        return label_map

    def load_file(self, path: str | Path) -> list[str]:
        """Load a single MeTTa file and return atom strings."""
        return self._parse_file(Path(path))

    def _parse_file(self, path: Path,
                    id_to_name: dict[str, str] | None = None) -> list[str]:
        """Parse one .metta file, extracting triples and their metadata."""
        lines = path.read_text(encoding="utf-8").splitlines()

        exprs = list(_collect_exprs(lines))

        meta: dict[str, dict] = {}
        triples: list[tuple[str, str, str, str]] = []  # (expr, pred, subj, obj)

        for expr in exprs:
            if expr.startswith(';') or not expr.startswith('('):
                continue
            parsed = _parse_triple(expr)
            if parsed is None:
                continue
            pred, subj, obj = parsed
            if subj.startswith('(') and _parse_triple(subj) is not None:
                triple_key = subj
                meta.setdefault(triple_key, {})[pred] = obj
            else:
                triples.append((expr, pred, subj, obj))

        atoms = []
        for expr, pred, subj, obj in triples:
            m = meta.get(expr, {})
            negated = m.get('negated', 'False').strip().lower() == 'true'
            if negated:
                continue

            strength   = float(m.get('strength',   str(self.default_strength)))
            confidence = float(m.get('confidence', str(self.confidence)))

            atom = self._make_atom(pred, subj, obj, strength, confidence, id_to_name)
            if atom:
                atoms.append(atom)

        return atoms

    def _make_atom(self, predicate: str, subj: str, obj: str,
                   strength: float, confidence: float,
                   id_to_name: dict[str, str] | None = None) -> str | None:
        """Convert one triple to a PeTTaChainer atom string using entity names."""
        id_to_name  = id_to_name or {}
        pascal_pred = _pascal(predicate)

        subj_id   = _extract_id(subj)
        obj_id    = _extract_id(obj)
        subj_type = _extract_type(subj)
        obj_type  = _extract_type(obj)

        # Use the Layer-7 canonical name from this paper's nodes file; fall back to ID slug.
        subj_name = _name_slug(id_to_name.get(subj_id) or subj_id)
        obj_name  = _name_slug(id_to_name.get(obj_id)  or obj_id)

        atom_name  = _make_atom_name(predicate, subj_name, obj_name)
        subj_expr  = f"({subj_type} {subj_name})" if subj_type else subj_name
        obj_expr   = f"({obj_type} {obj_name})"   if obj_type  else obj_name

        s = f"{strength:.3f}"
        c = f"{confidence:.3f}"
        return f"(: {atom_name} ({pascal_pred} {subj_expr} {obj_expr}) (STV {s} {c}))"

    def triple_to_atom(self, subject: str, relation: str, object_: str,
                       subject_type: str = "", object_type: str = "",
                       confidence: float = 0.85) -> str:
        """Convert a staging DB triple dict to a PeTTaChainer atom string."""
        subj_expr = f"({subject_type.lower()} {subject})" if subject_type else subject
        obj_expr  = f"({object_type.lower()} {object_})"  if object_type  else object_
        pascal    = _pascal(relation)
        name      = _make_atom_name(relation, subject, object_)
        return f"(: {name} ({pascal} {subj_expr} {obj_expr}) (STV {confidence:.3f} {confidence:.3f}))"

    def db_to_atoms(self, staging_db: str | Path) -> list[str]:
        """Load atoms directly from a staging SQLite DB (alternative to MeTTa files)."""
        import sqlite3
        atoms = []
        try:
            conn = sqlite3.connect(str(staging_db), timeout=10)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT subject_name, subject_type, relation, object_name, object_type, confidence "
                "FROM triples WHERE flagged_for_review=0"
            ).fetchall()
            conn.close()
            for r in rows:
                atom = self.triple_to_atom(
                    r["subject_name"], r["relation"], r["object_name"],
                    r["subject_type"], r["object_type"],
                    float(r["confidence"] or 0.85),
                )
                atoms.append(atom)
        except Exception:
            pass
        return atoms


def _iter_node_labels(path: Path):
    """Yield (entity_type, entity_id, label_text) from a nodes_*.metta file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for expr in _collect_exprs(lines):
        if not expr.startswith('('):
            continue
        inner = expr[1:-1].strip()
        parts = _split_top_level(inner)
        if len(parts) < 3:
            continue
        pred = parts[0]
        if pred not in ('name', 'synonyms'):
            continue
        entity_expr = parts[1]
        entity_inner = entity_expr.strip('()').split()
        if len(entity_inner) < 2:
            continue
        etype = entity_inner[0]
        eid   = entity_inner[-1]
        label_raw = parts[2].strip('"')
        if pred == 'name':
            yield etype, eid, label_raw
        else:
            for syn in label_raw.split(';'):
                syn = syn.strip()
                if syn:
                    yield etype, eid, syn


def _collect_exprs(lines: list[str]) -> Iterator[str]:
    """Yield complete s-expressions from raw lines, handling multi-line and comments."""
    buf, depth = [], 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith(';'):
            if buf:
                yield ' '.join(buf).strip()
                buf, depth = [], 0
            continue
        for ch in line:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
        buf.append(line)
        if depth <= 0 and buf:
            yield ' '.join(buf).strip()
            buf, depth = [], 0
    if buf:
        yield ' '.join(buf).strip()
