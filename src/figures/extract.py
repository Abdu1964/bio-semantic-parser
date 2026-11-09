"""Figures from any supported paper file, and saving them next to the paper's data."""
import json
from pathlib import Path

from src.fetcher.archive import pick_main, unpack
from src.fetcher.format_detector import ARCHIVES, FormatDetector
from src.figures.figure import Figure
from src.figures.jats_figures import extract_jats_figures
from src.figures.pdf_figures import extract_pdf_figures

FIGURES_ROOT = Path("data/figures")


def extract_figures(data: bytes, filename: str = "", content_type: str = "") -> list:
    """Figures from a PDF, JATS XML, or an archive holding either (other formats have none)."""
    fmt = FormatDetector().detect(content_type, data=data, filename=filename)
    files = []
    if fmt in ARCHIVES:
        members = unpack(data, fmt, filename)
        (filename, data), _ = pick_main(members)
        files = members
        fmt = FormatDetector().detect(data=data, filename=filename)
    if fmt == "pdf":
        return extract_pdf_figures(data)
    if fmt in ("jats", "xml") and b"<fig" in data:
        return extract_jats_figures(data, files)
    if fmt == "image":                                         # the upload itself may be a diagram
        name = Path(filename or "image.png")
        return [Figure(fig_id="image1", source="image", image=data,
                       ext=name.suffix.lstrip(".").lower() or "png", image_file=name.name)]
    return []


def save_figures(figures: list, out_dir) -> Path:
    """Write each image and a figures.json index; returns the index path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*"):                                  # a rerun replaces earlier output
        if old.is_file():
            old.unlink()
    index = []
    for f in figures:
        entry = f.to_dict()
        if f.image:
            name = f"{f.fig_id}.{f.ext}".replace("/", "_")
            (out / name).write_bytes(f.image)
            entry["file"] = name
        index.append(entry)
    path = out / "figures.json"
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def figures_dir(document_id: str) -> Path:
    return FIGURES_ROOT / str(document_id).replace("/", "_").replace(":", "_")
