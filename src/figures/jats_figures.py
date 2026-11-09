"""Figures from JATS XML (<fig>), with image files from the same package when present."""
import posixpath
import xml.etree.ElementTree as ET

from src.fetcher.archive import FIGURE_EXT
from src.figures.figure import Figure


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _href(fig) -> str:
    for g in fig.iter():
        if g.tag.endswith("graphic"):
            for k, v in g.attrib.items():
                if k.endswith("href"):
                    return v
    return ""


def _stem(name: str) -> str:
    """File name without an image extension ("pone.0033962.g001.jpg" and "pone.0033962.g001" match)."""
    base = posixpath.basename(name.replace("\\", "/")).lower()
    return base[: base.rfind(".")] if base.endswith(FIGURE_EXT) else base


def extract_jats_figures(xml, files=()) -> list:
    """One Figure per <fig>; the image is attached when a package file matches its graphic."""
    root = ET.fromstring(xml)
    images = {_stem(n): (n, c) for n, c in files if n.lower().endswith(FIGURE_EXT)}
    figures = []
    for n, fig in enumerate(root.iter("fig"), 1):
        href = _href(fig)
        name, data = images.get(_stem(href), ("", b"")) if href else ("", b"")
        figures.append(Figure(
            fig_id=fig.get("id") or f"fig{n}", label=_text(fig.find("label")),
            caption=_text(fig.find("caption")), source="jats", image=data,
            ext=name.rsplit(".", 1)[-1].lower() if name else "png", image_file=href))
    return figures
