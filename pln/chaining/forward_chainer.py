"""Forward chaining over facts and bio_rules.metta. PeTTaChainer derives each rule's conclusions
(PLN truth formulas, multi-step chains, proofs); derived atoms are kept apart from the KB so
later queries do not count them twice."""
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pln.config import PLNConfig

_CONCLUSION_RE = re.compile(r'\(Implication\s+\(And\s.*\)\s+\((\w+)\s+\$\w+\s+\$\w+\)\s*\)\s*\(STV', re.S)
_STATEMENT_RE = re.compile(r'^\(:\s+(\S+)\s+\((\w+)\s+(.*?)\)\s+\(STV\s+([\d.]+)\s+([\d.]+)\)\)$')


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


def _split_top(s: str) -> list[str]:
    """Top-level parts of an expression body."""
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ' ' and depth == 0:
            if buf:
                parts.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append(''.join(buf))
    return parts


def _parse_result(result: str):
    """(proof, predicate, subject, object, s, c) of a query result '(: proof (P s o) (STV s c))'."""
    body = result.strip()
    if not body.startswith('(:') or not body.endswith(')'):
        return None
    parts = _split_top(body[1:-1].strip())
    if len(parts) != 4:
        return None
    _, proof, statement, stv = parts
    st = _split_top(statement[1:-1]) if statement.startswith('(') else []
    tv = re.match(r'\(STV\s+([\d.eE-]+)\s+([\d.eE-]+)\)', stv)
    if len(st) != 3 or not tv:
        return None
    return proof, st[0], st[1], st[2], float(tv.group(1)), float(tv.group(2))


def _proof_parts(proof: str):
    """(rule name, premise proofs) of '(by rule (conjunction p1 p2 ...))'; None for a plain fact."""
    if not proof.startswith('(by '):
        return None
    parts = _split_top(proof[1:-1])
    rule = parts[1] if len(parts) > 1 else ''
    premises = []
    if len(parts) > 2:
        inner = parts[2]
        premises = _split_top(inner[1:-1])[1:] if inner.startswith('(conjunction') else [inner]
    return rule, premises


