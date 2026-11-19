"""Cross-paper entity identity: names with TF-IDF cosine similarity ≥ threshold get
(IdenticalTo A B (STV similarity confidence))."""
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pln.config import PLNConfig


def _normalize(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9 ]', ' ', name).lower().strip()


class IdentityResolver:
    """Emits IdenticalTo atoms for similar entity names of the same type."""

    def __init__(self, cfg: "PLNConfig"):
        self._cfg = cfg

    def resolve(self, staging_dbs: list[str | Path]) -> list[str]:
        """IdenticalTo atoms for the entities in the staging DBs, compared within each type."""
        entities = self._load_entities(staging_dbs)
        if not entities:
            return []

        by_type: dict[str, list[tuple[str, str]]] = {}
        for name, etype in entities:
            by_type.setdefault(etype.lower(), []).append((name, etype))

        atoms = []
        for etype, group in by_type.items():
            if len(group) < 2:
                continue
            atoms.extend(self._compare_group(group))
        return atoms

    def _load_entities(self, dbs: list[str | Path]) -> list[tuple[str, str]]:
        seen = set()
        result = []
        for db_path in dbs:
            try:
                conn = sqlite3.connect(str(db_path), timeout=10)
                rows = conn.execute(
                    "SELECT DISTINCT subject_name, subject_type FROM triples WHERE flagged_for_review=0 "
                    "UNION SELECT DISTINCT object_name, object_type FROM triples WHERE flagged_for_review=0"
                ).fetchall()
                conn.close()
                for name, etype in rows:
                    key = (name, (etype or '').lower())
                    if key not in seen:
                        seen.add(key)
                        result.append(key)
            except Exception:
                pass
        return result

    def _compare_group(self, group: list[tuple[str, str]]) -> list[str]:
        """TF-IDF cosine similarity within an entity type group."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            import numpy as np
        except ImportError:
            return []

        names  = [_normalize(n) for n, _ in group]
        types  = [t for _, t in group]
        originals = [n for n, _ in group]

        if len(names) < 2:
            return []

        vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4))
        try:
            matrix = vec.fit_transform(names)
        except Exception:
            return []

        sim = cosine_similarity(matrix)
        threshold = self._cfg.identity_threshold
        atoms = []
        n = len(names)
        for i in range(n):
            for j in range(i + 1, n):
                score = float(sim[i, j])
                if score >= threshold and originals[i] != originals[j]:
                    # Confidence scales with similarity above threshold
                    conf = min(1.0, score)
                    atom = self._make_atom(originals[i], originals[j], types[i], score, conf)
                    atoms.append(atom)
        return atoms

    def _make_atom(self, a: str, b: str, etype: str,
                   strength: float, confidence: float) -> str:
        """Emit a HoTT IdenticalTo atom in PeTTaChainer format."""
        def _safe(s: str) -> str:
            return re.sub(r'[^a-zA-Z0-9_]', '_', s).strip('_')[:30]

        name = f"identity_{_safe(a)}_{_safe(b)}"
        subj = f"({etype.lower()} {a})"
        obj  = f"({etype.lower()} {b})"
        return f"(: {name} (IdenticalTo {subj} {obj}) (STV {strength:.3f} {confidence:.3f}))"

    def atoms_to_handler(self, atoms: list[str], handler) -> None:
        """Load IdenticalTo atoms directly into a PeTTaChainer handler."""
        for atom in atoms:
            try:
                handler.add_atom(atom)
            except Exception:
                pass
