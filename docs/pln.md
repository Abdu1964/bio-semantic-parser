# PLN in the Bio-Semantic Parser

How the parser's knowledge graph is used for PLN (Probabilistic Logic Networks) reasoning with
PeTTaChainer, which files do what, and how a request flows. Branch: `BSP-PLN`.

PLN is optional. With `ENABLE_PLN=false` the pipeline (Layers 1–8) runs exactly as without it.

---

## 1. What PLN adds

| Without PLN | With PLN |
|---|---|
| Facts extracted from a paper (subject – relation – object) | The same facts as PLN atoms, each with a truth value `(STV strength confidence)` |
| — | New relations inferred by rules (A inhibits B + B inhibits C ⇒ A activates C), each with a proof |
| — | Questions answered from facts and inferences ("what does adenosine activate?") |
| — | A graph of original facts (solid) and inferred ones (dashed purple) |

---

## 2. Flow

```
 Layers 1–7  ──►  Layer 8 writes the paper's facts
                    run_dir/metta/<type>/edges_*.metta   (MeTTa facts)
                    data/staging/<paper>_<fmt>.db         (facts with confidence, papers, negation)
                           │
     ┌─────────────────────┴───────────────────────────────┐
     │ A. Automatic, after each paper (Layer 8)            │ B. On demand (UI / API)
     │    optional_modules.run_module("pln")               │    "Run PLN Inference", "PLN Query",
     │    → pln/integration/pipeline.run                   │    unified PLN AtomSpace
     │    → pln/legacy/layer9_pln.py                       │    → pln/integration/api_routes.py
     │      revision · deduction · contradictions          │    → PLNModule (PeTTaChainer)
     │    → run_dir/pln/pln_graph.html                     │
     └─────────────────────────────────────────────────────┘

 B in detail (PeTTaChainer path):

   1. Load        bridge/metta_adapter.py
                  edges_*.metta  ──►  (: name (Inhibits (protein a) (pathway b)) (STV s c))
                  s = the fact's confidence score, c = N/(N+1) over its source papers   (staging DB)
                  negated facts are skipped
   2. Rules       rules/bio_rules.metta + lib/lib_pln.metta  ──►  added to PeTTaChainer
   3. Forward     chaining/forward_chainer.py
                  for each predicate a rule concludes: PeTTaChainer.query("(: $prf (P $x $y) $tv)")
                  results with a proof "(by rule (conjunction f1 f2))" = inferred atoms
   4. Write       writer/pln_visualizer.py  ──►  run_dir/pln_graph.html
                                                  run_dir/pln_original|inferred|atomspace.metta
                                                  run_dir/pln_metta/<type>/…   run_dir/manifest.json
   5. Query       query/nl_to_pln.py   "what does X inhibit"  ──►  (: $prf (Inhibits (type x) $target) $tv)
                  query/reasoner.py    ──►  chaining/backward_chainer.py  ──►  PeTTaChainer.query
                  answers: facts and inferred relations, with STV and proof, sorted by confidence
```

Each per-paper run (`/api/pln-run`, or `/api/pln-query` with a `run_dir`) starts with a fresh atomspace,
so papers never mix. The unified atomspace (`/api/pln-unified-*`) loads the latest run of every paper.

---

## 3. How PeTTaChainer is used

PeTTaChainer (`pettachainer.pettachainer.PeTTaChainer`) is a PLN chainer on the PeTTa MeTTa runtime.

| Call | Where | What for |
|---|---|---|
| `PeTTaChainer()` | `ForwardChainer._ensure_handler` | one knowledge base per atomspace |
| `add_atom(atom)` | `ForwardChainer.load_atoms`, `load_rules_file` | facts and rules |
| `query(goal, steps, timeout_sec)` | `ForwardChainer.materialize`, `BackwardChainer.query` | proofs for a goal; rules are applied during the search |

Atom formats:

```metta
; fact
(: inhibits_adenosine_immune_response
   (Inhibits (small_molecule adenosine) (biological_process immune_response))
   (STV 0.900 0.500))

; rule (PeTTaChainer syntax: one antecedent, And for several premises)
(: rule_double_inhibition_activates
   (Implication
     (And
       (Inhibits $a $b)
       (Inhibits $b $c))
     (Activates $a $c))
   (STV 0.80 0.75))

; query and an answer
(: $prf (Activates (small_molecule adenosine) $target) $tv)
(: (by rule_double_inhibition_activates (conjunction f1 f2))
   (Activates (small_molecule adenosine) (cell_type malignant_cell))
   (STV 0.576 0.771))
```

PeTTaChainer computes the truth value of an inference itself (conjunction of the premises, then the
rule). A fact supported by one paper has confidence 0.5, so inferences from single-paper facts have low
confidence; it rises as more papers state the same facts.

Versions are pinned in the `Dockerfile` (PeTTa `e038e4d`, PeTTaChainer `b0e24f9`); newer PeTTaChainer
commits need a newer PeTTa.

---

## 4. Files

