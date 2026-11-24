"""PLN reasoning over the bio-semantic KG. Entry point: PLNModule (ENABLE_PLN=true)."""
from __future__ import annotations

import re
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
        self._sources: list[dict] = []                        # one entry per loaded paper
        self._fact_papers: dict[str, list[str]] = {}          # fact name → papers that state it
        self._backward_found: dict[str, dict] = {}            # triples a rule proved for a question

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
        self._forward.load_rules_file(self._cfg.lib_path)          # PLN helper functions; rules come from the schema and the facts

        # BackwardChainer shares the handler so it sees forward-derived facts
        self._backward = BackwardChainer(self._cfg, self._forward.handler())
        self._reasoner = PLNReasoner(self._cfg, self._forward, self._backward)

    def load_paper(self, run_dir: str | Path,
                   staging_db: str | Path | list | None = None) -> int:
        """Load one paper's triples into the atomspace; returns the atom count. staging_db: one database or
        several, the paper's own first. Strength: the first one holding the fact (the paper's pipeline score);
        confidence: the most papers any of them counts for it."""
        if not self._cfg.enabled:
            return 0
        self._ensure_components()

        atoms: list[str] = []

        # MeTTa edge files are the facts; the staging DB gives their truth values
        dbs = [str(d) for d in (staging_db if isinstance(staging_db, list) else [staging_db]) if d and Path(d).exists()]
        truth = self._adapter.csv_truth(run_dir) if run_dir else {}     # the run's own scores first
        for db in dbs:
            for k, (s, c) in self._adapter.db_truth(db).items():
                truth[k] = (truth[k][0], max(truth[k][1], c)) if k in truth else (s, c)
        staging_db = dbs[0] if len(dbs) == 1 else ' + '.join(dbs)
        files = sorted(str(f.relative_to(run_dir)) for f in (Path(run_dir) / "metta").rglob("edges_*.metta")) if run_dir else []
        paper = Path(run_dir).name if run_dir else Path(str(staging_db or '')).stem
        self._sources.append({'paper': paper, 'run_dir': str(run_dir or ''), 'files': files,
                              'staging_db': str(staging_db or ''), 'truth_values_from_db': len(truth)})
        if run_dir:
            atoms.extend(self._adapter.load_run_dir(run_dir, truth))
            self._label_map.update(self._adapter.load_labels(run_dir))
            prev_keys = set(self._atom_meta.keys())
            self._atom_meta.update(self._adapter.load_atom_meta(run_dir))
            paper_key = Path(run_dir).name
            for k in self._atom_meta:
                if k not in prev_keys:
                    self._atom_meta[k]['paper_id'] = paper_key

        if not atoms and dbs:                                           # no MeTTa files: the DB's facts
            atoms.extend(self._adapter.db_to_atoms(dbs[0]))

        if not atoms:
            return 0

        from pln.chaining.truth_revision import revise_atoms
        atoms = revise_atoms(atoms)
        new = []
        for atom in atoms:                           # a triple another paper already gave is not added twice:
            m = re.match(r'\(:\s+(\S+)', atom)       # its confidence already counts every paper stating it
            name = m.group(1) if m else atom
            if name not in self._fact_papers:
                new.append(atom)
            self._fact_papers.setdefault(name, []).append(paper)
        self._original_atoms.extend(new)
        self._forward.load_atoms(new)
        self._forward.invalidate(premises={m.group(1) for a in new if (m := re.match(r'\(:\s+\S+\s+\((\w+)', a))})
                                                     # derive again only what the new facts' relations can affect
        self._reasoner.invalidate()
        self._rules_stale = True                     # rules follow the facts' relations and patterns
        return len(new)

    def refresh_rules(self) -> None:
        """Rules for the loaded facts: from the schema (Biolink parents and symmetry, RO transitivity) and learned
        from the facts' own patterns. Rebuilt only after facts were added."""
        if not getattr(self, '_rules_stale', False):
            return
        from pln.rules.schema_rules import schema_rules
        from pln.rules.mined_rules import mine_rules
        facts = self.facts()
        schema = schema_rules({f[0] for f in facts})
        known = {r['text'] for r in schema}
        mined = [r for r in mine_rules(facts, self._cfg.mine_min_support, self._cfg.mine_min_coverage,
                                       self._cfg.mine_min_lift, self._cfg.mine_min_strength) if r['text'] not in known]
        self._forward.set_rules(schema + mined)
        self._rule_counts = {'schema': len(schema), 'learned': len(mined)}
        self._rules_stale = False

    def rule_counts(self) -> dict:
        """How many rules came from the schema and how many were learned from the facts."""
        return dict(getattr(self, '_rule_counts', {'schema': 0, 'learned': 0}))

    def papers(self) -> list[str]:
        """Papers loaded into this atomspace."""
        return [s['paper'] for s in self._sources]

    def names(self) -> dict[str, set]:
        """Entity symbol → its names and synonyms, from every loaded paper's node files."""
        from pln.bridge.metta_adapter import _id_key
        names = {t: {e['name']} for t, e in self._adapter.entities.items()} if self._adapter else {}
        for label, (_type, eid) in self._label_map.items():
            if _id_key(eid) in names:
                names[_id_key(eid)].add(label)
        return names

    def facts(self) -> list[tuple[str, str, str]]:
        """(predicate, subject term, object term) of every loaded fact."""
        from pln.chaining.forward_chainer import _parse_result
        return [p[1:4] for p in (_parse_result(a) for a in self._original_atoms) if p]

    def _papers_of(self, answer: dict) -> list[str]:
        """Papers whose facts the answer's proof uses: the fact itself, or a rule's premises."""
        from pln.chaining.forward_chainer import _proof_parts
        parts = _proof_parts(answer['proof'])
        names = [answer['proof']] if not parts else parts[1]
        found = []
        for n in names:
            m = re.match(r'\(:\s+(\S+)', n)
            for p in self._fact_papers.get(m.group(1) if m else n.strip('()'), []):
                if p not in found:
                    found.append(p)
        return found

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
        self.refresh_rules()
        names = self.names()
        # relations stated in facts, and those only rules conclude (asking for them needs the rules)
        predicates = sorted({f[0] for f in self.facts()} | self._forward._conclusions)
        result = self._reasoner.ask(question, raw_pln_query, names=names, predicates=predicates)
        for a in result.answers:
            a['papers'] = self._papers_of(a)
            if a['inferred']:
                self._keep_backward(a, question)
            for side in ('subject', 'object'):              # display name and type of the entity symbol
                e = self._adapter.entities.get(a[side], {})
                a[f'{side}_name'], a[f'{side}_type'] = e.get('name', a[side]), e.get('type', '')
        result.trace.insert(0, {
            'step': 'Knowledge', 'ms': None,
            'detail': (f"{len(self._original_atoms)} fact(s) about {len(names)} entities, {len(predicates)} relation types, "
                       f"from {len(self._sources)} paper(s) in the atomspace"),
            'explain': ("Each fact the pipeline extracted is read from its paper's MeTTa edge files and becomes one atom "
                        "(: name (Predicate subject object) (STV strength confidence)). Strength is the fact's confidence "
                        "score from the pipeline (Layer 7, kept in the triple store); confidence is N/(N+1) for the N papers "
                        "that state it. A fact found in no database keeps the default STV 0.5 0.5. Repeated facts are "
                        "merged by truth-value revision. These atoms are the only knowledge PLN reasons over."),
            'data': {'sources': self._sources, 'atoms': self._original_atoms[:500],
                     'atoms_total': len(self._original_atoms)}})
        return result

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

    def _keep_backward(self, answer: dict, question: str) -> None:
        """A triple a rule proved while answering a question joins the inferred ones, with its proof."""
        from pln.chaining.forward_chainer import _proof_parts
        from pln.bridge.metta_adapter import _make_atom_name
        name = "inferred_" + _make_atom_name(answer['predicate'], answer['subject'], answer['object'])
        if name in self._backward_found:
            return
        rule, premises = _proof_parts(answer['proof']) or ('', [])
        self._backward_found[name] = {
            'atom': f"(: {name} ({answer['predicate']} {answer['subject']} {answer['object']}) "
                    f"(STV {answer['strength']:.3f} {answer['confidence']:.3f}))",
            'via': 'backward', 'question': question, 'rule': rule,
            'premises': [self._forward.premise_label(p) for p in premises], 'proof': answer['proof']}

    def backward_found(self) -> dict[str, dict]:
        """Triples rules proved for questions: name → {atom, question, rule, premises, proof}."""
        return dict(self._backward_found)

    def restore_backward(self, found: dict[str, dict]) -> None:
        """Triples proved for earlier questions, saved with the atomspace."""
        self._backward_found.update(found or {})

    def inferred(self) -> list[str]:
        """Atoms derived by the rules: forward chaining's, then those proved for questions."""
        if self._forward is None:
            return []
        self.refresh_rules()
        from pln.chaining.forward_chainer import _parse_result
        forward = self._forward.run()
        seen = {tuple(_parse_result(a)[1:4]) for a in forward}            # same triple found both ways: once
        return forward + [v['atom'] for v in self._backward_found.values()
                          if tuple(_parse_result(v['atom'])[1:4]) not in seen]

    def all_facts(self) -> list[str]:
        """Return all atoms in the atomspace (facts + inferred)."""
        if self._forward is None:
            return []
        return list(self._original_atoms) + self.inferred()

    def run_inference(self) -> None:
        """Run PLN forward chaining to materialize inferred atoms."""
        if not self._cfg.enabled:
            return
        self._ensure_components()
        self.refresh_rules()
        self._forward.materialize(steps=self._cfg.forward_steps)

    def generate_visualization(self, run_dir: str | Path, paper_id: str = '') -> Path | None:
        """Forward-chain and write pln_graph.html; None when PLN is disabled."""
        if not self._cfg.enabled:
            return None
        self._ensure_components()

        # Materialize inferred atoms via forward chaining
        self.refresh_rules()
        self._forward.materialize(steps=self._cfg.forward_steps)

        all_atoms = self.all_facts()

        # Proof traces for the visualizer, and how each inferred triple was found
        merged_meta = dict(self._atom_meta)
        for atom_name, proof in self._forward.derivations().items():
            merged_meta[atom_name] = {
                'reasoning':  '',
                'source':     '',
                'section':    '',
                'paper_url':  '',
                '_proof':     proof,  # full derivation trace for buildStvDetail
                '_via':       'forward',
            }
        for atom_name, found in self._backward_found.items():
            merged_meta.setdefault(atom_name, {
                'reasoning': '', 'source': '', 'section': '', 'paper_url': '',
                '_proof': {'rule': found['rule'],
                           'premise1': (found['premises'] + [''])[0], 'premise2': (found['premises'] + ['', ''])[1]},
                '_via': 'backward', '_question': found['question'],
            })

        from pln.writer.pln_visualizer import generate_pln_html
        out_path = Path(run_dir) / "pln_graph.html"
        return generate_pln_html(
            original_atoms=self._original_atoms,
            all_atoms=all_atoms,
            out_path=out_path,
            paper_id=paper_id,
            atom_meta=merged_meta,
            label_map=self._label_map,
            entities=self._adapter.entities,
        )

    def config_dict(self) -> dict:
        return self._cfg.as_dict()
