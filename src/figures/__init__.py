"""Figures and scanned pages: OCR, figure images and captions (Phase 2 of image support)."""
from src.figures.extract import extract_figures, figures_dir, save_figures
from src.figures.figure import Figure

__all__ = ["Figure", "extract_figures", "figures_dir", "save_figures"]
