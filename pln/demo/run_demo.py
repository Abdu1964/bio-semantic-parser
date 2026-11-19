"""Demo: forward chaining and an NL query over sample.metta. Run: python pln/demo/run_demo.py"""
import os
import sys
from pathlib import Path

# Allow running from project root or from pln/demo/
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

os.environ.setdefault("ENABLE_PLN", "true")

from pln import PLNModule
from pln.config import PLNConfig
from pln.query.answer_generator import generate_answer


def main():
    cfg = PLNConfig.from_env()
    pln = PLNModule(cfg)

    sample = Path(__file__).parent / "sample.metta"
    print(f"\n── Loading sample atoms from {sample.name} ──")
    atoms = pln._adapter or _lazy_adapter(pln)
    pln._ensure_components()
    demo_atoms = pln._adapter.load_file(sample)
    pln._forward.load_atoms(demo_atoms)
    pln._original_atoms.extend(demo_atoms)
    print(f"  Loaded {len(demo_atoms)} fact atoms")

    print("\n── Running forward chaining ──")
    pln._forward.materialize(steps=cfg.forward_steps)
    all_facts = pln._forward.all_facts()
    print(f"  Atomspace now has {len(all_facts)} atoms (original + inferred)")

    questions = [
        "What does CHEBI_28748 inhibit?",
        "What does HGNC_5358 activate?",
        "What treats MESH_D000544?",
        "What causes MESH_D000544?",
    ]

    print("\n── NL Query demo ──")
    for q in questions:
        result = pln.ask(q)
        print(f"\nQ: {q}")
        print(generate_answer(result))

    print("\n── Inferred atoms (not in original facts) ──")
    from pln.chaining.truth_revision import _atom_key
    original_keys = {_atom_key(a) for a in pln._original_atoms}
    inferred = [a for a in all_facts if _atom_key(a) not in original_keys]
    for atom in inferred[:10]:
        print(" ", atom)
    if len(inferred) > 10:
        print(f"  … and {len(inferred)-10} more")

    print("\nDemo complete.")


def _lazy_adapter(pln):
    pln._ensure_components()
    return pln._adapter


if __name__ == "__main__":
    main()
