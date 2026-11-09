"""Build or refresh the local Ensembl gene index from Ensembl's latest released GTF files.

Usage: python -m tools.build_ensembl_local [species ...]     (default: ENSEMBL_LOCAL_SPECIES)
Run again after a new Ensembl release; the old index stays in use until the new one is complete.
"""
import sys

from src.ensembl_local import build

if __name__ == "__main__":
    print("Building the local Ensembl gene index…")
    result = build(sys.argv[1:] or None)
    print(f"Ensembl release {result['release']}: {sum(result['genes'].values()):,} genes in {len(result['genes'])} species")
