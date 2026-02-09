# Palimpsest — "Where AI learns to use the backspace key."

## Context

During conversation about token generation architecture, we identified a real limitation: LLM
inference is forward-only. Once tokens are emitted, they pollute context and constrain everything
downstream. Bad early framing cascades into worse later output. The only current remedy is patching
forward with caveats, which adds noise.

**Palimpsest** (short name: **pali**) is a standalone demo app that gives an AI the ability to
**rewind its own token stream mid-generation**. The model places invisible checkpoints, and when it
detects a wrong path, it signals to backtrack. The stream processor intercepts the signal, cancels
inference, truncates context, and restarts with a hint about what went wrong.

The name pairs with Ash intentionally — both are about what persists through transformation. A
palimpsest is a manuscript where earlier writing has been scraped away and overwritten, but traces
of the original remain visible underneath.

Extended capabilities beyond simple backtracking:

- **Prompt rephrasing** — the model can reinterpret the question on backtrack
- **Cognitive mode shifting** — the model can request different inference parameters (temperature)

**Stack:** Textual TUI + Anthropic Python SDK + Pydantic, managed with uv.

**Scope:** Text-only chat (no tool use, no non-text content blocks). This is a focused demo of the
cognitive backtracking mechanism.

---

## Package Structure

Independent package inside the ash repo at `C:\Users\titusz\Code\ash\pali\`:

```
pali/
├── pyproject.toml
├── src/
│   └── pali/
│       ├── __init__.py          # Version: "0.1.0"
│       ├── __main__.py          # python -m pali
│       ├── app.py               # Textual App — TUI orchestration
│       ├── protocol.py          # Signal types, parser state machine
│       ├── stream.py            # StreamProcessor — intercept signals, manage flow
│       ├── inference.py         # InferenceManager — Anthropic SDK wrapper
│       ├── config.py            # Modes, defaults, settings
│       ├── widgets.py           # UserMessage, AssistantMessage, StatusBar
│       └── system_prompt.py     # Prompt template with dynamic backtrack hints
└── tests/
    ├── conftest.py
    ├── test_protocol.py
    ├── test_stream.py
    └── test_inference.py
```

---

## Backtrack Protocol

### Signal Syntax

```
<<checkpoint:ID>>                                    — invisible checkpoint
<<backtrack:ID|reason>>                              — simple backtrack
<<backtrack:ID|reason|rephrase:new interpretation>>  — with prompt rephrase
<<backtrack:ID|reason|mode:precise>>                 — with mode shift
<<backtrack:ID|reason|rephrase:text|mode:precise>>   — full backtrack
```

`<<`/`>>` delimiters chosen for reliable streaming parse (clear start/end, familiar to models).
Pipe `|` separates fields within backtrack signals.

**Code collision mitigation:** The `<<` delimiter collides with C++ streams and bitwise shift
operators. The parser uses **strict tag validation** after `<<` — it only enters signal mode if
the next characters match `checkpoint:` or `backtrack:`. Any other content (space, quote, letter
not matching these prefixes) causes immediate flush as plain text. See parser design below.

### Cognitive Modes

```python
MODES = {
    "precise": {"temperature": 0.2, "top_p": 0.9},
    "exploratory": {"temperature": 0.9, "top_p": 0.95},
    "adversarial": {"temperature": 0.7, "top_p": 0.9},
    "balanced": {"temperature": 0.5, "top_p": 0.9},  # default
}
```

### Constraints (enforced at runtime)

- Max 3 backtracks per response (prevents oscillation)
- Min 30 tokens between signals (prevents rapid-fire) — tracked via char counter since last signal
- Escalation order: simple retry → mode shift → rephrase (documented in prompt, not enforced in
    code — the model decides)
- Max signal body length: 500 chars (prevents runaway buffering in parser)

---

## Design Decisions

### Backtrack hints: reset per response

Hints accumulate during a single assistant response (across backtracks within that response) but
**reset when a new user message arrives**. Stale hints from earlier questions would confuse the
model.

### Phantom checkpoints in prefill

When building the prefill for a backtrack retry, checkpoint signal text is **included** in the API
context (even though it's hidden from the user display). This lets the model "see" that it already
passed that checkpoint, reducing the chance it re-emits the same checkpoint tag and stutters.

The `accumulated_text` on Checkpoint stores the user-visible text (for display truncation), while a
separate `accumulated_raw` stores the full text including signal markup (for API prefill).

### Concurrent user input during streaming

Uses Textual's `@work(exclusive=True)` — sending a new message while streaming cancels the active
worker and starts a new one. This is cancel-and-replace behavior.

### Committed vs transient message state

`self.messages` (the conversation history) only contains **committed** turns — complete
user/assistant pairs from previous exchanges. The current in-progress assistant response is
**transient** and only appended to `self.messages` after successful completion. The prefill for
backtrack retries is constructed separately, never touching committed history.

---

## Implementation Plan

### Step 1: `protocol.py` — Signal Types & Streaming Parser

**Data models (Pydantic):**

```python
class Checkpoint:
    id: str  # e.g., "opening", "claim1"
    position: int  # char offset in displayed text
    accumulated_text: str  # user-visible text up to this point
    accumulated_raw: str  # full text including signal markup (for API prefill)


