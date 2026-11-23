"""PLN module configuration — all settings read from environment / .env."""
import os
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class PLNConfig:
    enabled:             bool  = field(default_factory=lambda: os.getenv("ENABLE_PLN", "false").lower() == "true")
    forward_steps:       int   = field(default_factory=lambda: int(os.getenv("PLN_FORWARD_STEPS", "5")))
    backward_timeout:    int   = field(default_factory=lambda: int(os.getenv("PLN_BACKWARD_TIMEOUT", "30")))
    identity_threshold:  float = field(default_factory=lambda: float(os.getenv("PLN_IDENTITY_THRESHOLD", "0.8")))
    min_confidence:      float = field(default_factory=lambda: float(os.getenv("PLN_MIN_CONFIDENCE", "0.5")))
    mine_min_support:    int   = field(default_factory=lambda: int(os.getenv("PLN_MINE_MIN_SUPPORT", "2")))  # cases a learned rule needs
    mine_min_coverage:   float = field(default_factory=lambda: float(os.getenv("PLN_MINE_MIN_COVERAGE", "0.01")))  # share of R's facts it explains
    mine_min_lift:       float = field(default_factory=lambda: float(os.getenv("PLN_MINE_MIN_LIFT", "2.0")))  # times above R's base rate
    mine_min_strength:   float = field(default_factory=lambda: float(os.getenv("PLN_MINE_MIN_STRENGTH", "0.1")))  # share of its cases it holds in
    lib_path:            Path  = field(default_factory=lambda: Path(__file__).resolve().parent / "lib" / "lib_pln.metta")

    @classmethod
    def from_env(cls) -> "PLNConfig":
        return cls()

    def as_dict(self) -> dict:
        return {
            "enabled":            self.enabled,
            "forward_steps":      self.forward_steps,
            "backward_timeout":   self.backward_timeout,
            "identity_threshold": self.identity_threshold,
            "min_confidence":     self.min_confidence,
        }
