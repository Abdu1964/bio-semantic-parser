"""Forward chaining over facts and bio_rules.metta. PeTTaChainer's own forward chain covers only
Inheritance/Subset, so two-premise relation rules (Inhibits + Causes → Treats) run here and
derived atoms go back into its KB for backward queries."""
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pln.config import PLNConfig

_PC_FACT_RE = re.compile(
    r'^\(\((\w+)\s+(.*?)\)\s+\([a-zA-Z0-9]+\s+MAIN\s+Nil\)\s+(\S+)\s+\(STV\s+([\d.]+)\s+([\d.]+)\)\)\s*$'
)
_ENTITY_RE = re.compile(r'^\((\w+)\s+(.+?)\)$')


def _extract_id(expr: str) -> str:
    m = _ENTITY_RE.match(expr.strip())
    return m.group(2).strip() if m else expr.strip()


def _collect_exprs(lines: list[str]):
    buf, depth = [], 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(';'):
            if buf and depth == 0:
                buf, depth = [], 0
            continue
        buf.append(stripped)
        for ch in stripped:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
        if depth <= 0 and buf:
            yield ' '.join(buf).strip()
            buf, depth = [], 0
    if buf:
        yield ' '.join(buf).strip()


def _parse_bio_rules(rules_path: Path) -> list[dict]:
    """Parse bio_rules.metta and return list of rule dicts with premises and conclusion."""
    rules = []
    top_re = re.compile(r'^\(:')
    split_re = re.compile(r'\((\w+)\s+(\$\w+)\s+(\$\w+)\)')

    for expr in _collect_exprs(rules_path.read_text(encoding='utf-8').splitlines()):
        if not expr.startswith('(:'):
            continue
        # Extract name and STV from outer atom
        name_m = re.match(r'^\(:\s+(\S+)\s+', expr)
        stv_m = re.search(r'\(STV\s+([\d.]+)\s+([\d.]+)\)\s*\)\s*$', expr)
        if not name_m or not stv_m:
            continue
        name = name_m.group(1)
        rule_s = float(stv_m.group(1))
        rule_c = float(stv_m.group(2))

        # Find Premises block
        prem_m = re.search(r'\(Premises\s+(.*?)\)\s+\(Conclusions', expr)
        conc_m = re.search(r'\(Conclusions\s+(.*?)\)\s*\)\s*\(STV', expr)
        if not prem_m or not conc_m:
            continue

        prem_str = prem_m.group(1).strip()
        conc_str = conc_m.group(1).strip()

        premises = split_re.findall(prem_str)    # [(pred, $var1, $var2), ...]
        conclusions = split_re.findall(conc_str)
        if not premises or not conclusions:
            continue

        rules.append({
            'name': name, 'rule_s': rule_s, 'rule_c': rule_c,
            'premises': premises, 'conclusions': conclusions,
        })
    return rules


