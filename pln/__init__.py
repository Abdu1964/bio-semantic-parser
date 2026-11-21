"""PLN reasoning over the bio-semantic KG. Entry point: PLNModule (ENABLE_PLN=true)."""
from __future__ import annotations

from pathlib import Path

from pln.config import PLNConfig


class PLNModule:
    """Orchestrates bridge → chaining → identity → query → writer pipeline."""

    def __init__(self, cfg: PLNConfig):
        self._cfg = cfg
        self._forward  = None
        self._backward = None
        self._reasoner = None
        self._writer   = None
        self._adapter  = None
        self._resolver = None
        self._original_atoms: list[str] = []
        self._label_map: dict[str, tuple[str, str]] = {}
        self._atom_meta: dict[str, dict] = {}

    @classmethod
    def from_env(cls) -> "PLNModule":
        return cls(PLNConfig.from_env())

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    def _ensure_components(self) -> None:
        if self._forward is not None:
            return
        from pln.bridge.metta_adapter  import MeTTaAdapter
        from pln.chaining.forward_chainer import ForwardChainer
        from pln.chaining.backward_chainer import BackwardChainer
        from pln.identity.resolver     import IdentityResolver
        from pln.query.reasoner        import PLNReasoner
        from pln.writer.pln_writer     import PLNWriter

        self._adapter  = MeTTaAdapter(confidence=self._cfg.min_confidence)
        self._forward  = ForwardChainer(self._cfg)
        self._resolver = IdentityResolver(self._cfg)
        self._writer   = PLNWriter()

        # Load lib + bio rules once
        self._forward.load_rules_file(self._cfg.lib_path)
        self._forward.load_rules_file(self._cfg.rules_path)

        # BackwardChainer shares the handler so it sees forward-derived facts
        self._backward = BackwardChainer(self._cfg, self._forward.handler())
        self._reasoner = PLNReasoner(self._cfg, self._forward, self._backward)

    def load_paper(self, run_dir: str | Path,
                   staging_db: str | Path | None = None) -> int:
        """Load one paper's triples into the atomspace; returns the atom count."""
        if not self._cfg.enabled:
            return 0
        self._ensure_components()

        atoms: list[str] = []

        # MeTTa edge files are the facts; the staging DB gives their truth values
        truth = self._adapter.db_truth(staging_db) if staging_db and Path(staging_db).exists() else {}
        files = sorted(str(f.relative_to(run_dir)) for f in (Path(run_dir) / "metta").rglob("edges_*.metta")) if run_dir else []
        self._sources = {'run_dir': str(run_dir or ''), 'files': files,
                         'staging_db': str(staging_db or ''), 'truth_values_from_db': len(truth)}
        if run_dir:
            atoms.extend(self._adapter.load_run_dir(run_dir, truth))
            self._label_map.update(self._adapter.load_labels(run_dir))
            prev_keys = set(self._atom_meta.keys())
            self._atom_meta.update(self._adapter.load_atom_meta(run_dir))
            paper_key = Path(run_dir).name
            for k in self._atom_meta:
                if k not in prev_keys:
                    self._atom_meta[k]['paper_id'] = paper_key

        if not atoms and staging_db and Path(staging_db).exists():      # no MeTTa files: the DB's facts
            atoms.extend(self._adapter.db_to_atoms(staging_db))

        if not atoms:
            return 0

        from pln.chaining.truth_revision import revise_atoms
        atoms = revise_atoms(atoms)
        self._original_atoms.extend(atoms)
        self._forward.load_atoms(atoms)
        return len(atoms)

    def add_identity_atoms(self, staging_dbs: list[str | Path]) -> int:
        """Add IdenticalTo atoms from the identity resolver; call after load_paper()."""
        if not self._cfg.enabled:
            return 0
        self._ensure_components()
        id_atoms = self._resolver.resolve(staging_dbs)
        self._forward.load_atoms(id_atoms)
        self._original_atoms.extend(id_atoms)
        return len(id_atoms)

    def ask(self, question: str, raw_pln_query: str | None = None):
        """Answer a NL question using PLN reasoning. Returns ReasoningResult."""
        if not self._cfg.enabled:
            from pln.query.reasoner import ReasoningResult
            r = ReasoningResult()
            r.query = question
            return r
        self._ensure_components()
        terms = self._entity_terms()
        result = self._reasoner.ask(question, raw_pln_query, entity_map=self._label_map, terms=terms)
        src = getattr(self, '_sources', {})
        result.trace.insert(0, {
            'step': 'Knowledge', 'ms': None,
            'detail': f"{len(self._original_atoms)} fact(s) about {len(terms)} entities loaded into the atomspace",
            'explain': ("Each fact the pipeline extracted is read from this run's MeTTa edge files and becomes one atom "
                        "(: name (Predicate subject object) (STV strength confidence)). Strength and confidence come from "
                        "the paper's staging database when it is given (the fact's score after Layer 7); repeated facts are "
                        "merged by truth-value revision. These atoms are the only knowledge PLN reasons over."),
            'data': {'sources': src, 'atoms': list(self._original_atoms)}})
        return result

    def _entity_terms(self) -> list[str]:
        """Subject and object terms of the loaded facts, e.g. '(small_molecule sodium_intake)'."""
        from pln.chaining.forward_chainer import _parse_result
        terms = []
        for atom in self._original_atoms:
            parsed = _parse_result(atom)
            if parsed:
                terms.extend(t for t in parsed[2:4] if t.startswith('(') and t not in terms)
        return terms

    def save_inferred(self, run_dir: str | Path) -> Path | None:
        """Write inferred-only atoms to run_dir/pln_inferred.metta."""
        if not self._cfg.enabled or self._forward is None:
            return None
        all_atoms = self._forward.all_facts()
        return self._writer.write_inferred_only(
            self._original_atoms, all_atoms,
            metta_path=Path(run_dir) / "pln_inferred.metta",
            json_path=Path(run_dir) / "pln_inferred.json",
        ) and (Path(run_dir) / "pln_inferred.metta")

    def inferred(self) -> list[str]:
        """Atoms derived by the rules."""
        return [] if self._forward is None else self._forward.run()

    def all_facts(self) -> list[str]:
        """Return all atoms in the atomspace (facts + inferred)."""
        if self._forward is None:
            return []
        return self._forward.all_facts()

    def run_inference(self) -> None:
        """Run PLN forward chaining to materialize inferred atoms."""
        if not self._cfg.enabled:
            return
        self._ensure_components()
        self._forward.materialize(steps=self._cfg.forward_steps)

    def generate_visualization(self, run_dir: str | Path, paper_id: str = '') -> Path | None:
        """Forward-chain and write pln_graph.html; None when PLN is disabled."""
        if not self._cfg.enabled:
            return None
        self._ensure_components()

        # Materialize inferred atoms via forward chaining
        self._forward.materialize(steps=self._cfg.forward_steps)

        all_atoms = self._forward.all_facts()

        # Proof traces for the visualizer
        merged_meta = dict(self._atom_meta)
        for atom_name, proof in self._forward.derivations().items():
            merged_meta[atom_name] = {
                'reasoning':  '',
                'source':     '',
                'section':    '',
                'paper_url':  '',
                '_proof':     proof,  # full derivation trace for buildStvDetail
            }

        from pln.writer.pln_visualizer import generate_pln_html
        out_path = Path(run_dir) / "pln_graph.html"
        return generate_pln_html(
            original_atoms=self._original_atoms,
            all_atoms=all_atoms,
            out_path=out_path,
            paper_id=paper_id,
            atom_meta=merged_meta,
            label_map=self._label_map,
        )

    def config_dict(self) -> dict:
        return self._cfg.as_dict()
