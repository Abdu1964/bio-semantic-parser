; ─────────────────────────────────────────────────────────────────────────────
; Biological Implication Rules — PeTTaChainer format
; Used by pln/chaining/forward_chainer.py and backward_chainer.py
; ─────────────────────────────────────────────────────────────────────────────
; Atom format: (: <name> (Implication (Premises <P1> <P2> ...) (Conclusions <C1>)) (STV <s> <c>))
; Variables:   $a  $b  $c  $t  $d  $g  $pw  $dr
; ─────────────────────────────────────────────────────────────────────────────

; ── Activation transitivity ──────────────────────────────────────────────────
; A activates B  +  B activates C  →  A activates C (indirect activation)
(: rule_activation_transitive
   (Implication
     (Premises
       (Activates $a $b)
       (Activates $b $c))
     (Conclusions
       (Activates $a $c)))
   (STV 0.85 0.80))

; ── Inhibition transitivity ──────────────────────────────────────────────────
; A inhibits B  +  B inhibits C  →  A activates C (double inhibition)
(: rule_double_inhibition_activates
   (Implication
     (Premises
       (Inhibits $a $b)
       (Inhibits $b $c))
     (Conclusions
       (Activates $a $c)))
   (STV 0.80 0.75))

; ── Cross-type inhibition chain ───────────────────────────────────────────────
; A inhibits B  +  B activates C  →  A inhibits C (indirect suppression)
(: rule_inhibit_then_activate_is_inhibit
   (Implication
     (Premises
       (Inhibits $a $b)
       (Activates $b $c))
     (Conclusions
       (Inhibits $a $c)))
   (STV 0.82 0.78))

; ── Activation then inhibition ────────────────────────────────────────────────
; A activates B  +  B inhibits C  →  A inhibits C (indirect suppression)
(: rule_activate_then_inhibit_is_inhibit
   (Implication
     (Premises
       (Activates $a $b)
       (Inhibits $b $c))
     (Conclusions
       (Inhibits $a $c)))
   (STV 0.82 0.78))

; ── Drug-protein-disease chain ────────────────────────────────────────────────
; Drug D inhibits Protein P  +  Protein P causes Disease X  →  D treats X
(: rule_inhibit_cause_implies_treats
   (Implication
     (Premises
       (Inhibits $dr $a)
       (Causes $a $d))
     (Conclusions
       (Treats $dr $d)))
   (STV 0.78 0.75))

; ── Binding implies potential activation ─────────────────────────────────────
; If A binds_to B and B activates C, A may indirectly activate C
(: rule_bind_activate_implies_activate
   (Implication
     (Premises
       (Binds_To $a $b)
       (Activates $b $c))
     (Conclusions
       (Activates $a $c)))
   (STV 0.70 0.65))

; ── Gene expression links gene to protein function ───────────────────────────
; Gene G is_associated_with Disease D  +  Gene G encodes Protein P
; → Protein P is_associated_with Disease D
(: rule_gene_disease_via_protein
   (Implication
     (Premises
       (Is_Associated_With $g $d)
       (Encodes $g $a))
     (Conclusions
       (Is_Associated_With $a $d)))
   (STV 0.80 0.75))

; ── Disease causation transitivity ───────────────────────────────────────────
; Condition A causes Condition B  +  Condition B causes Condition C
; → Condition A causes Condition C (disease cascade)
(: rule_causes_transitive
   (Implication
     (Premises
       (Causes $a $b)
       (Causes $b $c))
     (Conclusions
       (Causes $a $c)))
   (STV 0.75 0.70))

; ── Association transitivity ──────────────────────────────────────────────────
; A associates_with B  +  B associates_with C  →  A associates_with C
; (guilt by association: 5 bridge entities in data)
(: rule_associates_transitive
   (Implication
     (Premises
       (AssociatesWith $a $b)
       (AssociatesWith $b $c))
     (Conclusions
       (AssociatesWith $a $c)))
   (STV 0.65 0.60))

; ── Upregulation then inhibition ─────────────────────────────────────────────
; A upregulates B  +  B inhibits C  →  A indirectly inhibits C
; (5 bridge entities in data)
(: rule_upregulate_inhibit_chain
   (Implication
     (Premises
       (Upregulates $a $b)
       (Inhibits $b $c))
     (Conclusions
       (Inhibits $a $c)))
   (STV 0.72 0.65))

; ── Target binds drug then inhibits ──────────────────────────────────────────
; IsTargetOf A B  +  B inhibits C  →  A indirectly inhibits via target
; (5 bridge entities in data: proteins are targets and those proteins inhibit others)
(: rule_target_inhibit_chain
   (Implication
     (Premises
       (IsTargetOf $a $b)
       (Inhibits $b $c))
     (Conclusions
       (IsTargetOf $a $c)))
   (STV 0.68 0.62))

; ── IsVariantOf + Causes disease ─────────────────────────────────────────────
; A is_variant_of Gene G  +  G causes Disease D
; → Variant A associated with Disease D (3 bridge entities)
(: rule_variant_cause_disease
   (Implication
     (Premises
       (IsVariantOf $a $b)
       (Causes $b $c))
     (Conclusions
       (AssociatesWith $a $c)))
   (STV 0.70 0.65))

; ── Upregulation-disease association ─────────────────────────────────────────
; Protein P upregulates Disease Pathway PW  +  PW associated_with Disease D
; → P is_associated_with D
(: rule_upregulate_pathway_disease
   (Implication
     (Premises
       (Upregulates $a $pw)
       (Is_Associated_With $pw $d))
     (Conclusions
       (Is_Associated_With $a $d)))
   (STV 0.72 0.68))

; ── Downregulation protective ─────────────────────────────────────────────────
; Entity A downregulates Protein P  +  Protein P causes Disease D
; → A may protect against D
(: rule_downregulate_cause_protects
   (Implication
     (Premises
       (Downregulates $a $b)
       (Causes $b $d))
     (Conclusions
       (Protects_Against $a $d)))
   (STV 0.73 0.68))

; ── Co-expression implies functional similarity ───────────────────────────────
; A co_expressed_with B  +  B is_associated_with Disease D
; → A may also be associated with D
(: rule_coexpress_association
   (Implication
     (Premises
       (Co_Expressed_With $a $b)
       (Is_Associated_With $b $d))
     (Conclusions
       (Is_Associated_With $a $d)))
   (STV 0.65 0.60))

; ── Biomarker in disease context ─────────────────────────────────────────────
; Entity A is_biomarker_of Disease D  +  Drug DR treats D
; → DR may influence A (target relationship)
(: rule_biomarker_treatment_influence
   (Implication
     (Premises
       (Is_Biomarker_Of $a $d)
       (Treats $dr $d))
     (Conclusions
       (Inhibits $dr $a)))
   (STV 0.60 0.55))
