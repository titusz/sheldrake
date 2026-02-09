"""Signal types and streaming parser for the backtrack protocol.

Parses a token stream to separate plain text from protocol signals
(checkpoints and backtracks). Uses a state machine with strict tag
validation to avoid false positives on '<<' in code.
"""

from __future__ import annotations

import contextlib
import enum

from pydantic import BaseModel

MAX_SIGNAL_LENGTH = 500

# Valid tag prefixes — the parser only enters signal mode for these
_TAG_PREFIXES = ("checkpoint:", "backtrack:")
_MAX_PREFIX_LEN = max(len(p) for p in _TAG_PREFIXES)


class TextChunk(BaseModel):
    """Plain text fragment from the token stream."""

    text: str


class Checkpoint(BaseModel):
    """Invisible checkpoint placed by the model."""

    id: str
    position: int = 0
    accumulated_text: str = ""
    accumulated_raw: str = ""


class Backtrack(BaseModel):
    """Backtrack signal requesting rewind to a checkpoint."""

    checkpoint_id: str
    reason: str
    rephrase: str | None = None
    mode: str | None = None
    temperature: float | None = None


class _State(enum.Enum):
    TEXT = "TEXT"
    MAYBE_OPEN = "MAYBE_OPEN"
    TAG_CHECK = "TAG_CHECK"
    IN_SIGNAL = "IN_SIGNAL"


Token = TextChunk | Checkpoint | Backtrack


def _parse_backtrack_extras(
    parts: list[str],
) -> tuple[str | None, str | None, float | None]:
    """Extract optional fields (rephrase, mode, temp) from backtrack parts."""
    rephrase = None
    mode = None
    temperature = None
    for part in parts:
        if part.startswith("rephrase:"):
            rephrase = part[len("rephrase:") :]
        elif part.startswith("mode:"):
            mode = part[len("mode:") :]
        elif part.startswith("temp:"):
            with contextlib.suppress(ValueError):
                temperature = float(part[len("temp:") :])
    return rephrase, mode, temperature


def _parse_signal_body(body: str) -> Checkpoint | Backtrack | None:
    """Parse the content between << and >> into a signal object."""
    if body.startswith("checkpoint:"):
        cp_id = body[len("checkpoint:") :]
        return Checkpoint(id=cp_id) if cp_id else None

    if body.startswith("backtrack:"):
        rest = body[len("backtrack:") :]
        if not rest:
            return None
        parts = rest.split("|")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return None
        rephrase, mode, temperature = _parse_backtrack_extras(parts[2:])
        return Backtrack(
            checkpoint_id=parts[0],
            reason=parts[1],
            rephrase=rephrase,
            mode=mode,
            temperature=temperature,
        )

    return None


class SignalParser:
    """Streaming parser that separates text from protocol signals.

    States:
        TEXT        — emit chars as TextChunk. On '<', go to MAYBE_OPEN.
        MAYBE_OPEN  — if next is '<', go to TAG_CHECK. Else emit '<' + char, back to TEXT.
        TAG_CHECK   — accumulate chars after '<<'. Check if they match prefix of
                      'checkpoint:' or 'backtrack:'. If yes and prefix complete, go to
                      IN_SIGNAL. If chars diverge from both prefixes, flush buffer as
                      text, back to TEXT.
        IN_SIGNAL   — accumulate until '>>'. Parse signal body. On parse failure or
                      max length exceeded, flush entire buffer as text, back to TEXT.
    """

    def __init__(self) -> None:
        self._state = _State.TEXT
        self._buffer = ""  # accumulates tag check / signal body chars
        self._text_buffer = ""  # accumulates plain text for batching

    def feed(self, chunk: str) -> list[Token]:
        """Feed a chunk of streamed text, returning parsed tokens."""
        result: list[Token] = []
        handlers = {
            _State.TEXT: self._feed_text,
            _State.MAYBE_OPEN: self._feed_maybe_open,
            _State.TAG_CHECK: self._feed_tag_check,
            _State.IN_SIGNAL: self._feed_in_signal,
        }

        for char in chunk:
            handlers[self._state](char, result)

        if self._text_buffer and self._state == _State.TEXT:
            result.append(TextChunk(text=self._text_buffer))
            self._text_buffer = ""

        return result

    def _feed_text(self, char: str, result: list[Token]) -> None:
        """Handle a character in TEXT state."""
        if char == "<":
            self._state = _State.MAYBE_OPEN
        else:
            self._text_buffer += char

    def _feed_maybe_open(self, char: str, result: list[Token]) -> None:
        """Handle a character in MAYBE_OPEN state."""
        if char == "<":
            self._state = _State.TAG_CHECK
            self._buffer = ""
        else:
            self._text_buffer += "<" + char
            self._state = _State.TEXT

    def _feed_tag_check(self, char: str, result: list[Token]) -> None:
        """Handle a character in TAG_CHECK state."""
        self._buffer += char
        if self._could_be_tag_prefix(self._buffer):
            if self._is_complete_tag_prefix(self._buffer):
                self._state = _State.IN_SIGNAL
        else:
            self._text_buffer += "<<" + self._buffer
            self._buffer = ""
            self._state = _State.TEXT

    def _feed_in_signal(self, char: str, result: list[Token]) -> None:
        """Handle a character in IN_SIGNAL state."""
        self._buffer += char
        if len(self._buffer) > MAX_SIGNAL_LENGTH:
            self._text_buffer += "<<" + self._buffer
            self._buffer = ""
            self._state = _State.TEXT
        elif self._buffer.endswith(">>"):
            self._complete_signal(result)

    def _complete_signal(self, result: list[Token]) -> None:
        """Parse a complete signal body and emit tokens."""
        body = self._buffer[:-2]
        signal = _parse_signal_body(body)
        if signal is not None:
            if self._text_buffer:
                result.append(TextChunk(text=self._text_buffer))
                self._text_buffer = ""
            result.append(signal)
        else:
            self._text_buffer += "<<" + self._buffer
        self._buffer = ""
        self._state = _State.TEXT

    def flush(self) -> list[TextChunk]:
        """Emit any buffered incomplete content as text."""
        result: list[TextChunk] = []

        pending = self._text_buffer
        match self._state:
            case _State.MAYBE_OPEN:
                pending += "<"
            case _State.TAG_CHECK:
                pending += "<<" + self._buffer
            case _State.IN_SIGNAL:
                pending += "<<" + self._buffer

        self._text_buffer = ""
        self._buffer = ""
        self._state = _State.TEXT

        if pending:
            result.append(TextChunk(text=pending))
        return result

    @staticmethod
    def _could_be_tag_prefix(s: str) -> bool:
        """Check if s could be the start of a valid tag name."""
        return any(prefix.startswith(s) for prefix in _TAG_PREFIXES)

    @staticmethod
    def _is_complete_tag_prefix(s: str) -> bool:
        """Check if s matches a complete tag prefix (e.g., 'checkpoint:' or 'backtrack:')."""
        return any(s == prefix for prefix in _TAG_PREFIXES)
