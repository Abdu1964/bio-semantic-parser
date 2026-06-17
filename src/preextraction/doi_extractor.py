"""
Layer 4 — DOI Extractor

Extracts the first DOI found in a text string using the standard
10.NNNN/suffix pattern registered with CrossRef.
"""
import re
from typing import Optional


class DOIExtractor:
    _PATTERN = re.compile(r'10\.\d{4,}\/[^\s]+')

    def extract(self, text: str) -> Optional[str]:
        match = self._PATTERN.search(text)
        return match.group(0).rstrip('.,;)') if match else None
