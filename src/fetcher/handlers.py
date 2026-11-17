import re
import json
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import fitz

PAGE_BREAK = "\f"


class JSONHandler:
    def extract(self, content: str, text_field: str = None) -> str:
        data = json.loads(content)
        
        # if text_field specified try that first
        if text_field and text_field in data:
            return str(data[text_field])
        
        # otherwise extract all string values from entire JSON
        return self._extract_all_text(data)

    def _extract_all_text(self, data) -> str:
        texts = []
        if isinstance(data, dict):
            for value in data.values():
                texts.append(self._extract_all_text(value))
        elif isinstance(data, list):
            for item in data:
                texts.append(self._extract_all_text(item))
        elif isinstance(data, str) and data.strip():
            texts.append(data.strip())
        return " ".join(filter(None, texts))


class XMLHandler:
    # Pure noise patterns: numeric/date tokens, short uppercase codes, DOI prefixes
    _NOISE = re.compile(
        r'^[\d\s\-\/\.\:\,\(\)\[\]]+$'
        r'|^[A-Z0-9]{1,6}$'
        r'|^10\.\d{4,}\/'
    )

    def extract(self, content: str, text_field: str = None) -> str:
        root = ET.fromstring(content)

        # config override: caller knows exactly which field to use
        if text_field:
            texts = [
                "".join(el.itertext()).strip()
                for el in root.iter(text_field)
            ]
            result = " ".join(t for t in texts if self._is_readable(t))
            if result:
                return result

        # PMC full paper: has a structured <body> with named sections
        if root.find(".//body") is not None:
            return self._extract_pmc_sections(root)

        # universal fallback: walk every text node, keep readable chunks
        return self._collect_text(root)

    def _collect_text(self, root) -> str:
        chunks = []
        for el in root.iter():
            for raw in (el.text, el.tail):
                if raw and raw.strip():
                    chunk = raw.strip()
                    if self._is_readable(chunk):
                        chunks.append(chunk)
        return " ".join(chunks)

    def _is_readable(self, text: str) -> bool:
        if len(text) < 3:
            return False
        if self._NOISE.match(text):
            return False
        # must contain at least one real word (3+ consecutive letters)
        return bool(re.search(r'[a-zA-Z]{3,}', text))

    # Back matter that is not paper content
    _SKIP = {"title", "label", "ref-list", "fn-group", "ack", "glossary"}

    def _extract_pmc_sections(self, root) -> str:
        # Each paragraph once, in document order; each section title once, on its own line
        sections = []

        def emit(paragraphs):
            if paragraphs:
                sections.append(" ".join(paragraphs))

        def walk(el):
            paragraphs = []
            for child in el:
                if child.tag == "p":
                    text = "".join(child.itertext()).strip()
                    if text:
                        paragraphs.append(text)
                elif child.tag not in self._SKIP:
                    emit(paragraphs)
                    paragraphs = []
                    t = child.find("title") if child.tag == "sec" else None
                    title = " ".join("".join(t.itertext()).split()) if t is not None else ""
                    if title:
                        sections.append(title)          # also for sections made only of subsections
                    walk(child)                         # subsections, figures, lists, boxed text
            emit(paragraphs)

        for part in (root.find(".//body"), root.find(".//back")):
            if part is not None:
                walk(part)

        abstract = root.find(".//abstract")
        if abstract is not None:
            abstract_text = " ".join("".join(p.itertext()).strip() for p in abstract.iter("p"))
            if abstract_text:
                sections.insert(0, f"abstract\n{abstract_text}")
        
        return "\n".join(sections)


class HTMLHandler:
    def extract(self, content: str, text_field: str = None) -> str:
        soup = BeautifulSoup(content, "html.parser")

        # remove scripts, styles, navigation, footer boilerplate
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # if text_field specified try to find that tag
        if text_field:
            element = soup.find(text_field)
            if element:
                return element.get_text(separator=" ", strip=True)

        # otherwise extract all visible text
        return soup.get_text(separator=" ", strip=True)


class TextHandler:
    def extract(self, content: str, text_field: str = None) -> str:
        lines = content.splitlines()

        # if a field label is specified, return only lines that match it
        if text_field:
            matched = [
                line.split(":", 1)[1].strip()
                for line in lines
                if line.lower().startswith(text_field.lower() + ":")
            ]
            if matched:
                return " ".join(matched)

        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # skip label-only lines with no value (e.g. "FTP download:")
            if ":" in line and not line.split(":", 1)[1].strip():
                continue
            kept.append(line)
        return " ".join(kept)


