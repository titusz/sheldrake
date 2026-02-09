"""Custom Textual widgets for the Sheldrake TUI."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Markdown, Static, TextArea


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


class BacktrackEntry(Static):
    """Single backtrack event entry for the side panel."""

    DEFAULT_CSS = """
    BacktrackEntry {
        color: $warning;
        padding: 0 1;
        margin: 0 0 1 0;
        border-bottom: solid $surface-lighten-2;
    }
    """

    def __init__(self, number: int, reason: str, mode: str | None = None) -> None:
        parts = [f"#{number} {reason}"]
        if mode:
            parts.append(f"→ {mode}")
        super().__init__("\n".join(parts))


class BacktrackPanel(VerticalScroll):
    """Collapsible right-docked panel showing backtrack events."""

    DEFAULT_CSS = """
    BacktrackPanel {
        dock: right;
        width: 40;
        border-left: solid $surface-lighten-2;
        background: $surface-darken-1;
        padding: 1;
    }
    BacktrackPanel.-hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._count = 0

    def compose(self):
        yield Static("[b]Backtracks[/b]", markup=True)

    def add_entry(self, reason: str, mode: str | None = None) -> None:
        """Add a backtrack event to the panel."""
        self._count += 1
        self.mount(BacktrackEntry(self._count, reason, mode))
        self.scroll_end(animate=False)

    def clear(self) -> None:
        """Remove all backtrack entries and reset counter."""
        self._count = 0
        for entry in self.query(BacktrackEntry):
            entry.remove()


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


class ChatInput(TextArea):
    """Multiline chat input with Enter-to-submit and auto-grow."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("shift+enter", "newline", "Newline"),
    ]

    DEFAULT_CSS = """
    ChatInput {
        dock: bottom;
        height: auto;
        max-height: 6;
        border: tall $surface-lighten-2;
    }
    ChatInput:focus {
        border: tall $accent;
    }
    """

    class Submitted(Message):
        """Posted when the user presses Enter to submit input."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("tab_behavior", "focus")
        super().__init__(**kwargs)

    def action_submit(self) -> None:
        """Submit input text if non-empty."""
        text = self.text.strip()
        if text:
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        """Insert a newline character."""
        self.insert("\n")
