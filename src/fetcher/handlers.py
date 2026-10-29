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

    def _extract_pmc_sections(self, root) -> str:
        sections = []
        for sec in root.iter("sec"):
            title = sec.find("title")
            title_text = title.text.strip() if title is not None and title.text else "unknown"
            paragraphs = []
            for p in sec.iter("p"):
                texts = "".join(p.itertext()).strip()
                if texts:
                    paragraphs.append(texts)
            if paragraphs:
                sections.append(f"{title_text}\n" + " ".join(paragraphs))
        
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
        texts = [page_text(page) for page in doc]          # OCR for scanned pages
        return PAGE_BREAK.join(texts)   # page boundaries kept for Layer 3 cleaning

class DocxHandler:
    """Word .docx: paragraph text from word/document.xml (headings become section lines)."""
    _W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def extract(self, content: bytes, text_field: str = None) -> str:
        import io, zipfile
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml"))
        lines = []
        for p in root.iter(f"{self._W}p"):
            text = "".join(t.text or "" for t in p.iter(f"{self._W}t")).strip()
            if text:
                lines.append(text)
        return "\n".join(lines)


class EpubHandler:
    """EPUB: chapter text in reading order (OPF spine)."""

    def extract(self, content: bytes, text_field: str = None) -> str:
        import io, posixpath, zipfile
        zf = zipfile.ZipFile(io.BytesIO(content))
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
                html = zf.read(posixpath.normpath(posixpath.join(base, href)))
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
        text = re.sub(r"[*_`>]{1,3}", "", text)                               # emphasis, quotes, code
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