class PDFHandler:
    def extract(self, content, text_field: str = None) -> str:
        if isinstance(content, bytes):
            # online PDF — content is raw bytes
            doc = fitz.open(stream=content, filetype="pdf")
        elif isinstance(content, str) and content.startswith("http"):
            # URL string — fetch and open
            import requests
            response = requests.get(content)
            doc = fitz.open(stream=response.content, filetype="pdf")
        else:
            # local file path
            doc = fitz.open(content)
        from src.figures.ocr import page_text
        from src.fetcher.pdf_glyphs import repair_glyphs
        texts = [repair_glyphs(page, page_text(page)) for page in doc]   # OCR for scanned pages; symbol glyphs
        return PAGE_BREAK.join(texts)   # page boundaries kept for Layer 3 cleaning

class DocxHandler:
    """Word .docx: paragraph text from word/document.xml (headings become section lines)."""
    _W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def extract(self, content: bytes, text_field: str = None) -> str:
        from src.fetcher.archive import bounded_zip
        root = ET.fromstring(bounded_zip(content).read("word/document.xml"))
        lines = []
        for p in root.iter(f"{self._W}p"):
            parts = []
            for el in p.iter():
                if el.tag == f"{self._W}t":
                    parts.append(el.text or "")
                elif el.tag == f"{self._W}tab":
                    parts.append("\t")
                elif el.tag in (f"{self._W}br", f"{self._W}cr"):
                    parts.append("\n")
            lines += [line.strip() for line in "".join(parts).split("\n") if line.strip()]
        return "\n".join(lines)


class EpubHandler:
    """EPUB: chapter text in reading order (OPF spine)."""

    def extract(self, content: bytes, text_field: str = None) -> str:
        import posixpath
        from urllib.parse import unquote, urlsplit
        from src.fetcher.archive import bounded_zip
        zf = bounded_zip(content)
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        opf_path = next(el.get("full-path") for el in container.iter() if el.get("full-path"))
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        items = {el.get("id"): el.get("href") for el in opf.iter() if el.tag.endswith("item")}
        order = [el.get("idref") for el in opf.iter() if el.tag.endswith("itemref")]
        texts = []
        for idref in order:
            href = items.get(idref)
            if not href:
                continue
            try:
                path = unquote(urlsplit(href).path)               # a URI: decode, drop fragment and query
                html = zf.read(posixpath.normpath(posixpath.join(base, path)))
            except KeyError:
                continue
            texts.append(HTMLHandler().extract(html.decode("utf-8", errors="ignore")))
        return PAGE_BREAK.join(t for t in texts if t)


class MarkdownHandler:
    """Markdown: text without markup; headings kept as their own lines."""

    def extract(self, content: str, text_field: str = None) -> str:
        text = re.sub(r"```.*?```", " ", content, flags=re.S)                 # code blocks
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                     # images
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)                  # links → their text
        text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)                     # heading marks
        text = re.sub(r"(?m)^\s*([-*+]|\d+\.)\s+", "", text)                  # list markers
        text = re.sub(r"(?m)^\s{0,3}(>\s?)+", "", text)                        # blockquote markers at line start
        text = re.sub(r"`([^`\n]+)`", r"\1", text)                             # inline code
        text = re.sub(r"(?<!\w)(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", text)  # emphasis pairs only
        return "\n".join(l.strip() for l in text.splitlines() if l.strip())


class BioCHandler:
    """BioC XML or JSON: passage text, with section types as headings when present."""

    def extract(self, content, text_field: str = None) -> str:
        content = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
        out = []
        if content.lstrip().startswith(("{", "[")):
            data = json.loads(content)
            docs = data if isinstance(data, list) else [data]
            for collection in docs:
                for doc in collection.get("documents", [collection]):
                    for p in doc.get("passages", []):
                        section = (p.get("infons") or {}).get("section_type", "")
                        if p.get("text"):
                            out.append(f"{section.lower()}\n{p['text']}" if section else p["text"])
        else:
            root = ET.fromstring(content)
            for p in root.iter("passage"):
                section = next((i.text for i in p.iter("infon") if i.get("key") == "section_type"), "")
                text = (p.findtext("text") or "").strip()
                if text:
                    out.append(f"{section.lower()}\n{text}" if section else text)
        return "\n".join(out)
