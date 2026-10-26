import re
import statistics
from collections import Counter

PAGE_BREAK = "\f"   # PDFHandler separates pages with this

# Repeated lines = page furniture; pages of short numbered lines = contents/index.
_FURNITURE_SHARE = 0.2
_PAGE_NUM_END = re.compile(r"(?:^|[\s.,])(?:\d{1,4}(?:[e–—-]\d{1,4})?|[ivxlcdm]{1,7})[,.;]?\s*$", re.I)
_TOC_MIN_LINES, _TOC_NUMBERED_SHARE, _TOC_MAX_MEDIAN_LEN = 15, 0.55, 25
_INDEX_NUMBERED_SHARE, _INDEX_MAX_MEDIAN_LEN = 0.65, 40
_E_RANGE = re.compile(r"\b(\d{1,4})e(\d{1,4})\b")                    # "233e237"
_E_NAME_JOIN = re.compile(r"\b([A-Z][a-z]{2,})e([A-Z][a-z]{2,})\b")    # "HutchinsoneGilford"
_DASH_GLYPH_MIN = 5


def _is_page_range(m) -> bool:
    """'233e237': both sides ≥2 digits, same length, second a bit larger — never '1e6' or '2e10'."""
    a, b = m.group(1), m.group(2)
    return len(a) >= 2 and len(a) == len(b) and 0 < int(b) - int(a) <= 200


_BLANK_PAGE = re.compile(r"^this page (?:is )?(?:intentionally )?left blank\.?$", re.I)

# Hyphenation at line breaks ("robust-\nness").
_LINE_BREAK_HYPHEN = re.compile(r"\b([A-Za-z]{2,})-[ \t]*\n[ \t]*([a-z]{2,})\b")
_INLINE_HYPHENATED = re.compile(r"\b([A-Za-z]{2,})-([a-z]{2,})\b")


# Reference entry start: "Surname, I. (Year)."
_AUTHOR = r"[A-Z][A-Za-z'’\-]+(?:\s[A-Z][A-Za-z'’\-]+){0,2},\s(?:[A-Z]\.\s?(?:-?[A-Z]\.\s?){0,3})"
_AUTHORS = rf"{_AUTHOR}(?:,?\s(?:&\s|and\s)?{_AUTHOR}){{0,30}}(?:,?\s(?:&\s)?et\sal\.)?"
_REF_HEAD = re.compile(rf"{_AUTHORS}\s*\((?:19|20)\d{{2}}[a-z]?\)\.")
# Reference entry end.
_REF_END = re.compile(
    r"https?://doi\.org/\S+|https?://\S+|doi:\s*\S+"
    r"|\d+\s*\(\d+\)\s*,\s*[\dA-Za-z]+[e–\-]?[\dA-Za-z]*\."
    r"|\b\d+[e–\-]\d+\."
    r"|\bPress\.|\bPublishers?\.|\bUniversity\.?"
)
_MAX_ENTRY = 700          # max chars per entry
_REF_HEADING = re.compile(
    r"\b(?:References(?:\s+and\s+further\s+reading)?|Further\s+reading|Bibliography|Literature\s+Cited|Works\s+Cited)\b"
    r"(?=\s*(?:[A-Z]|https?://|$))")
