; ─────────────────────────────────────────────────────────────────────────────
; PLN Standard Functions — bio-semantic-parser Layer 9
; ─────────────────────────────────────────────────────────────────────────────
; Source:    https://github.com/trueagi-io/PeTTa/blob/main/lib/lib_pln.metta
; Reference: PLN Book — Goertzel et al., Probabilistic Logic Networks (2009)
; Used in:   src/pln/layer9_pln.py
; ─────────────────────────────────────────────────────────────────────────────

; ── Confidence ↔ Weight conversion ───────────────────────────────────────────

; Convert confidence → weight   (PLN internal representation)
; w = c / (1 - c)
(: Truth_c2w (-> Number Number))
(= (Truth_c2w $c)
   (/ $c (- 1 $c)))

; Convert weight → confidence   (human-readable 0–1 value)
; c = w / (w + 1)
(: Truth_w2c (-> Number Number))
(= (Truth_w2c $w)
   (/ $w (+ $w 1)))

; ── Revision Rule — PLN Book page 116 ────────────────────────────────────────
; Combines two independent truth values for the SAME statement.
; Used when the same triple appears in two independent papers.
;
; w1 = c1 / (1-c1)
; w2 = c2 / (1-c2)
; w  = w1 + w2
; f  = (w1×f1 + w2×f2) / w     weighted average of strengths
; c  = w / (w+1)
;
(= (Truth_Revision (stv $f1 $c1) (stv $f2 $c2))
   (let* (($w1 (Truth_c2w $c1))
          ($w2 (Truth_c2w $c2))
          ($w  (+ $w1 $w2))
          ($f  (/ (+ (* $w1 $f1) (* $w2 $f2)) $w))
          ($c  (Truth_w2c $w)))
         (stv (min 1.0 $f)
              (min 1.0 (max (max $c $c1) $c2)))))

; ── Deduction Rule — PLN Book page 15 ────────────────────────────────────────
; A→B + B→C → derives A→C
; Used in Layer 9 forward chaining inference.
;
; s = PQs × QRs + ((1-PQs) × (Rs - Qs×QRs)) / (1-Qs)
; c = min(Pc, Qc, Rc, PQc, QRc)
;
(= (Truth_Deduction (stv $Ps $Pc)
                    (stv $Qs $Qc)
                    (stv $Rs $Rc)
                    (stv $PQs $PQc)
                    (stv $QRs $QRc))
   (stv (if (< 0.9999 $Qs)
            $Rs
            (+ (* $PQs $QRs)
               (/ (* (- 1 $PQs) (- $Rs (* $Qs $QRs)))
                  (- 1 $Qs))))
        (min $Pc (min $Qc (min $Rc (min $PQc $QRc))))))

; ── Negation — used in contradiction resolution ───────────────────────────────
; Inverts strength, preserves confidence.
;
(= (Truth_Negation (stv $s $c))
   (stv (- 1.0 $s) $c))
