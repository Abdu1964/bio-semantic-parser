"""PLN writer: serializes atomspace to disk in MeTTa and JSON formats."""
import json
import re
from pathlib import Path


_STV_RE  = re.compile(r'\(STV\s+([\d.]+)\s+([\d.]+)\)')
_NAME_RE = re.compile(r'^\(:\s+(\S+)')


def _parse_atom(atom_str: str) -> dict | None:
    nm = _NAME_RE.match(atom_str.strip())
    stv = _STV_RE.search(atom_str)
    if not nm:
        return None
    return {
        'name':       nm.group(1),
        'raw':        atom_str.strip(),
        'strength':   float(stv.group(1)) if stv else None,
        'confidence': float(stv.group(2)) if stv else None,
    }


class PLNWriter:
    """Writes PLN atomspace (facts + inferred) to output files."""

    def write_metta(self, atoms: list[str], path: str | Path,
                    header: str = "; PLN atomspace output") -> None:
        """Write atoms as a MeTTa file, one atom per line."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [header, ""]
        for atom in atoms:
            lines.append(atom)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_json(self, atoms: list[str], path: str | Path,
                   meta: dict | None = None) -> None:
        """Write atoms as a JSON array with parsed STV fields."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        parsed = [_parse_atom(a) for a in atoms]
        parsed = [p for p in parsed if p]
        obj = {"atoms": parsed}
        if meta:
            obj["meta"] = meta
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

    def write_inferred_only(self, original_atoms: list[str],
                            all_atoms: list[str],
                            metta_path: str | Path,
                            json_path: str | Path | None = None) -> list[str]:
        """Write and return only the atoms derived by inference."""
        original_set = set(a.strip() for a in original_atoms)
        inferred = [a for a in all_atoms if a.strip() not in original_set]
        self.write_metta(inferred, metta_path,
                         header="; PLN inferred atoms (not in original facts)")
        if json_path:
            self.write_json(inferred, json_path, meta={"inferred_only": True})
        return inferred

    def append_to_run_dir(self, atoms: list[str], run_dir: str | Path,
                          filename: str = "pln_inferred.metta") -> Path:
        """Write atoms into a paper's run directory."""
        out = Path(run_dir) / filename
        self.write_metta(atoms, out)
        return out
