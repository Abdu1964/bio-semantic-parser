import re


class TextCleaner:

    BOILERPLATE_PATTERNS = [
        r"©.*?\.",
        r"copyright.*?\.",
        r"all rights reserved.*?\.",
        r"doi:.*?\s",
        r"pmid:.*?\s",
        r"epub ahead of print.*?\.",
        r"conflict of interest.*?\.",
        r"funding.*?\.",
        r"acknowledgement.*?\.",
        r"acknowledgment.*?\.",
    ]

    def clean(self, text: str) -> str:
        text = self._remove_boilerplate(text)
        text = self._normalize_whitespace(text)
        return text.strip()

    def _remove_boilerplate(self, text: str) -> str:
        for pattern in self.BOILERPLATE_PATTERNS:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        return text

    def _normalize_whitespace(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        return text