| File | Role |
|---|---|
| `pln/__init__.py` | `PLNModule`: load_paper → run_inference / inferred → ask → generate_visualization |
| `pln/config.py` | settings from the environment (section 6) |
| `pln/bridge/metta_adapter.py` | Layer 8 MeTTa files and staging DB → PeTTaChainer atoms with truth values |
| `pln/rules/bio_rules.metta` | 16 biological rules (activation/inhibition chains, causation, treatment, biomarkers…) |
| `pln/lib/lib_pln.metta` | PLN formula library (trueagi-io/PeTTa) |
| `pln/chaining/forward_chainer.py` | PeTTaChainer knowledge base; derives every rule conclusion with proofs |
| `pln/chaining/backward_chainer.py` | goal queries through `PeTTaChainer.query` |
| `pln/chaining/truth_revision.py` | merges duplicate atoms by PLN revision |
| `pln/identity/resolver.py` | `IdenticalTo` atoms for the same entity named differently across papers |
| `pln/query/nl_to_pln.py` | question templates → PLN queries; names resolved to `(type name)` |
| `pln/query/reasoner.py` | runs the query, returns answers (fact or inferred) |
| `pln/query/answer_generator.py` | answers as text and JSON |
| `pln/writer/pln_visualizer.py` | `pln_graph.html` and the PLN MeTTa files (`pln_metta/`) |
| `pln/writer/pln_writer.py` | writes inferred-only atoms |
| `pln/integration/api_routes.py` | API endpoints (section 5) |
| `pln/integration/pipeline.py` | `run()` (Layer 8, legacy Layer 9) and `run_v2()` |
| `pln/legacy/layer9_pln.py` | Layer 9: revision from verification, deduction, contradiction resolution |
| `tools/visualize_pln.py` | graph page for Layer 9 results |
| `optional_modules/__init__.py` | `run_module(name)` — plug-in loader used by Layer 8 |
| `api/app.py` | mounts the PLN routes when `ENABLE_PLN=true` |
| `frontend/static/index.html`, `state.js`, `js/main/12-pln-query.js` | Step 2 "PLN Inference", PLN query panel, unified PLN AtomSpace view |
| `pln/demo/run_demo.py` | stand-alone demo on `pln/demo/sample.metta` |
| `tools/backfill_pln_strength.py` | adds `(strength …)` to older per-paper MeTTa files |

---

## 5. API

| Endpoint | Does |
|---|---|
| `POST /api/pln-run` `{run_dir, staging_db}` | one paper: load, infer, write `pln_graph.html`; returns atoms loaded and inferred |
| `POST /api/pln-query` `{question, run_dir?, staging_db?}` | answers a question (for that paper when `run_dir` is given) |
| `GET /api/pln-facts`, `GET /api/pln-inferred` | atoms of the current atomspace |
| `POST /api/pln-unified-run`, `POST /api/pln-unified-query`, `GET /api/pln-unified-status`, `GET /api/pln-papers` | the atomspace of all papers |
| `POST /api/run_pln` | legacy Layer 9 analysis |

---

## 6. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ENABLE_PLN` | `false` | PLN routes, UI and the Layer 8 PLN step |
| `PLN_FORWARD_STEPS` | `5` | search depth for forward derivation |
| `PLN_BACKWARD_TIMEOUT` | `30` | seconds per PeTTaChainer query |
| `PLN_MIN_CONFIDENCE` | `0.5` | default confidence for MeTTa facts without a truth value |
| `PLN_IDENTITY_THRESHOLD` | `0.8` | name similarity for `IdenticalTo` |
| `ENABLE_PLN_MODULE` | `true` | lets Layer 8 call the PLN module |
| `ENABLE_ACCURACY_METRICS` | `false` | precision / recall / F1 panel in the verification report |

---

## 7. Accuracy metrics (same branch)

`optional_modules/accuracy.py` runs `tools/verify_kg.py` after Layer 8:

- **Precision** = confirmed ÷ (confirmed + mismatch), each KG fact checked against the paper text.
- **Recall** = ground-truth triples found in the KG ÷ all ground-truth triples. The ground truth is a second
  LLM extraction of the paper (first 12,000 characters); a triple counts as found when the relation is the
  same and subject and object contain each other.
- **F1** = 2·P·R ÷ (P + R).

Shown on the "Neo4j Verification" card and in the report's "KG ACCURACY METRICS" panel.
Known limits: the ground truth is another LLM run with looser rules, matching needs the exact relation,
and `_ABBREV_MAP` is a fixed abbreviation list.

---

## 8. Deployment (test server)

The PLN copy runs apart from the live parser:

| | Live | PLN test |
|---|---|---|
| Folder | `~/services/bio-semantic-parser` | `~/services/bsp-pln` |
| Compose project | `bio-semantic-parser` | `bsp-pln` |
| Port / URL | 8020 → `dev.rejuve.bio:5056` | 8024 → `dev.rejuve.bio:5057` |
| Flags | PLN off | `ENABLE_PLN=true`, `ENABLE_ACCURACY_METRICS=true` |

`bsp-pln` has its own `data/`, `.env.docker` and an untracked `docker-compose.override.yml` (port 8024,
frontend image tag `:pln`, env file). Update: `git pull`, then `docker compose -p bsp-pln up -d --build`
(code-only changes: `docker restart bsp-pln-api-1`).

---

## 9. Known limits

- Forward derivation lists what open queries find per predicate; a question about one entity can follow
  longer chains than the graph shows.
- Rules are hand-written biological patterns (`bio_rules.metta`); their truth values are set by hand.
- Inferences from single-paper facts have low confidence by design (section 3).
