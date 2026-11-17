"""Build or refresh the full local copies of lookup sources.

Usage: python -m tools.build_local_sources [source ...]
Sources: hgnc, ncbi_gene, ncbitaxon, mesh, or an ontology name (mondo, go, chebi, …); default: all of them.
Run again for a new release; each copy stays in use until its replacement is complete.
"""
import sys

from src.local_sources import hgnc, ncbi_gene, ontology

if __name__ == "__main__":
    wanted = sys.argv[1:] or ["hgnc", "ncbi_gene", "ncbitaxon", "mesh"] + ontology.default_ontologies()
    print(f"Building local copies: {', '.join(wanted)}")
    for name in dict.fromkeys(wanted):
        try:
            if name == "hgnc":
                hgnc.build()
            elif name == "ncbi_gene":
                ncbi_gene.build()
            else:
                ontology.build(name)
        except Exception as e:                       # one source failing never stops the others
            print(f"  {name}: failed — {e}")
