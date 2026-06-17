"""
Layer 4 — Accession Detector

Extracts database accession numbers from text using regex patterns.
Supports GEO, ClinicalTrials, UniProt, PubMed, ArrayExpress, BioProject, SRA.
"""
import re


class AccessionDetector:
    _PATTERNS = {
        "GEO":            re.compile(r'\bGSE\d+\b'),
        "ClinicalTrials": re.compile(r'\bNCT\d{8}\b'),
        "UniProt":        re.compile(r'\b[OPQ][0-9][A-Z0-9]{3}[0-9]\b'),
        "PubMed":         re.compile(r'\bPMID:?\s*\d+\b'),
        "ArrayExpress":   re.compile(r'\bE-[A-Z]{4}-\d+\b'),
        "BioProject":     re.compile(r'\bPRJNA\d+\b'),
        "SRA":            re.compile(r'\bSRP\d+\b|\bSRR\d+\b'),
    }

    def extract(self, text: str) -> list:
        found = []
        for database, pattern in self._PATTERNS.items():
            for match in pattern.finditer(text):
                found.append({
                    "accession": match.group(0).strip(),
                    "database":  database,
                })
        return found