# DOI/URL trailing an entry.
_TRAILING_LINK = re.compile(r"\s*(?:https?://\S+|doi:\s*\S+)")


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

    def __init__(self):
        self.references_removed = 0
        self.furniture_lines_removed = 0
        self.toc_pages_removed = 0
        self.hyphens_rejoined = 0
        self.dashes_repaired = 0

    def clean(self, text: str) -> str:
        text = self._repair_dash_glyphs(text)
        text = self._remove_page_noise(text)
        text = self._rejoin_hyphenation(text)
        text = self._remove_reference_entries(text)
        text = self._remove_boilerplate(text)
        text = self._normalize_whitespace(text)
        return text.strip()

    def _repair_dash_glyphs(self, text: str) -> str:
        """Restore en dashes some PDF fonts extract as 'e' (only if the document shows it)."""
        self.dashes_repaired = 0
        if PAGE_BREAK not in text:                       # only PDF text has this glyph defect
            return text
        ranges = [m for m in _E_RANGE.finditer(text) if _is_page_range(m)]
        if len(ranges) < _DASH_GLYPH_MIN:
            return text

        def fix_range(m):
            if not _is_page_range(m):                    # e.g. scientific notation "1e6"
                return m.group(0)
            self.dashes_repaired += 1
            return f"{m.group(1)}\u2013{m.group(2)}"

        def fix_name(m):
            self.dashes_repaired += 1
            return f"{m.group(1)}\u2013{m.group(2)}"

        text = _E_RANGE.sub(fix_range, text)
        return _E_NAME_JOIN.sub(fix_name, text)

    def _remove_page_noise(self, text: str) -> str:
        """Drop page furniture and contents/index pages (needs PAGE_BREAK-separated pages)."""
        pages = text.split(PAGE_BREAK)
        self.furniture_lines_removed = self.toc_pages_removed = 0
        if len(pages) < 4:
            return text

        key = lambda line: re.sub(r"^\d+\s*|\s*\d+$", "#", line.strip().lower())  # page numbers sit at line edges
        seen = Counter()
        for page in pages:
            seen.update({key(l) for l in page.split("\n") if len(l.strip()) >= 8})
        threshold = max(3, int(len(pages) * _FURNITURE_SHARE))
        furniture = {k for k, n in seen.items() if n >= threshold}

        kept = []
        for page in pages:
            lines = [l for l in page.split("\n") if l.strip()]
            body = [l for l in lines if key(l) not in furniture and not _BLANK_PAGE.match(l.strip())]
            self.furniture_lines_removed += len(lines) - len(body)
            if self._is_toc_or_index(body):
                self.toc_pages_removed += 1
                continue
            kept.append("\n".join(body))
        return PAGE_BREAK.join(kept)

    def _rejoin_hyphenation(self, text: str) -> str:
        """Rejoin words split across PDF lines, keeping hyphens the document uses elsewhere."""
        compounds = {m.group(0).lower() for m in _INLINE_HYPHENATED.finditer(text)}
        self.hyphens_rejoined = 0

        def join(m):
            first, second = m.group(1), m.group(2)
            if f"{first}-{second}".lower() in compounds:
                return f"{first}-{second}"
            self.hyphens_rejoined += 1
            return first + second

        return _LINE_BREAK_HYPHEN.sub(join, text)

    @staticmethod
    def _is_toc_or_index(lines: list) -> bool:
        if len(lines) < _TOC_MIN_LINES:
            return False
        numbered = sum(1 for l in lines if _PAGE_NUM_END.search(l.strip())) / len(lines)
        median_len = statistics.median(len(l.strip()) for l in lines)
        return ((numbered >= _TOC_NUMBERED_SHARE and median_len <= _TOC_MAX_MEDIAN_LEN)
                or (numbered >= _INDEX_NUMBERED_SHARE and median_len <= _INDEX_MAX_MEDIAN_LEN))

    def _remove_reference_entries(self, text: str) -> str:
        """Drop bibliography entries."""
        heads = list(_REF_HEAD.finditer(text))
        if len(heads) < 2:
            self.references_removed = 0
            return text

        spans = []
        for i, head in enumerate(heads):
            start = head.start()
            limit = min(len(text), start + _MAX_ENTRY)
            next_start = heads[i + 1].start() if i + 1 < len(heads) else None
            if next_start is not None and next_start <= limit:
                end = next_start                                  # entry runs into the next one
            else:
                tail = _REF_END.search(text, head.end(), limit)
                if not tail:
                    continue                                      # no recognisable ending → leave it
                end = tail.end()
            link = _TRAILING_LINK.match(text, end)
            if link:
                end = link.end()
            spans.append((start, end))

        if len(spans) < 2:
            self.references_removed = 0
            return text

        out, pos = [], 0
        for start, end in spans:
            if start < pos:
                continue
            out.append(text[pos:start])
            out.append(" ")
            pos = end
        out.append(text[pos:])
        self.references_removed = len(spans)
        cleaned = "".join(out)
        return _REF_HEADING.sub(" ", cleaned)

    def _remove_boilerplate(self, text: str) -> str:
        for pattern in self.BOILERPLATE_PATTERNS:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        return text

    def _normalize_whitespace(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        return text
