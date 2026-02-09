"""Custom Textual widgets for the Palimpsest TUI."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Markdown, Static


class UserMessage(Static):
    """Displays a user message with 'You' header."""

    DEFAULT_CSS = """
    UserMessage {
        margin: 1 2;
        color: $accent;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(f"**You:** {text}")


class AssistantMessage(Markdown):
    """Displays an assistant message with streaming support.

    Uses Markdown.get_stream() for real-time token display.
    On backtrack, content is replaced via update() and streaming
    continues from the checkpoint.
    """

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 2;
    }
    """


class BacktrackIndicator(Static):
    """Brief visual indicator when backtrack occurs."""

    DEFAULT_CSS = """
    BacktrackIndicator {
        color: $warning;
        margin: 0 4;
    }
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"⟲ rethinking... ({reason})")


class StatusBar(Static):
    """Bottom bar showing backtrack count, mode, and model."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
    }
    """

    backtracks: reactive[int] = reactive(0)
    mode: reactive[str] = reactive("balanced")
    model: reactive[str] = reactive("")

    def render(self) -> str:
        parts = [f"model: {self.model}"]
        parts.append(f"mode: {self.mode}")
        if self.backtracks > 0:
            parts.append(f"backtracks: {self.backtracks}")
        return " │ ".join(parts)
