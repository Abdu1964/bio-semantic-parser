"""Relation parents, symmetry and transitivity generated from Biolink and RO refer only to KG relations."""
from src.schema import biolink_generated as G
from src.schema.taxonomy import RelationType

KG = {r.value for r in RelationType}


def test_everything_named_is_a_kg_relation():
    named = set(G.RELATION_PARENTS) | {p for ps in G.RELATION_PARENTS.values() for p in ps} | set(G.SYMMETRIC) | set(G.TRANSITIVE)
    assert named <= KG


def test_no_relation_is_its_own_parent():
    assert all(r not in ps for r, ps in G.RELATION_PARENTS.items())


def test_known_semantics():
    assert "interacts_with" in G.RELATION_PARENTS["binds_to"]
    assert "part_of" in G.TRANSITIVE and "interacts_with" in G.SYMMETRIC
    assert "causes" not in G.SYMMETRIC