class Backtrack:
    checkpoint_id: str
    reason: str
    rephrase: str | None = None
    mode: str | None = None  # key into MODES dict


class TextChunk:
    text: str
```

**Streaming parser** — state machine with strict tag validation:

```python
MAX_SIGNAL_LENGTH = 500  # prevent runaway buffering

class SignalParser:
    """Streaming parser that separates text from protocol signals.

    States:
        TEXT        — emit chars as TextChunk. On '<', go to MAYBE_OPEN.
        MAYBE_OPEN  — if next is '<', go to TAG_CHECK. Else emit '<' + char, back to TEXT.
        TAG_CHECK   — accumulate chars after '<<'. Check if they match prefix of
                      'checkpoint:' or 'backtrack:'. If yes and prefix complete, go to
                      IN_SIGNAL. If chars diverge from both prefixes, flush buffer as
                      text, back to TEXT. This handles '<<' in code (e.g. `x << 1`).
        IN_SIGNAL   — accumulate until '>>'. Parse signal body. On parse failure or
                      max length exceeded, flush entire buffer as text, back to TEXT.
    """

    def feed(self, chunk: str) -> list[TextChunk | Checkpoint | Backtrack]: ...
    def flush(self) -> list[TextChunk]:  # emit any buffered incomplete signal as text
```

The TAG_CHECK state is key: after seeing `<<`, we accumulate up to 11 chars. If they match
`checkpoint:` or `backtrack:`, we proceed to IN_SIGNAL. If at any point the accumulated chars
can't be a prefix of either tag name, we immediately flush `<<` + accumulated chars as text and
return to TEXT state. This prevents `std::cout << "hello"` from hanging the parser.

### Step 2: `config.py` — Settings & Defaults

```python
DEFAULT_MODEL = "claude-sonnet-4-20250514"


class Settings(BaseModel):
    model: str = DEFAULT_MODEL
    max_backtracks: int = 3
    min_tokens_between_signals: int = 30
    default_mode: str = "balanced"
    max_hint_length: int = 200  # truncate backtrack reasons
    modes: dict[str, dict] = Field(default_factory=lambda: dict(MODES))
