"""Settings, cognitive modes, and defaults."""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-4-6"

MODES: dict[str, dict[str, float]] = {
    "precise": {"temperature": 0.2},
    "exploratory": {"temperature": 0.9},
    "adversarial": {"temperature": 0.7},
    "balanced": {"temperature": 0.6},
}


class Settings(BaseModel):
    """Runtime configuration for Palimpsest."""

    model: str = DEFAULT_MODEL
    max_backtracks: int = 8
    min_tokens_between_signals: int = 20
    default_mode: str = "balanced"
    max_hint_length: int = 200
    modes: dict[str, dict[str, float]] = Field(default_factory=lambda: dict(MODES))