class ForwardChainer:
    """PeTTaChainer KB with rule-based derivation of every rule conclusion."""

    def __init__(self, cfg: "PLNConfig"):
        self._cfg = cfg
        self._handler = None
        self._rules: dict[str, tuple[float, float]] = {}      # rule name → (s, c)
        self._rule_info: dict[str, dict] = {}                # rule name → {'text', 'meaning'}
        self._conclusions: set[str] = set()                   # predicates rules conclude
        self._facts: dict[str, str] = {}                      # fact name → statement atom
        self._derived_atoms: list[str] = []
        self._derivations: dict[str, dict] = {}
        self.materialized = False                             # derived once; also when nothing follows

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
                continue
            m = _STATEMENT_RE.match(atom.strip())
            if m:
                self._facts[m.group(1)] = atom

    def load_rules_file(self, rules_path: Path | None = None) -> None:
        """Add the file's rules to PeTTaChainer and note each rule's conclusion predicate."""
        path = rules_path or self._cfg.rules_path
        if not path or not path.exists():
            return
        self._ensure_handler()
        lines = path.read_text(encoding='utf-8').splitlines()
        meaning, meanings = '', {}                           # the comment line right above each rule
        for line in lines:
            t = line.strip()
            if t.startswith(';') and not set(t) <= set('; ─'):
                meaning = t.lstrip('; ').strip()
            elif t.startswith('(:'):
                meanings[t.split()[1]] = meaning
        for expr in _collect_exprs(lines):
            if not expr.startswith('(:'):
                continue
            try:
                self._handler.add_atom(expr)
            except Exception:
                continue
            name = re.match(r'^\(:\s+(\S+)', expr).group(1)
            self._rule_info[name] = {'text': ' '.join(expr.split()), 'meaning': meanings.get(name, '')}
            stv = re.search(r'\(STV\s+([\d.]+)\s+([\d.]+)\)\s*\)\s*$', expr)
            if stv:
                self._rules[name] = (float(stv.group(1)), float(stv.group(2)))
            m = _CONCLUSION_RE.search(expr)
            if m:
                self._conclusions.add(m.group(1))

    def _label(self, proof: str) -> tuple[str, str]:
        """'subject —[P]→ object' and its STV for a premise proof."""
        atom = self._facts.get(proof)
        if atom:
            m = _STATEMENT_RE.match(atom.strip())
            s, o = _split_top(m.group(3))[:2]
            return f"{_entity_id(s)} —[{m.group(2)}]→ {_entity_id(o)}", f"(stv {float(m.group(4)):.2f} {float(m.group(5)):.2f})"
        return "derived relation", ""

    def materialize(self, steps: int | None = None) -> None:
        """Derive every rule conclusion with PeTTaChainer, keeping the best proof per relation."""
        if not self._conclusions or self.materialized:
            return
        self.materialized = True
        n = steps if steps is not None else self._cfg.forward_steps
        known = set()
        for atom in self._facts.values():
            m = _STATEMENT_RE.match(atom.strip())
            s, o = _split_top(m.group(3))[:2]
            known.add((m.group(2), s, o))
        best: dict[tuple, tuple] = {}
        for pred in sorted(self._conclusions):
            try:
                results = self._handler.query(f"(: $prf ({pred} $x $y) $tv)", steps=n * 20,
                                              timeout_sec=self._cfg.backward_timeout)
            except Exception:
                continue
            for r in results or []:
                parsed = _parse_result(str(r))
                if not parsed:
                    continue
                proof, p, s, o, sv, cv = parsed
                parts = _proof_parts(proof)
                if not parts or (p, s, o) in known or s == o:
                    continue
                key = (p, s, o)
                if key not in best or cv > best[key][4]:
                    best[key] = (proof, parts, s, o, cv, sv)
        safe = lambda x: re.sub(r'[^a-zA-Z0-9_]', '_', _entity_id(x)).strip('_')[:28]
        for (p, s, o), (proof, (rule, premises), _, _, cv, sv) in best.items():
            name = f"inferred_{safe(p)}_{safe(s)}_{safe(o)}"
            self._derived_atoms.append(f"(: {name} ({p} {s} {o}) (STV {sv:.3f} {cv:.3f}))")
            labels = [self._label(x) for x in premises[:2]] + [("", "")] * 2
            rs, rc = self._rules.get(rule, (0.0, 0.0))
            self._derivations[name] = {
                'rule': rule, 'rule_s': rs, 'rule_c': rc,
                'premise1': labels[0][0], 'premise1_stv': labels[0][1],
                'premise2': labels[1][0], 'premise2_stv': labels[1][1],
                'conclusion': f"{_entity_id(s)} —[{p}]→ {_entity_id(o)}",
                'derived_s': sv, 'derived_c': cv, 'proof': proof,
            }

    def rules(self) -> dict[str, tuple[float, float]]:
        """Loaded rule names → (s, c)."""
        return dict(self._rules)

    def rule_info(self) -> list[dict]:
        """Each rule: name, STV, its MeTTa text and the plain meaning written above it."""
        return [{'name': n, 'stv': list(self._rules.get(n, (0, 0))), **i} for n, i in self._rule_info.items()]

    def premise_label(self, proof: str) -> str:
        """'subject —[P]→ object (stv s c)' for a premise proof."""
        text, stv = self._label(proof)
        return f"{text} {stv}".strip()

    def derivations(self) -> dict[str, dict]:
        """Return {atom_name: proof_trace} for all derived atoms."""
        return dict(self._derivations)

    def run(self, steps: int | None = None) -> list[str]:
        """Derive and return the inferred atoms."""
        self.materialize(steps)
        return list(self._derived_atoms)

    def all_facts(self) -> list[str]:
        """Original facts plus derived atoms."""
        return list(self._facts.values()) + list(self._derived_atoms)

    def handler(self):
        """Direct access to the underlying PeTTaChainer handler (for query reuse)."""
        self._ensure_handler()
        return self._handler


def _entity_id(expr: str) -> str:
    m = re.match(r'^\((\w+)\s+(.+?)\)$', expr.strip())
    return m.group(2).strip() if m else expr.strip()
