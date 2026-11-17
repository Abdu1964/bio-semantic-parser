"""Local, permanent copy of external API answers — checked before any online lookup."""
from src.api_cache.store import cached_get, lookup, remember, reset_stats, stats

__all__ = ["cached_get", "lookup", "remember", "reset_stats", "stats"]
