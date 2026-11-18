"""Direction of reversed/restored findings."""
from src.postextraction.reversal_check import check


def _r(s, rel, o, ci="1"):
    return {"subject_name": s, "relation": rel, "object_name": o, "chunk_index": ci}


TEXT = ("After 5 days of RTD-1, levels of ACSL1, CXCL6, LPL, and SERPINE1 were reversed relative to PIA, "
        "reflecting the activation of PPARG.")


def test_reversal_takes_the_opposite_of_the_stated_change():
    recs = [_r("PIA", "upregulates", "CXCL6", "2"), _r("RTD-1", "upregulates", "CXCL6"),
            _r("RTD-1", "upregulates", "LPL"), _r("RTD-1", "upregulates", "SERPINE1"), _r("RTD-1", "activates", "PPARG")]
    assert check(recs, {"1": TEXT, "2": "CXCL6 rose in PIA."}) == 3
    assert [r["relation"] for r in recs] == ["upregulates", "downregulates", "regulates", "regulates", "activates"]


def test_reversibly_is_not_a_reversal():
    recs = [_r("RTD-1", "inhibits", "TACE")]
    assert check(recs, {"1": "RTD-1 reversibly inhibits TACE, MMPs, and cathepsins."}) == 0