```

API key from `ANTHROPIC_API_KEY` env var. Model configurable via CLI flag.

### Step 3: `system_prompt.py` — The Critical Prompt

This is the most important file. The model needs to reliably produce signals through in-context
learning alone (no fine-tuning). Full draft:

```python
SYSTEM_PROMPT_TEMPLATE = """
You are an AI assistant with cognitive backtracking — the ability to revise your
thoughts mid-generation by rewinding to an earlier point and trying again.

## Protocol Signals

### Checkpoints
Place before significant direction choices (2-4 per response):
    <<checkpoint:ID>>
ID is a short descriptive label: opening, approach, claim1, conclusion, etc.

### Backtrack
Rewind to a checkpoint and regenerate from there:
    <<backtrack:ID|reason>>
Extended forms:
    <<backtrack:ID|reason|rephrase:new interpretation of the question>>
    <<backtrack:ID|reason|mode:precise>>
    <<backtrack:ID|reason|rephrase:reinterpretation|mode:exploratory>>
Available modes: precise (focused), exploratory (creative), adversarial (critical), balanced

## Rules
1. Place checkpoints at natural decision points BEFORE committing to a direction
2. Maximum 3 backtracks per response
3. Generate at least 30 tokens after a backtrack before placing another checkpoint
4. Escalate: simple backtrack first → mode shift → rephrase. Lighter fixes first
5. NEVER reference a checkpoint ID that doesn't exist
6. Checkpoint and backtrack tags must be on their own — not inside code blocks or quotes

## When to Backtrack
✅ Wrong framing that will constrain the entire response poorly
✅ Premature commitment to an uncertain answer
✅ Tone/register completely wrong for context
✅ Overcomplicating something that should be simple
✅ Realized a factual claim is likely wrong

❌ Minor word choice improvements — just continue
❌ Adding a forgotten detail — mention it normally
❌ Stylistic polish — not worth the rewind
❌ Anything that's adequate but not perfect

## Example

User: Explain quantum entanglement simply.
Assistant: <<checkpoint:opening>>Quantum entanglement is a phenomenon
predicted by the mathematical formalism of<<backtrack:opening|too technical
for a "simply" request, need everyday analogy|mode:exploratory>>Imagine you
have two magic coins. You flip them in different rooms, and somehow they
always land the same way...<<checkpoint:detail>>

## Important
- Checkpoints are INVISIBLE to the reader — the system strips them
- On backtrack, the reader sees old text erased and new text appearing
- A hint about your backtrack reason is injected, so avoid repeating the mistake
- Use this authentically — it's a real cognitive tool, not a performance
{backtrack_hints}
"""
```

Dynamic `{backtrack_hints}` section, with sanitization:

```python
def sanitize_hint(hint: str, max_length: int = 200) -> str:
    """Bound length, strip control chars, ensure hint is inert context."""
    cleaned = "".join(c for c in hint if c.isprintable() or c == " ")
    return cleaned[:max_length]


def build_system_prompt(hints: list[str], max_length: int = 200) -> str:
    if not hints:
        return SYSTEM_PROMPT_TEMPLATE.replace("{backtrack_hints}", "")
    hint_text = "\n\n## Active Backtrack Context\n"
    for i, hint in enumerate(hints, 1):
        hint_text += f"- Backtrack {i}: {sanitize_hint(hint, max_length)}\n"
    return SYSTEM_PROMPT_TEMPLATE.replace("{backtrack_hints}", hint_text)
```

### Step 4: `inference.py` — Anthropic SDK Wrapper

```python
class InferenceManager:
    """Manages Anthropic API streaming with cancel/restart support."""

    def __init__(self, client: AsyncAnthropic, settings: Settings):
        self.client = client
        self.settings = settings
        self._active_stream: AsyncMessageStream | None = None

    async def stream(
        self,
        messages: list[dict],
        system: str,
        mode: str = "balanced",
    ) -> AsyncIterator[str]:
        """Start streaming inference. Yields text deltas.

        Uses stream.text_stream for clean text-only iteration (ignores
        non-text events like message_start, content_block_start, etc.).
        """
        params = self.settings.modes[mode]
        self._active_stream = self.client.messages.stream(
            model=self.settings.model,
            max_tokens=4096,
            system=system,
            messages=messages,
            temperature=params["temperature"],
            top_p=params["top_p"],
        )
        async with self._active_stream as stream:
            async for text in stream.text_stream:
                yield text

    async def cancel(self) -> None:
        """Cancel active stream. Awaits async close to prevent race."""
        if self._active_stream is not None:
            await self._active_stream.close()
            self._active_stream = None
