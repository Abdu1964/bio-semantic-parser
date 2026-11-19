"""Plug-in modules for the pipeline (accuracy, pln, llm_confidence); each has one run() and can
be turned off with ENABLE_<NAME>_MODULE=false."""
from __future__ import annotations
import os


def run_module(name: str, **kwargs):
    """Run a module by name; its result, or None when it is off, missing or fails."""
    env_key = f"ENABLE_{name.upper()}_MODULE"
    if os.getenv(env_key, "true").lower() == "false":
        return None
    try:
        if name == "accuracy":
            from optional_modules.accuracy import run
        elif name == "pln":
            from pln.integration.pipeline import run
        elif name == "llm_confidence":
            from optional_modules.llm_confidence import run
        else:
            return None
        return run(**kwargs)
    except Exception:
        return None
