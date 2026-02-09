"""Textual TUI app — wires together inference, stream processing, and display."""

from __future__ import annotations

import contextlib
import io
import os
import re
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Header, Input, Markdown, RichLog

from pali.config import Settings
from pali.protocol import Backtrack
from pali.widgets import AssistantMessage, BacktrackIndicator, StatusBar, UserMessage


class PalimpsestApp(App):
    """Palimpsest — Where AI learns to use the backspace key."""

    TITLE = "Palimpsest"
    SUB_TITLE = "cognitive backtracking"

    CSS = """
    #chat-view {
        overflow-y: auto;
    }
    #chat-view.with-debug {
        height: 1fr;
    }
    #debug-log {
        height: 12;
        border-top: solid $surface-lighten-2;
        background: $surface-darken-1;
    }
    #input {
        dock: bottom;
    }
    #status {
        dock: bottom;
        height: 1;
    }
    """

    def __init__(self, model: str | None = None, debug: bool = False) -> None:
        super().__init__()
        self.settings = Settings(model=model) if model else Settings()
        self._show_debug = debug
        self._debug_file: io.TextIOWrapper | None = None
        self._processor = None
        self._current_stream: Any = None
        self._current_indicator: BacktrackIndicator | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(id="chat-view")
        if self._show_debug:
            yield RichLog(id="debug-log", markup=True, max_lines=200)
        yield StatusBar(id="status")
        yield Input(placeholder="Type your message...", id="input")

    _RICH_TAG_RE = re.compile(r"\[/?[a-z][a-z0-9_ ]*\]", re.IGNORECASE)

    def _log_debug(self, msg: str) -> None:
        """Write to the debug panel and trace file."""
        if not self._show_debug:
            return
        with contextlib.suppress(Exception):
            self.query_one("#debug-log", RichLog).write(msg)
        if self._debug_file:
            plain = self._RICH_TAG_RE.sub("", msg)
            self._debug_file.write(plain + "\n")
            self._debug_file.flush()

    def on_mount(self) -> None:
        self.query_one("#input", Input).focus()

        status = self.query_one("#status", StatusBar)
        status.model = self.settings.model
        status.mode = self.settings.default_mode

        if self._show_debug:
            self._debug_file = open("pali_debug.log", "w", encoding="utf-8")  # noqa: SIM115
            self.query_one("#chat-view").add_class("with-debug")
            self._log_debug("[bold]Palimpsest debug trace[/bold]")

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

        from pali.inference import InferenceManager
        from pali.stream import StreamProcessor

        client = AsyncAnthropic()
        inference = InferenceManager(client, self.settings)
        self._processor = StreamProcessor(
            inference, self.settings, on_debug=self._log_debug if self._show_debug else None
        )

    def on_unmount(self) -> None:
        if self._debug_file:
            self._debug_file.close()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user message submission."""
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

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

        async def on_text(t: str) -> None:
            await stream.write(t)

        def on_backtrack(bt: Backtrack, text: str) -> None:
            response_widget.update(text)
            if self._current_indicator is not None:
                self._current_indicator.remove()
            indicator = BacktrackIndicator(bt.reason)
            self._current_indicator = indicator
            chat.mount(indicator)
            status.backtracks += 1
            if bt.mode:
                status.mode = bt.mode

        def on_error(msg: str) -> None:
            response_widget.update(f"**Error:** {msg}")

        def on_done(text: str) -> None:
            if self._current_indicator is not None:
                self._current_indicator.remove()
                self._current_indicator = None

        try:
            await self._processor.run(
                user_message=user_text,
                on_text=on_text,
                on_backtrack=on_backtrack,
                on_error=on_error,
                on_done=on_done,
            )
        finally:
            await stream.stop()
            self._current_stream = None
            chat.scroll_end(animate=False)