```

Key details:

- Uses `stream.text_stream` (not manual event type checking) for clean text-only iteration
- `cancel()` uses `await` on `close()` — the close is async and must complete before retry starts
- `system` is a top-level API parameter (not in `messages`), so backtrack hints can be swapped
    between retries without touching conversation history

### Step 5: `stream.py` — The Core Orchestrator

```python
class _BacktrackSignal(Exception):
    """Internal signal to break out of stream loop for retry."""


class StreamProcessor:
    """Orchestrates inference with backtrack interception."""

    def __init__(self, inference: InferenceManager, settings: Settings):
        self.inference = inference
        self.settings = settings
        self.messages: list[dict] = []  # committed conversation history only

    async def run(self, user_message: str, on_text, on_backtrack, on_error, on_done):
        """Process a user message with backtracking support.

        Callbacks:
            on_text(str)                  — display text chunk
            on_backtrack(Backtrack, str)   — visual indicator + truncated text
            on_error(str)                 — API/network error message
            on_done(str)                  — final complete response
        """
        self.messages.append({"role": "user", "content": user_message})
        backtrack_hints: list[str] = []  # reset per response
        backtrack_count = 0
        mode = self.settings.default_mode
        accumulated = ""  # user-visible text
        accumulated_raw = ""  # full text including signal markup (for API prefill)
        checkpoints: dict[str, Checkpoint] = {}
        chars_since_last_signal = 0

        while True:  # retry loop for backtracks
            api_messages = self._build_messages(accumulated_raw)
            system = build_system_prompt(backtrack_hints, self.settings.max_hint_length)

            parser = SignalParser()
            try:
                async for delta in self.inference.stream(api_messages, system, mode):
                    for token in parser.feed(delta):
                        match token:
                            case TextChunk(text=t):
                                accumulated += t
                                accumulated_raw += t
                                chars_since_last_signal += len(t)
                                on_text(t)

                            case Checkpoint() as cp:
                                # Enforce min-tokens-between-signals
                                if (
                                    chars_since_last_signal
                                    < self.settings.min_tokens_between_signals
                                ):
                                    continue  # too soon, ignore
                                cp.position = len(accumulated)
                                cp.accumulated_text = accumulated
                                cp.accumulated_raw = accumulated_raw
                                checkpoints[cp.id] = cp
                                accumulated_raw += f"<<checkpoint:{cp.id}>>"
                                chars_since_last_signal = 0

                            case Backtrack() as bt:
                                # Budget exhausted — surface to UI, don't crash
                                if backtrack_count >= self.settings.max_backtracks:
                                    on_text(f" [backtrack budget exhausted] ")
                                    continue

                                # Unknown checkpoint ID — ignore, don't crash
                                if bt.checkpoint_id not in checkpoints:
                                    continue

                                # Unknown mode — fall back to current mode
                                if bt.mode and bt.mode not in self.settings.modes:
                                    bt.mode = None

                                await self.inference.cancel()
                                cp = checkpoints[bt.checkpoint_id]

                                # Truncate to checkpoint
                                accumulated = cp.accumulated_text
                                accumulated_raw = cp.accumulated_raw

                                # Prune stale checkpoints beyond rewind point
                                checkpoints = {
                                    k: v
                                    for k, v in checkpoints.items()
                                    if v.position <= cp.position
                                }

                                backtrack_hints.append(bt.reason)
                                mode = bt.mode or mode
                                backtrack_count += 1
                                chars_since_last_signal = 0
                                on_backtrack(bt, accumulated)
                                raise _BacktrackSignal()

            except _BacktrackSignal:
                continue  # restart with new context

            except Exception as exc:
                # API errors, network errors, unexpected failures
                on_error(f"Inference error: {exc}")
                # Remove the user message we optimistically appended
                self.messages.pop()
                return

            else:
                break  # normal completion

        # Flush any buffered parser content
        for token in parser.flush():
            if isinstance(token, TextChunk):
                accumulated += token.text
                on_text(token.text)

        self.messages.append({"role": "assistant", "content": accumulated})
        on_done(accumulated)

    def _build_messages(self, prefill_raw: str) -> list[dict]:
        """Build API messages, using raw prefill for backtrack continuation.

        self.messages contains only committed history (complete user/assistant
        pairs). The current assistant response is transient — only the prefill
        is appended temporarily for the API call.
        """
        msgs = list(self.messages)
        if prefill_raw:
            msgs.append({"role": "assistant", "content": prefill_raw})
        return msgs
