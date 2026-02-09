"""Core orchestrator: intercepts protocol signals and manages backtrack flow."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pali.protocol import Backtrack, Checkpoint, SignalParser, TextChunk
from pali.system_prompt import build_system_prompt

if TYPE_CHECKING:
    from pali.config import Settings


@runtime_checkable
class InferenceLike(Protocol):
    """Protocol for inference backends (production and test doubles)."""

    def stream(
        self, messages: list[dict], system: str, mode: str = "balanced"
    ) -> AsyncIterator[str]: ...

    async def cancel(self) -> None: ...


class _BacktrackSignal(Exception):
    """Internal signal to break out of stream loop for retry."""


async def _maybe_await(result: object) -> None:
    """Await the result if it's a coroutine, otherwise no-op."""
    if inspect.isawaitable(result):
        await result


@dataclass
class _RunCtx:
    """Mutable state for a single run() invocation."""

    accumulated: str = ""
    accumulated_raw: str = ""
    checkpoints: dict[str, Checkpoint] = field(default_factory=dict)
    chars_since: int = 0
    hints: list[str] = field(default_factory=list)
    bt_count: int = 0
    mode: str = "balanced"


class StreamProcessor:
    """Orchestrates inference with backtrack interception."""

    def __init__(
        self,
        inference: InferenceLike,
        settings: Settings,
        on_debug: Callable[[str], None] | None = None,
    ) -> None:
        self.inference = inference
        self.settings = settings
        self.messages: list[dict] = []
        self._debug = on_debug

    def _dbg(self, msg: str) -> None:
        """Emit a debug message if debug callback is set."""
        if self._debug:
            self._debug(msg)

    async def run(
        self,
        user_message: str,
        on_text: Callable,
        on_backtrack: Callable,
        on_error: Callable,
        on_done: Callable,
    ) -> None:
        """Process a user message with backtracking support.

        Callbacks may be sync or async — async results are awaited.
        """
        self.messages.append({"role": "user", "content": user_message})
        ctx = _RunCtx(
            mode=self.settings.default_mode,
            chars_since=self.settings.min_tokens_between_signals,
        )
        self._dbg(f"[dim]start:[/dim] mode={ctx.mode}")

        while True:
            api_messages = self._build_messages(ctx.accumulated)
            system = build_system_prompt(ctx.hints, self.settings.max_hint_length)

            if ctx.hints:
                self._dbg(f"[dim]retry:[/dim] hints={ctx.hints}")

            parser = SignalParser()
            try:
                async for delta in self.inference.stream(api_messages, system, ctx.mode):
                    self._dbg(f"[dim cyan]raw:[/dim cyan] {delta!r}")
                    for token in parser.feed(delta):
                        await self._process_token(token, ctx, on_text, on_backtrack)

            except _BacktrackSignal:
                continue
            except Exception as exc:
                self._dbg(f"[bold red]error:[/bold red] {exc}")
                await _maybe_await(on_error(f"Inference error: {exc}"))
                self.messages.pop()
                return
            else:
                break

        for token in parser.flush():
            if isinstance(token, TextChunk):
                ctx.accumulated += token.text
                await _maybe_await(on_text(token.text))

        self.messages.append({"role": "assistant", "content": ctx.accumulated})
        self._dbg(f"[dim]done:[/dim] {len(ctx.accumulated)} chars, {ctx.bt_count} backtracks")
        await _maybe_await(on_done(ctx.accumulated))

    async def _process_token(
        self, token: object, ctx: _RunCtx, on_text: Callable, on_backtrack: Callable
    ) -> None:
        """Dispatch a parsed token to the appropriate handler."""
        match token:
            case TextChunk(text=t):
                ctx.accumulated += t
                ctx.accumulated_raw += t
                ctx.chars_since += len(t)
                await _maybe_await(on_text(t))
            case Checkpoint() as cp:
                self._handle_checkpoint(cp, ctx)
            case Backtrack() as bt:
                await self._handle_backtrack(bt, ctx, on_text, on_backtrack)

    def _handle_checkpoint(self, cp: Checkpoint, ctx: _RunCtx) -> None:
        """Register a checkpoint if enough tokens have elapsed since last signal."""
        if ctx.chars_since < self.settings.min_tokens_between_signals:
            self._dbg(f"[yellow]checkpoint ignored (too soon):[/yellow] {cp.id}")
            return
        cp.position = len(ctx.accumulated)
        cp.accumulated_text = ctx.accumulated
        ctx.accumulated_raw += f"<<checkpoint:{cp.id}>>"
        cp.accumulated_raw = ctx.accumulated_raw
        ctx.checkpoints[cp.id] = cp
        ctx.chars_since = 0
        self._dbg(
            f"[green]checkpoint:[/green] {cp.id} (pos={cp.position}, total={len(ctx.checkpoints)})"
        )

    async def _handle_backtrack(
        self, bt: Backtrack, ctx: _RunCtx, on_text: Callable, on_backtrack: Callable
    ) -> None:
        """Execute a backtrack: validate, rewind state, and raise to restart."""
        if ctx.bt_count >= self.settings.max_backtracks:
            self._dbg("[red]backtrack budget exhausted[/red]")
            await _maybe_await(on_text(" [backtrack budget exhausted] "))
            ctx.accumulated += " [backtrack budget exhausted] "
            ctx.accumulated_raw += " [backtrack budget exhausted] "
            return

        if bt.checkpoint_id not in ctx.checkpoints:
            self._dbg(f"[red]backtrack ignored:[/red] unknown checkpoint {bt.checkpoint_id!r}")
            return

        if bt.mode and bt.mode not in self.settings.modes:
            self._dbg(f"[yellow]unknown mode {bt.mode!r},[/yellow] keeping {ctx.mode}")
            bt.mode = None

        self._dbg(
            f"[bold magenta]BACKTRACK:[/bold magenta] "
            f"→ {bt.checkpoint_id} | {bt.reason}"
            + (f" | mode:{bt.mode}" if bt.mode else "")
            + (f" | rephrase:{bt.rephrase}" if bt.rephrase else "")
        )

        await self.inference.cancel()
        cp = ctx.checkpoints[bt.checkpoint_id]
        ctx.accumulated = cp.accumulated_text
        ctx.accumulated_raw = cp.accumulated_raw
        ctx.checkpoints = {k: v for k, v in ctx.checkpoints.items() if v.position <= cp.position}
        ctx.hints.append(bt.reason)
        ctx.mode = bt.mode or ctx.mode
        ctx.bt_count += 1
        ctx.chars_since = 0
        await _maybe_await(on_backtrack(bt, ctx.accumulated))
        raise _BacktrackSignal

    def _build_messages(self, accumulated_text: str) -> list[dict]:
        """Build API messages with optional multi-turn continuation after backtrack.

        Instead of assistant prefill (not supported by all models), preserves
        good text as a prior assistant turn and adds a user continuation prompt.
        """
        msgs = list(self.messages)
        if accumulated_text.strip():
            msgs.append({"role": "assistant", "content": accumulated_text})
            msgs.append(
                {
                    "role": "user",
                    "content": (
                        "Continue your response directly from where you left off. "
                        "Do not repeat, summarize, or acknowledge this instruction. "
                        "Pick up mid-sentence if needed."
                    ),
                }
            )
        return msgs
