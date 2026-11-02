"""Checks that hold for any paper; a failing one means a pipeline step lost or broke content."""
from src.quality.repeated_text import repeated_line, repeated_share
from src.quality.text_coverage import coverage_line, text_coverage

__all__ = ["coverage_line", "repeated_line", "repeated_share", "text_coverage"]
