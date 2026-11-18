"""Staging DB path of the current run, per thread, so parallel runs never share it."""
import contextvars
import os

_current = contextvars.ContextVar("staging_db", default="")


def use(path) -> None:
    _current.set(str(path) if path else "")


def path(default: str = "") -> str:
    """This run's staging DB; else TRIPLE_STORE_PATH from the environment; else default."""
    return _current.get() or os.getenv("TRIPLE_STORE_PATH", "") or default
