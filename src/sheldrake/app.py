"""Textual TUI app — wires together inference, stream processing, and display."""

from __future__ import annotations

import io
import os
import re
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Markdown

from sheldrake import _win32_keys
from sheldrake.config import Settings
from sheldrake.protocol import Backtrack
from sheldrake.widgets import (
    AssistantMessage,
    BacktrackIndicator,
    BacktrackPanel,
    ChatInput,
    StatusBar,
    UserMessage,
)

_win32_keys.apply()


class SheldrakeApp(App):
    """Sheldrake — Where AI learns to use the backspace key."""

    TITLE = "Sheldrake"
    SUB_TITLE = "cognitive backtracking"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel_inference", "Cancel"),
        Binding("ctrl+o", "toggle_panel", "Backtracks"),
    ]

    CSS = """
    #main {
        height: 1fr;
    }
    #chat-view {
        overflow-y: auto;
        height: 1fr;
    }
    #status {
        dock: bottom;
        height: 1;
    }
    Footer {
        background: transparent;
    }
    Footer .footer-key--key {
        background: transparent;
        color: $text;
        padding: 0 1;
    }
    Footer .footer-key--description {
        color: $text-muted;
    }
    """

    def __init__(self, model: str | None = None, debug: bool = False) -> None:
        super().__init__()
        self.settings = Settings(model=model) if model else Settings()
        self._show_debug = debug
        self._debug_file: io.TextIOWrapper | None = None
        self._processor = None
        self._current_stream: Any = None
        self._backtrack_indicators: list[BacktrackIndicator] = []
        self._inferring = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        panel = BacktrackPanel(id="backtrack-panel")
        panel.add_class("-hidden")
        yield panel
        with Vertical(id="main"):
            yield VerticalScroll(id="chat-view")
            yield StatusBar(id="status")
            yield ChatInput(id="input")
        yield Footer()

    _RICH_TAG_RE = re.compile(r"\[/?[a-z][a-z0-9_ ]*\]", re.IGNORECASE)

    def _log_debug(self, msg: str) -> None:
        """Write to the debug trace file."""
        if not self._show_debug:
            return
        if self._debug_file:
            plain = self._RICH_TAG_RE.sub("", msg)
            self._debug_file.write(plain + "\n")
            self._debug_file.flush()

    def on_mount(self) -> None:
        self.query_one("#input", ChatInput).focus()

        status = self.query_one("#status", StatusBar)
        status.model = self.settings.model
        status.mode = self.settings.default_mode

        if self._show_debug:
            self._debug_file = open("sheldrake_debug.log", "w", encoding="utf-8")  # noqa: SIM115
            self._log_debug("[bold]Sheldrake debug trace[/bold]")

        if not os.environ.get("ANTHROPIC_API_KEY"):
            chat = self.query_one("#chat-view", VerticalScroll)
            error_md = AssistantMessage()
            chat.mount(error_md)
            error_md.update(
                "**Error:** `ANTHROPIC_API_KEY` environment variable is not set.\n\n"
                "Set it and restart:\n```\nexport ANTHROPIC_API_KEY=sk-ant-...\n```"
            )
            return

        from anthropic import AsyncAnthropic

        from sheldrake.inference import InferenceManager
        from sheldrake.stream import StreamProcessor

        client = AsyncAnthropic()
        inference = InferenceManager(client, self.settings)
        self._processor = StreamProcessor(
            inference, self.settings, on_debug=self._log_debug if self._show_debug else None
        )

    def on_unmount(self) -> None:
        if self._debug_file:
            self._debug_file.close()

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """Handle user message submission."""
        text = event.value

        self.query_one("#backtrack-panel", BacktrackPanel).clear()

        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(UserMessage(text))

        if self._processor is None:
            error = AssistantMessage()
            chat.mount(error)
            error.update("**Error:** No API key configured. Set `ANTHROPIC_API_KEY` and restart.")
            return

        response = AssistantMessage()
        chat.mount(response)
        chat.scroll_end(animate=False)

        self._run_inference(text, response)

    @work(exclusive=True)
    async def _run_inference(self, user_text: str, response_widget: AssistantMessage) -> None:
        """Background worker for streaming inference."""
        chat = self.query_one("#chat-view", VerticalScroll)
        status = self.query_one("#status", StatusBar)
        stream = Markdown.get_stream(response_widget)
        self._current_stream = stream
        chat.anchor()

        async def on_text(t: str) -> None:
            await stream.write(t)

        def on_backtrack(bt: Backtrack, text: str) -> None:
            response_widget.update(text)
            indicator = BacktrackIndicator(bt.reason)
            self._backtrack_indicators.append(indicator)
            chat.mount(indicator)
            status.backtracks += 1
            if bt.mode:
                status.mode = bt.mode
            panel = self.query_one("#backtrack-panel", BacktrackPanel)
            panel.add_entry(bt.reason, bt.mode)
            panel.remove_class("-hidden")

        def on_error(msg: str) -> None:
            response_widget.update(f"**Error:** {msg}")

        def on_done(text: str) -> None:
            for indicator in self._backtrack_indicators:
                indicator.remove()
            self._backtrack_indicators.clear()

        self._inferring = True
        self.refresh_bindings()
        try:
            await self._processor.run(
                user_message=user_text,
                on_text=on_text,
                on_backtrack=on_backtrack,
                on_error=on_error,
                on_done=on_done,
            )
        finally:
            self._inferring = False
            self.refresh_bindings()
            await stream.stop()
            self._current_stream = None
            chat.scroll_end(animate=False)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Show Esc binding only during inference."""
        if action == "cancel_inference":
            return self._inferring
        return True

    def action_toggle_panel(self) -> None:
        """Toggle the backtrack side panel."""
        self.query_one("#backtrack-panel", BacktrackPanel).toggle_class("-hidden")

    def action_cancel_inference(self) -> None:
        """Cancel the running inference worker."""
        self.workers.cancel_group(self, "default")
