"""Truth value revision: combine duplicate STV atoms using PLN revision rule."""
import re


_STV_RE = re.compile(r'\(STV\s+([\d.]+)\s+([\d.]+)\)')


def parse_stv(atom_str: str) -> tuple[float, float] | None:
    """Extract (strength, confidence) from an atom string."""
    m = _STV_RE.search(atom_str)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def revise(stv1: tuple[float, float], stv2: tuple[float, float]) -> tuple[float, float]:
    """PLN revision (PLN Book p.116): w = c/(1-c) per input; f = Σw·f / Σw; c = Σw / (Σw + 1)."""
    s1, c1 = stv1
    s2, c2 = stv2

    # Guard against confidence=1.0 (infinite weight)
    c1 = min(c1, 0.9999)
    c2 = min(c2, 0.9999)

    w1 = c1 / (1.0 - c1)
    w2 = c2 / (1.0 - c2)
    w  = w1 + w2
    s  = (w1 * s1 + w2 * s2) / w
    c  = w / (w + 1.0)
    # Confidence should be at least as high as either input
    c = max(c, max(c1, c2))
    return min(1.0, s), min(1.0, c)


def _atom_key(atom_str: str) -> str:
    """Strip the STV from an atom to get its structural key for dedup."""
    return _STV_RE.sub('', atom_str).strip()


def revise_atoms(atoms: list[str]) -> list[str]:
    """One atom per predicate and arguments, truth values combined by revision."""
    seen: dict[str, tuple[float, float]] = {}
    originals: dict[str, str] = {}

    for atom in atoms:
        key = _atom_key(atom)
        stv = parse_stv(atom)
        if stv is None:
            # No STV — keep as-is under its key
            seen[key] = (1.0, 0.5)
            originals[key] = atom
            continue
        if key in seen:
            seen[key] = revise(seen[key], stv)
        else:
            seen[key] = stv
            originals[key] = atom

    result = []
    for key, (s, c) in seen.items():
        original = originals[key]
        revised = _STV_RE.sub(f'(STV {s:.3f} {c:.3f})', original)
        result.append(revised)
    return result