```

Key improvements over initial design:

- **`backtrack_hints` reset per response** — don't accumulate across turns
- **`accumulated_raw`** tracks full text including signal markup for accurate API prefill
- **Unknown checkpoint IDs** handled gracefully (skip, don't crash)
- **Unknown modes** fall back to current mode instead of KeyError
- **Stale checkpoint pruning** after rewind — checkpoints from discarded branches are removed
- **Min-tokens-between-signals** enforced at runtime (ignore signals that arrive too fast)
- **Budget exhaustion** surfaced to UI as inline text rather than silently swallowed
- **API error handling** via broad except — calls `on_error` callback, cleans up message state
- **`await self.inference.cancel()`** — properly awaits async close before retry

### Step 6: `widgets.py` — Custom Textual Widgets

```python
class UserMessage(Static):
    """Displays a user message with 'You' header."""


class AssistantMessage(Markdown):
    """Displays an assistant message with streaming support.

    Uses Markdown.get_stream() for real-time token display.
    On backtrack, the widget content is replaced with truncated text
    and streaming continues from the checkpoint.
    """


class BacktrackIndicator(Static):
    """Brief visual indicator when backtrack occurs."""

    # Shows: "⟲ rethinking... (reason)" then auto-removes


class StatusBar(Static):
    """Bottom bar showing backtrack count, mode, model."""

    backtracks = reactive(0)
    mode = reactive("balanced")
    model = reactive("")
