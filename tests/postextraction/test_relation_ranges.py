"""Relation ranges from the taxonomy: swap backwards facts, drop facts no relation fits."""
from src.postextraction.postextractor import fit_relation_targets


def _r(s, st, rel, o, ot):
    return {"subject_name": s, "subject_type": st, "subject_id": "S:1", "relation": rel,
            "object_name": o, "object_type": ot, "object_id": "O:1"}


def test_backwards_fact_is_swapped():
    r = _r("ESRR", "PROTEIN", "open_chromatin_at", "gastric epithelial cells", "CELL_TYPE")
    assert fit_relation_targets([r]) == (1, 0)
    assert (r["subject_name"], r["object_name"], r["subject_id"]) == ("gastric epithelial cells", "ESRR", "O:1")


def test_member_of_a_cell_type_is_not_a_process():
    r = _r("SOX10", "PROTEIN", "participates_in", "oligodendrocytes", "CELL_TYPE")
    assert fit_relation_targets([r]) == (0, 1) and "range" in r["invalid_reason"]


def test_fitting_target_is_kept():
    r = _r("cardiac fibroblasts", "CELL_TYPE", "open_chromatin_at", "GATA4", "GENE")
    assert fit_relation_targets([r]) == (0, 0)
