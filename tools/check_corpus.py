"""Run Layer 3 (read, clean, split, chunk) on many papers and report the quality checks.

Usage: python -m tools.check_corpus <file or folder> [...]
Exit code 1 when any paper fails a check, so it can gate a change.
"""
import sys
from pathlib import Path

from src.fetcher.chunker import Chunker
from src.fetcher.cleaner import TextCleaner
from src.fetcher.handlers import PDFHandler
from src.fetcher.section_splitter import SectionSplitter
from src.quality.repeated_text import MAX_REPEATED, repeated_share
from src.quality.text_coverage import MIN_COVERAGE, text_coverage
from src.quality.unreadable import MAX_UNREADABLE, unreadable_count

_TEXT = {".txt", ".md", ".xml", ".nxml", ".html", ".htm", ".json"}


def _read(path: Path) -> str:
    try:                                                  # every format, where the reader exists
        from src.fetcher.document_reader import read_document
        return read_document(path.read_bytes(), path.name).text
    except ImportError:
        if path.suffix.lower() == ".pdf":
            return PDFHandler().extract(str(path))
        if path.suffix.lower() in (".xml", ".nxml"):
            from src.fetcher.handlers import XMLHandler
            return XMLHandler().extract(path.read_text(encoding="utf-8", errors="replace"))
        return path.read_text(encoding="utf-8", errors="replace")


def check(path: Path) -> dict:
    text = TextCleaner().clean(_read(path))
    sections = SectionSplitter().split(text)
    chunks = Chunker().chunk_document(sections)
    return {"paper": path.name, "chars": len(text), "coverage": text_coverage(text, chunks),
            "repeated": repeated_share(text), "unreadable": unreadable_count(text),
            "sections": [s["section"] for s in sections]}


def _problem(r: dict) -> str:
    if not r["chars"]:
        return "EMPTY"
    if r["coverage"] < MIN_COVERAGE:
        return "LOST"
    if r["repeated"] > MAX_REPEATED:
        return "REPEAT"
    if r["unreadable"] > MAX_UNREADABLE:
        return "SYMBOLS"
    return ""


def main(args: list) -> int:
    files = [f for a in args for f in (sorted(Path(a).rglob("*")) if Path(a).is_dir() else [Path(a)])
             if f.is_file() and (f.suffix.lower() == ".pdf" or f.suffix.lower() in _TEXT)]
    failed = 0
    for f in files:
        try:
            r = check(f)
        except Exception as e:
            print(f"ERROR   {f.name[:50]:50} {e}")
            failed += 1
            continue
        problem = _problem(r)
        failed += bool(problem)
        print(f"{problem or 'ok':7} {r['paper'][:50]:50} {r['chars']:8,} chars  coverage {r['coverage']:6.1%}  "
              f"repeated {r['repeated']:5.1%}  unreadable {r['unreadable']:3}  {r['sections']}")
    print(f"\n{len(files) - failed}/{len(files)} papers passed (coverage >= {MIN_COVERAGE:.0%}, "
          f"repeated <= {MAX_REPEATED:.0%}, unreadable <= {MAX_UNREADABLE}, not empty)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["data/uploads"]))
