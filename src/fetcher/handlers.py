import json
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import fitz


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
    def extract(self, content: str, text_field: str = None) -> str:
        root = ET.fromstring(content)

        # if text_field specified try that first
        if text_field:
            elements = root.iter(text_field)
            texts = [el.text for el in elements if el.text]
            if texts:
                return " ".join(texts)

        # check if this is a PMC full paper
        body = root.find(".//body")
        if body is not None:
            return self._extract_pmc_sections(root)

        # fallback extract all text
        texts = []
        for element in root.iter():
            if element.text and element.text.strip():
                texts.append(element.text.strip())
            if element.tail and element.tail.strip():
                texts.append(element.tail.strip())
        return " ".join(texts)

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
        texts = [page.get_text() for page in doc]
        return " ".join(texts)