import os
import time
import tempfile
import requests
from dotenv import load_dotenv
from src.fetcher.format_detector import FormatDetector
from src.fetcher.handlers import JSONHandler, XMLHandler, HTMLHandler, PDFHandler
from src.fetcher.cleaner import TextCleaner
from src.fetcher.coref_client import CorefClient
from src.fetcher.section_splitter import SectionSplitter
from src.fetcher.chunker import Chunker
from src.fetcher.metadata_attacher import MetadataAttacher

load_dotenv()


class Fetcher:

    def __init__(self, coref_url: str):
        self.format_detector = FormatDetector()
        self.cleaner = TextCleaner()
        self.coref_client = CorefClient(coref_url)
        self.section_splitter = SectionSplitter()
        self.chunker = Chunker()
        self.metadata_attacher = MetadataAttacher()
        self.timeout = int(os.getenv("REQUEST_TIMEOUT", "30"))

    def fetch(self, url: str, source: dict, document_id: str, verbose: bool = False) -> list:

        def log(msg):
            if verbose:
                print(msg)

        # handle local file path
        if not url.startswith("http"):
            log("  Step 1 — Reading local file...")
            raw_text = PDFHandler().extract(url)
            log(f"           ✓ Extracted {len(raw_text):,} chars from file")
            return self._run_steps(raw_text, None, source, document_id, url, log)

        # Step 1 — fetch raw response
        log("  Step 1 — Fetching raw response...")
        self._rate_limit(source)
        response = requests.get(self._apply_api_key(url, source), timeout=self.timeout)
        content_type = response.headers.get("Content-Type", "")
        log(f"           ✓ {response.status_code} OK  |  Content-Type: {content_type}  |  Size: {len(response.content):,} bytes")

        return self._run_steps(None, response, source, document_id, url, log)

    def _run_steps(self, raw_text, response, source, document_id, url, log):
        # Step 2 — detect format and extract text
        log("  Step 2 — Detecting format & extracting text...")
        if raw_text is not None:
            text = raw_text
            log(f"           ✓ Local file — {len(text):,} chars")
        else:
            content_type = response.headers.get("Content-Type", "")
            format_type = self.format_detector.detect(content_type)
            text = self._extract_text(format_type, response, source)
            log(f"           ✓ Format: {format_type.upper()}  |  Extracted: {len(text):,} chars")

        # Step 3 — noise removal
        log("  Step 3 — Cleaning noise...")
        before_len = len(text)
        text = self.cleaner.clean(text)
        log(f"           ✓ {before_len:,} → {len(text):,} chars  ({before_len - len(text):,} removed)")

        # Step 4 — coreference resolution
        log("  Step 4 — Resolving coreferences...")
        coref_url = self.coref_client.base_url
        if self.coref_client.health_check():
            log(f"           Service : ONLINE  ({coref_url})")
            text_before = text
            text = self.coref_client.resolve(text)
            rewrites = _find_coref_rewrites(text_before, text)
            if rewrites:
                log(f"           Rewrites: {len(rewrites)} sentence(s) changed")
                for i, (before, after) in enumerate(rewrites[:3], 1):
                    log(f"           [{i}] BEFORE : {before}")
                    log(f"               AFTER  : {after}")
                if len(rewrites) > 3:
                    log(f"           ... and {len(rewrites) - 3} more")
            else:
                log(f"           ✓ No pronouns resolved (text may already be unambiguous)")
        else:
            log(f"           Service : OFFLINE  ({coref_url})")
            log(f"           ✓ Passing text through unchanged")

        # Step 5 — section splitting
        log("  Step 5 — Splitting into sections...")
        sections = self.section_splitter.split(text)
        section_names = [s["section"] for s in sections]
        log(f"           ✓ {len(sections)} section(s): {section_names}")

        # Step 6 — chunking
        log("  Step 6 — Chunking sections...")
        chunks = self.chunker.chunk_document(sections)
        log(f"           ✓ {len(chunks)} chunk(s) produced")

        # Step 7 — metadata attachment
        log("  Step 7 — Attaching metadata...")
        chunks = self.metadata_attacher.attach(chunks, document_id, source["name"], url)
        log(f"           ✓ Metadata attached: doc_id, source_name, url, section, chunk_index")

        return chunks

    def _apply_api_key(self, url: str, source: dict) -> str:
        key_env = source.get("api_key_env")
        if not key_env:
            return url
        api_key = os.getenv(key_env, "")
        if not api_key:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}api_key={api_key}"

    def _rate_limit(self, source: dict):
        rate = source.get("rate_limit")
        if rate:
            time.sleep(1 / rate)

    def _extract_text(self, format_type: str, response, source: dict) -> str:
        if format_type == "json":
            return JSONHandler().extract(
                response.text, source.get("text_field")
            )
        elif format_type == "xml":
            return XMLHandler().extract(
                response.text, source.get("text_field")
            )
        elif format_type == "html":
            return HTMLHandler().extract(response.text)
        elif format_type == "pdf":
            return PDFHandler().extract(response.content)
        else:
            # TODO: replace with local LLM  once MODEL is configured
            return response.text


def _find_coref_rewrites(before: str, after: str) -> list:
    """Return list of (before_sentence, after_sentence) pairs that differ."""
    import re
    before_sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', before) if s.strip()]
    after_sents  = [s.strip() for s in re.split(r'(?<=[.!?])\s+', after)  if s.strip()]
    rewrites = []
    for b, a in zip(before_sents, after_sents):
        if b != a:
            rewrites.append((b, a))
    return rewrites