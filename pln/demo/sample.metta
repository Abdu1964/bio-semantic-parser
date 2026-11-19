; ── Demo sample atomspace — Alzheimer's disease mini-KG ─────────────────────
; Run via:  python pln/demo/run_demo.py
; ─────────────────────────────────────────────────────────────────────────────

; Facts (from Alzheimer paper Layer 8 output)
(: binds_to_HGNC17761_HGNC613
   (Binds_To (protein HGNC_17761) (protein HGNC_613))
   (STV 0.85 0.90))

(: inhibits_CHEBI28748_HGNC5358
   (Inhibits (small_molecule CHEBI_28748) (protein HGNC_5358))
   (STV 0.90 0.88))

(: activates_HGNC5358_HGNC9170
   (Activates (protein HGNC_5358) (protein HGNC_9170))
   (STV 0.80 0.82))

(: causes_HGNC9170_MESH_D000544
   (Causes (protein HGNC_9170) (disease MESH_D000544))
   (STV 0.78 0.75))

(: assoc_HGNC613_MESH_D000544
   (Is_Associated_With (protein HGNC_613) (disease MESH_D000544))
   (STV 0.72 0.70))

(: treats_CHEBI28748_MESH_D000544
   (Treats (small_molecule CHEBI_28748) (disease MESH_D000544))
   (STV 0.65 0.60))

; HoTT identity example — same gene, different paper name
(: identity_HGNC5358_BACE1
   (IdenticalTo (protein HGNC_5358) (protein BACE1))
   (STV 0.95 0.90))