class ForwardChainer:
    """Wraps PeTTaChainer for KB + backward chaining; adds Python-level forward chain for bio rules."""

    def __init__(self, cfg: "PLNConfig"):
        self._cfg = cfg
        self._handler = None
        self._bio_rules: list[dict] = []
        self._derived_atoms: list[str] = []
        # Maps atom_name → proof trace dict for each derived atom
        self._derivations: dict[str, dict] = {}

    def _ensure_handler(self):
        if self._handler is not None:
            return
        from pettachainer.pettachainer import PeTTaChainer
        self._handler = PeTTaChainer()

    def load_atoms(self, atoms: list[str]) -> None:
        """Add fact atoms to the atomspace."""
        self._ensure_handler()
        for atom in atoms:
            try:
                self._handler.add_atom(atom)
            except Exception:
                pass

    def load_rules_file(self, rules_path: Path | None = None) -> None:
        """Load rule file: adds atoms to PeTTaChainer (for backward chain) and parses rules
        for Python-level forward chaining."""
        path = rules_path or self._cfg.rules_path
        if not path or not path.exists():
            return
        self._ensure_handler()
        for expr in _collect_exprs(path.read_text(encoding='utf-8').splitlines()):
            if expr.startswith('(:'):
                try:
                    self._handler.add_atom(expr)
                except Exception:
                    pass
        # Parse bio_rules.metta specifically for Python-level forward chaining
        if path.name == 'bio_rules.metta':
            self._bio_rules = _parse_bio_rules(path)

    def materialize(self, steps: int | None = None) -> None:
        """Apply the two-premise rules for up to `steps` rounds, adding derived atoms to the KB."""
        if not self._bio_rules:
            return
        n = steps if steps is not None else self._cfg.forward_steps

        for _step in range(n):
            # Snapshot current facts in PeTTaChainer output format
            raw_facts = [str(a) for a in (self._handler.all_facts() or [])]
            # Index facts by predicate → list of (subj_id, obj_id, s, c)
            by_pred: dict[str, list[tuple[str, str, float, float]]] = {}
            for a in raw_facts:
                m = _PC_FACT_RE.match(a.strip())
                if not m:
                    continue
                pred, args_str, _name, s, c = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
                # Extract subj/obj IDs from args_str
                args = re.findall(r'\([^()]+\)', args_str)
                if len(args) < 2:
                    continue
                sid = _extract_id(args[0])
                oid = _extract_id(args[1])
                by_pred.setdefault(pred, []).append((sid, oid, float(s), float(c)))

            new_atoms = []
            for rule in self._bio_rules:
                if len(rule['premises']) < 2:
                    continue
                p1_pred, p1_var1, p1_var2 = rule['premises'][0]  # e.g. Inhibits $dr $a
                p2_pred, p2_var1, p2_var2 = rule['premises'][1]  # e.g. Causes $a $d
                c_pred, c_var1, c_var2   = rule['conclusions'][0]

                # We expect pattern: ($dr → $a) then ($a → $d) → ($dr → $d)
                # The shared variable is p1_var2 == p2_var1 (bridge entity)
                if p1_var2 != p2_var1:
                    continue

                facts1 = by_pred.get(p1_pred, [])
                facts2 = by_pred.get(p2_pred, [])
                if not facts1 or not facts2:
                    continue

                # Index p2 by subject for fast lookup
                p2_by_subj: dict[str, list[tuple[str, float, float]]] = {}
                for sid2, oid2, s2, c2 in facts2:
                    p2_by_subj.setdefault(sid2, []).append((oid2, s2, c2))

                # Existing fact key set to avoid duplicates
                existing = {(p, sid, oid) for p, entries in by_pred.items() for sid, oid, _, _ in entries}

                for sid1, oid1, s1, c1 in facts1:
                    for oid2, s2, c2 in p2_by_subj.get(oid1, []):
                        key = (c_pred, sid1, oid2)
                        if key in existing:
                            continue
                        existing.add(key)
                        ds = min(s1, s2) * rule['rule_s']
                        dc = min(c1, c2, rule['rule_c'])
                        safe = lambda s: re.sub(r'[^a-zA-Z0-9_]', '_', s).strip('_')[:28]
                        atom_name = f"inferred_{safe(c_pred)}_{safe(sid1)}_{safe(oid2)}"
                        subj_type = _get_entity_type(sid1, raw_facts)
                        obj_type  = _get_entity_type(oid2, raw_facts)
                        subj_expr = f"({subj_type} {sid1})" if subj_type else sid1
                        obj_expr  = f"({obj_type} {oid2})"  if obj_type  else oid2
                        atom = f"(: {atom_name} ({c_pred} {subj_expr} {obj_expr}) (STV {ds:.3f} {dc:.3f}))"
                        new_atoms.append(atom)
                        # Record proof trace for the visualizer
                        self._derivations[atom_name] = {
                            'rule':      rule['name'],
                            'rule_s':    rule['rule_s'],
                            'rule_c':    rule['rule_c'],
                            'premise1':  f"{sid1} —[{p1_pred}]→ {oid1}",
                            'premise1_stv': f"(stv {s1:.2f} {c1:.2f})",
                            'premise2':  f"{oid1} —[{p2_pred}]→ {oid2}",
                            'premise2_stv': f"(stv {s2:.2f} {c2:.2f})",
                            'conclusion': f"{sid1} —[{c_pred}]→ {oid2}",
                            'derived_s': ds,
                            'derived_c': dc,
                        }

            if not new_atoms:
                break
            for atom in new_atoms:
                try:
                    self._handler.add_atom(atom)
                    self._derived_atoms.append(atom)
                except Exception:
                    pass

    def derivations(self) -> dict[str, dict]:
        """Return {atom_name: proof_trace} for all Python-derived atoms."""
        return dict(self._derivations)

    def run(self, steps: int | None = None) -> list[str]:
        """Run Python-level forward chaining and return derived atoms."""
        self.materialize(steps)
        return list(self._derived_atoms)

    def all_facts(self) -> list[str]:
        """Return all current atoms in the atomspace (original + derived)."""
        if self._handler is None:
            return []
        try:
            return [str(a) for a in (self._handler.all_facts() or [])]
        except Exception:
            return []

    def handler(self):
        """Direct access to the underlying PeTTaChainer handler (for query reuse)."""
        self._ensure_handler()
        return self._handler


def _get_entity_type(entity_id: str, raw_facts: list[str]) -> str:
    """Infer entity type from facts in the KB (e.g. HP_0000822 → disease)."""
    for a in raw_facts:
        m = _PC_FACT_RE.match(a.strip())
        if not m:
            continue
        args_str = m.group(2)
        args = re.findall(r'\((\w+)\s+([^()]+)\)', args_str)
        for etype, eid in args:
            if eid.strip() == entity_id:
                return etype
    return ''