```

### Step 7: `app.py` — Textual App

```python
class PalimpsestApp(App):
    CSS = """
    #chat-view {
        overflow-y: auto;
    }
    .user-message { margin: 1 2; color: $accent; }
    .assistant-message { margin: 1 2; }
    .backtrack-indicator { color: $warning; margin: 0 4; }
    #input { dock: bottom; }
    #status { dock: bottom; height: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(id="chat-view")
        yield StatusBar(id="status")
        yield Input(placeholder="Type your message...", id="input")

    @on(Input.Submitted)
    async def on_input_submitted(self, event):
        # Display user message
        # Create AssistantMessage widget
        # Launch streaming worker (exclusive=True cancels any active stream)
        ...

    @work(exclusive=True)
    async def run_inference(self, user_text: str, response_widget):
        """Background worker for streaming inference."""
        await self.processor.run(
            user_message=user_text,
            on_text=lambda t: self._append_text(response_widget, t),
            on_backtrack=lambda bt, text: self._handle_backtrack(response_widget, bt, text),
            on_error=lambda msg: self._show_error(msg),
            on_done=lambda _: self._finalize(response_widget),
        )
```

Note: Removed `layout: stream` from CSS — need to verify Textual version support at implementation
time. `VerticalScroll` with default layout is the safe baseline.

Backtrack visual effect:

1. On `on_backtrack`: replace response widget content with truncated text
1. Mount a `BacktrackIndicator` below it: `"⟲ rethinking... (reason)"`
1. Continue streaming into same response widget from checkpoint
1. Remove indicator after new text starts flowing

### Step 8: `__main__.py` — Entry Point

```python
import typer

app = typer.Typer()


@app.command()
def main(model: str = typer.Option("claude-sonnet-4-20250514", help="Model ID")):
    """Pali — Where AI learns to use the backspace key."""
    from pali.app import PalimpsestApp

    tui = PalimpsestApp(model=model)
    tui.run()


if __name__ == "__main__":
    app()
```

---

## Dependencies

```toml
[project]
name = "pali"
version = "0.1.0"
description = "Palimpsest — Where AI learns to use the backspace key."
requires-python = ">=3.12"
dependencies = [
  "textual>=1.0",
  "anthropic>=0.45",
  "pydantic>=2.0",
  "typer>=0.15",
]

[project.scripts]
pali = "pali.__main__:app"
```

---

## Verification

### Unit tests: `cd pali && uv run pytest tests/ -v`

**test_protocol.py:**

- Parse complete checkpoint signal
- Parse complete backtrack signal (all field combinations: reason only, +rephrase, +mode, +both)
- Signals split across multiple chunks (partial `<<check` / `point:id>>`)
- Malformed signals emitted as plain text (no crash)
- `<<` followed by non-tag content (e.g., `x << 1`) flushed as text immediately
- Max signal length exceeded → flushed as text
- Nested `<` handling (e.g., `<a <<checkpoint:x>>`)
- `flush()` emits buffered incomplete signal as text at end of stream

**test_stream.py:**

- Mock token stream with checkpoints + backtracks → verify correct callback sequence
- Verify prefill construction includes raw signal markup
- Unknown checkpoint ID → ignored gracefully (no KeyError)
- Unknown mode value → falls back to current mode (no KeyError)
- Budget exhaustion → 4th backtrack surfaced as inline text
- Stale checkpoint pruning → checkpoints from discarded branch are removed
- Min-tokens-between-signals → checkpoint placed too soon is ignored
- API error → on_error called, message state cleaned up
- Normal completion (no backtracks) → on_done called with full text
- Backtrack hints reset between calls to `run()`

**test_inference.py:**

- Verify parameter mapping per mode (temperature, top_p values)
- `cancel()` awaits close (mock verification)
- Stream yields text deltas correctly

### Manual smoke test

1. `cd pali && uv run pali` — launches TUI
1. Type a question that's likely to trigger backtracking (e.g., "Explain quantum mechanics in one
    sentence" — the model will likely start too technical, then backtrack)
1. Verify: tokens stream in, checkpoint signals are invisible, backtrack causes visible rewind +
    rethink indicator, new text continues from checkpoint
1. Verify status bar updates backtrack count and mode
1. Ask the model to write code containing `<<` (e.g., "write C++ hello world") — verify parser
    doesn't hang on `std::cout <<`
1. Send a new message while streaming is active — verify cancel-and-replace behavior

### Edge cases to test manually

- Model doesn't backtrack at all (normal completion) — should work fine
- Model backtracks 3 times (budget limit) — 4th signal shows `[backtrack budget exhausted]`
- Model uses rephrase + mode shift together
- Model references non-existent checkpoint ID — ignored, no crash
- Model produces malformed signal — displayed as text, no crash
- Network error mid-stream — error message displayed, app remains functional

---

## Implementation Order

1. `pyproject.toml` + package scaffold with `uv init`
1. `protocol.py` + `test_protocol.py` — pure logic, no external deps, most testable
1. `config.py` — settings with Pydantic
1. `system_prompt.py` — crafted prompt with hint sanitization
1. `inference.py` + `test_inference.py` — Anthropic SDK wrapper with async cancel
1. `stream.py` + `test_stream.py` — core orchestrator with all guards
1. `widgets.py` — Textual widgets
1. `app.py` — wire everything together
1. `__main__.py` — entry point
1. Manual testing and prompt iteration
