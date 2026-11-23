"""Inference rules from the schema: a relation implies its parent relations, a symmetric relation holds both ways,
a transitive one chains. Parents and symmetry are Biolink's, transitivity the Relation Ontology's, read from
src/schema/biolink_generated.py; a schema update changes the rules, and nothing is listed here."""
from pln.bridge.metta_adapter import _pascal

DEFINITIONAL = "(STV 1.0 1.0)"          # true by the schema's definition whenever the premises hold


def schema_rules(present: set[str]) -> list[dict]:
    """Rules whose premises use a relation the facts hold (others can never fire): name, MeTTa text, meaning."""
    try:
        from src.schema.biolink_generated import RELATION_PARENTS, SYMMETRIC, TRANSITIVE
    except ImportError:                                   # a schema without relation semantics: no schema rules
        return []
    rules = []
    for rel, parents in RELATION_PARENTS.items():
        r = _pascal(rel)
        if r not in present:
            continue
        for parent in parents:
            p = _pascal(parent)
            rules.append({'name': f"schema_isa_{rel}_{parent}",
                          'text': f"(Implication (And ({r} $a $b)) ({p} $a $b))",
                          'meaning': f"{rel.replace('_', ' ')} is a kind of {parent.replace('_', ' ')} (Biolink)"})
    for rel in SYMMETRIC:
        r = _pascal(rel)
        if r in present:
            rules.append({'name': f"schema_symmetric_{rel}",
                          'text': f"(Implication (And ({r} $a $b)) ({r} $b $a))",
                          'meaning': f"{rel.replace('_', ' ')} holds both ways (Biolink: symmetric)"})
    for rel in TRANSITIVE:
        r = _pascal(rel)
        if r in present:
            rules.append({'name': f"schema_transitive_{rel}",
                          'text': f"(Implication (And ({r} $a $b) ({r} $b $c)) ({r} $a $c))",
                          'meaning': f"{rel.replace('_', ' ')} chains: a→b and b→c give a→c (RO: transitive)"})
    for r in rules:
        r['stv'] = DEFINITIONAL
    return rules
