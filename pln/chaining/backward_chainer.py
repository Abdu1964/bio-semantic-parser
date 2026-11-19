"""Backward chainer: run PeTTaChainer query() for proof-goal search."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pln.config import PLNConfig
    from pettachainer.pettachainer import PeTTaChainer


class BackwardChainer:
    """Wraps PeTTaChainer query() for goal-directed proof search."""

    def __init__(self, cfg: "PLNConfig", handler: "PeTTaChainer"):
        self._cfg = cfg
        self._handler = handler  # shared with ForwardChainer

    def query(self, pln_query: str, timeout: int | None = None) -> list[str]:
        """Backward-chain a query '(: $prf (Predicate subj $target) $tv)'; PeTTaChainer applies the rules."""
        t = timeout if timeout is not None else self._cfg.backward_timeout
        try:
            results = self._handler.query(pln_query, timeout_sec=t)
            return [str(r) for r in (results or [])]
        except Exception:
            return []

    def query_target(self, predicate: str, subject: str,
                     timeout: int | None = None) -> list[str]:
        """Proofs for every $target of (predicate subject $target)."""
        q = f"(: $prf ({predicate} {subject} $target) $tv)"
        return self.query(q, timeout)

    def query_source(self, predicate: str, target: str,
                     timeout: int | None = None) -> list[str]:
        """Proofs for every $source of (predicate $source target)."""
        q = f"(: $prf ({predicate} $source {target}) $tv)"
        return self.query(q, timeout)
