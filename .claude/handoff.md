# Palimpsest — Handoff Notes

## What This Is

A standalone TUI demo that gives an AI the ability to **rewind its own token stream mid-generation**.
The model places invisible checkpoints, and when it detects a wrong path (or a notably better
alternative), it signals to backtrack. The stream processor intercepts the signal, cancels inference,
truncates context to the checkpoint, and restarts with a hint about what went wrong.

**Stack:** Textual TUI + Anthropic Python SDK + Pydantic, managed with uv.

**Location:** `C:\Users\titusz\Code\sheldrake\`

## Current State

- **43 tests passing** — protocol parser, stream processor, inference manager all covered
- **TUI functional** — streaming chat with Markdown rendering, debug panel, status bar
- **Multi-turn conversation** — `StreamProcessor` persists across messages, full history sent to API
- **Backtracking works end-to-end with Opus 4.6** — confirmed with multiple prompts (see below)
- **Sonnet 4 also works** — backtracks more frequently than Opus

### What Changed Recently

1. **Multiline ChatInput** — Replaced single-line `Input` with `ChatInput(TextArea)` subclass.
    Enter submits, Shift+Enter / Ctrl+J inserts newline. Auto-grows up to 6 lines.

1. **Win32 modifier+Enter monkeypatch** — Textual's VT input mode zeroes `dwControlKeyState`,
    making Shift+Enter indistinguishable from Enter. `_win32_keys.py` patches `EventMonitor.run`
    to read one input record at a time and poll `GetAsyncKeyState` (user32) for modifier state.
    Injects Kitty CSI u sequences (`\x1b[13;2u` for shift+enter) so `XTermParser` produces the
    correct key binding. Tracks: https://github.com/Textualize/textual/issues/6074

1. **Esc to cancel inference** — `Binding("escape", "cancel_inference")` with `check_action` to
    show only during active inference. Cancels the `@work(exclusive=True)` worker.

1. **System prompt rewrite** — shifted framing from "error recovery tool" to "thinking in drafts."
    Key changes: identity-level framing ("you think in drafts"), checkpoint discipline section with
    self-evaluation questions, 4 few-shot examples (up from 1), lowered backtrack threshold from
    "wrong direction" to "notably better alternative available," removed generous opt-out language.

1. **Replaced assistant prefill with multi-turn continuation** — Opus 4.6 doesn't support assistant
    message prefill (`"This model does not support assistant message prefill"`). The retry path now
    uses `[user, assistant_preserved_text, user_continue_instruction]` instead of
    `[user, assistant_prefill]`. Works with all models. The phantom checkpoint concept (embedding
    `<<checkpoint:id>>` in prefill) is gone — re-emitted checkpoints just get re-registered harmlessly.

## Confirmed Backtrack Results (Opus 4.6)

### Breakup text — tone correction

**Prompt:** "Write the first paragraph of a breakup text to someone you still love"

- V1: "Hey. I need to say something I've been carrying for a while, and I don't know how to make
    these words do what I need them to do."
- **Backtrack reason:** "too polished and writerly — a real breakup text wouldn't sound like a craft
    essay opening. It should feel like someone's hands are shaking while they type"
- V2: "Hey. I don't even know how to start this because every version I've typed sounds wrong. I
    keep deleting and rewriting..." — categorically better, raw emotional register

### Monty Hall — structural/pedagogical correction

**Prompt:** "Explain the Monty Hall problem — but make it intuitive, not mathematical"

- V1: 100-door analogy was good but ended without explicitly connecting back to 3-door version
- **Backtrack reason:** "The 100-door version is good but I should land the plane — connect it back
    to the 3-door version and make the 'aha' explicit rather than leaving the reader to bridge the gap"
- V2: Added "Now shrink that back down to three doors. The *exact same logic* applies..." and a
    stronger closing line. Noticeably better pedagogical arc.

**Key observation:** These are two different *types* of backtrack — tone/register correction vs.
structural/pedagogical refinement. The prompt successfully triggers both.

## How to Run

```bash
cd sheldrake
uv run sheldrake                    # normal mode
uv run sheldrake --debug            # with debug panel + trace file (sheldrake_debug.log)
uv run sheldrake --model claude-sonnet-4-20250514   # force a specific model
uv run pytest tests/ -v        # run all tests
```

Requires `ANTHROPIC_API_KEY` in environment. Without it, the TUI shows an error message.

## Architecture

```
src/sheldrake/
├── protocol.py      # Signal types + streaming parser state machine
├── config.py        # Settings, cognitive modes, defaults
├── system_prompt.py # Prompt template with dynamic backtrack hint injection
├── inference.py     # Anthropic SDK wrapper with async cancel
├── stream.py        # Core orchestrator — the retry loop
├── _win32_keys.py   # Monkeypatch: modifier+Enter via GetAsyncKeyState
├── widgets.py       # Textual widgets (ChatInput, AssistantMessage, etc.)
├── app.py           # TUI app wiring
└── __main__.py      # CLI entry point (typer)
```

### Data Flow

```
User input
  → StreamProcessor.run()
    → InferenceManager.stream() → Anthropic API (streaming)
      → raw token deltas
    → SignalParser.feed(delta)
      → TextChunk       → on_text callback → Markdown widget
      → Checkpoint       → stored in checkpoints dict
      → Backtrack        → cancel stream, truncate to checkpoint, retry with hint
    → on_done callback
```

### Key Design Decisions

- **Multi-turn continuation (not prefill)**: On retry after backtrack, preserved text goes into an
    `assistant` message, followed by a `user` message ("Continue directly from where you left off").
    This replaced the original prefill approach because Opus 4.6 doesn't support assistant prefill.
    Works with all models. The ephemeral continuation messages are built from a copy and never
    committed to `self.messages`.
- **Committed vs transient state**: `self.messages` only contains complete user/assistant pairs.
    The in-progress response is transient and only committed on successful completion.
- **Hints reset per response**: Backtrack hints accumulate within a response but reset when a new
    user message arrives.
- **`_maybe_await` pattern**: StreamProcessor callbacks can be sync (tests) or async (TUI). The
    `_maybe_await` helper checks and conditionally awaits.
- **Backtracking is per-turn only**: Checkpoints are scoped to each `run()` call. The model cannot
    backtrack into a previous conversational turn (see "Future Directions" below).

### Prompt Design (What Worked for Opus)

The system prompt went through two iterations. What finally worked:

1. **Identity framing, not capability framing** — "You think in drafts" (identity) vs "You have
    cognitive backtracking" (tool you can use). Opus ignores optional tools; it doesn't ignore
    who it is.

1. **Lowered backtrack threshold** — From "backtrack when wrong" to "backtrack when you can
    articulate a notably better alternative." Opus rarely generates something *wrong*, but it
    often generates something that could be *sharper*.

1. **Checkpoint Discipline section** — Three self-evaluation questions at each checkpoint. Creates
    the metacognitive pause where the model might notice a better path.

1. **Multiple diverse examples** — Four examples showing different backtrack types (reframing,
    overcomplexity, tone correction, mid-response structural revision). The original single
    example only showed opening-level backtracking.

1. **Removed opt-out language** — The old prompt said "Use this authentically" and "Anything
    adequate but not perfect" shouldn't trigger backtracking. Both gave Opus permission to skip.

1. **Autopilot callout** — "A response with zero backtracks should mean you genuinely considered
    alternatives at each checkpoint — not that you autopiloted through."

## Bugs Found and Fixed During Implementation

| Bug                                                    | Root Cause                                                           | Fix                                                      |
| ------------------------------------------------------ | -------------------------------------------------------------------- | -------------------------------------------------------- |
| No response at all without API key                     | `AsyncAnthropic()` raises in constructor before TUI starts           | Defer client creation to `on_mount`, check env var first |
| Text never appears in TUI                              | `on_text` is async but called without `await`                        | Added `_maybe_await()` for all callbacks                 |
| `AttributeError: 'debug' has no setter`                | Textual's `App` has a `debug` property                               | Renamed to `_show_debug`                                 |
| `'AsyncMessageStreamManager' has no attribute 'close'` | `_active_stream` stored the manager, not the inner stream            | Store the stream from inside `async with`                |
| `temperature and top_p cannot both be specified`       | Opus doesn't allow both                                              | Removed `top_p` from modes, only use `temperature`       |
| First checkpoint always ignored                        | `chars_since_last_signal` started at 0, failing the min-tokens guard | Initialize to `min_tokens_between_signals`               |
| Retry crashes with Opus                                | Opus doesn't support assistant message prefill                       | Replaced prefill with multi-turn continuation pattern    |

## Known Issues

### Checkpoint re-emission after backtrack

With multi-turn continuation (no prefill), the model doesn't see checkpoint tags from the preserved
text. It may re-emit the same checkpoint ID. This is harmless — the checkpoint gets re-registered
in the dict — but it wastes a few tokens and resets `chars_since_last_signal`.

## Next Task

See `.claude/conversations/cryptobiosis-analysis.md` for the analysis of the existing trace that
motivated this change, and `cryptobiosis.md` for the recovered conversation.

## Future Directions

### Temperature self-awareness and control (next up)

The model can already switch modes on backtrack (`mode:exploratory`), but it doesn't know what
temperature it's running at or what the modes map to. Give it that self-knowledge.

**Implementation plan (3 levels, do together):**

1. **Transparency in system prompt** — Add current temperature and mode mapping to the prompt:
    "You are generating at temperature 0.5 (balanced). Available modes: precise (0.2),
    exploratory (0.9), adversarial (0.7), balanced (0.5)." The model can now make informed
    mode-shift decisions on backtrack.

1. **Direct temperature control** — Add `temp:X` as a backtrack parameter alongside `mode:`:
    `<<backtrack:ID|reason|temp:0.7>>`. Lets the model pick an exact value instead of a preset.
    Parser change: handle `temp:` prefix in backtrack parts (like `mode:` and `rephrase:`).
    Stream processor change: validate float in [0, 1], pass to inference manager, update current
    temperature. Config change: modes dict stays for presets, but raw temperature overrides.

1. **Dynamic system prompt** — `build_system_prompt` already runs fresh each retry. Pass the
    current temperature/mode so the model sees "You're now at 0.9 (exploratory)" after a mode
    shift, and can decide whether to shift again or settle.

**Where to change:**

- `protocol.py`: Add `temperature: float | None = None` field to `Backtrack`, parse `temp:` parts
- `stream.py`: Handle `bt.temperature`, validate range, pass to inference, update system prompt
- `system_prompt.py`: `build_system_prompt` takes current mode + temperature, renders into prompt
- `config.py`: No changes needed — modes dict stays, raw temp is a separate path
- `inference.py`: Accept optional temperature override (already gets mode, just need temp float)
- Tests: Add cases for `temp:` parsing, validation (out of range, non-numeric), stream behavior

**Design note:** `temp:0.7` and `mode:adversarial` are different paths to the same thing (adversarial
maps to 0.7). If both are specified in a single backtrack, `temp:` should win — it's the more
specific instruction. Mode labels remain useful as semantic shortcuts.

**Previous note on static temperature tuning:** Balanced mode is 0.5. The prompt changes alone were
sufficient to trigger backtracking with Opus, but the default might benefit from bumping to 0.6–0.7.
This is separate from — and less interesting than — letting the model control its own temperature.

### Benchmarking

To evaluate whether backtracking produces measurably better output:

- **Best approach:** Paired evaluation — same prompts to vanilla Opus vs Palimpsest, blind human
    preference ranking on a curated set of framing-sensitive prompts.
- **Where backtracking helps most:** Creative writing (tone/register), explanations (audience
    matching), opinion/argument (opening commitment), emotional register.
- **Where it probably doesn't help:** Factual questions, code generation, well-structured tasks
    where Opus nails the framing on the first try.
- **Cost:** ~2x tokens and latency per backtrack. The question is which tasks justify the compute.

### Cross-turn backtracking

Currently, checkpoints are scoped to each `run()` call. The model cannot backtrack into a previous
conversational turn. Cross-turn backtracking would be fundamentally different — more like "undo"
than "revise." Would require: rolling back UI, discarding the user's follow-up context, rethinking
what "rewind to here" means across conversational exchanges. Deliberately out of scope for now.

### Test prompts that reliably trigger backtracking

**Confirmed working (Opus 4.6):**

- "Write the first paragraph of a breakup text to someone you still love"
- "Explain the Monty Hall problem — but make it intuitive, not mathematical"

**Untested but likely effective:**

- "Is consciousness an illusion? Take a strong position." (forces premature commitment)
- "Explain why 0.999... = 1 to someone who doesn't believe it" (easy to start too formal)
- "Write the opening paragraph of a novel about loneliness" (creative — tone is everything)
- "Summarize the plot of Hamlet in exactly one sentence" (compression forces trade-offs)
- "Explain to a 5-year-old why the sky is blue" (easy to start too technical)

## Debug Trace Format

When running with `--debug`, the debug panel (RichLog) shows events with Rich markup, and
`sheldrake_debug.log` gets the same content with markup stripped. Trace format:

```
user: 'the user message'                          — user message (turn start)
start: mode=balanced                              — response started
text: 'clean text chunk'                          — clean parser output (no signal markup)
checkpoint: id (pos=N, total=M)                   — checkpoint accepted
checkpoint ignored (too soon): id                 — rejected by min-tokens guard
BACKTRACK: → id | reason | mode:X                 — backtrack triggered
retry: hints=[...]                                — retry with accumulated hints
backtrack budget exhausted                        — 4th+ backtrack rejected
error: message                                    — API/network error
done: N chars, M backtracks                       — response complete
```

To recover the final conversation from a trace: concatenate `text:` lines between `start:` and
`done:`, discarding text between `BACKTRACK:` and the next `text:` line.

## Test Coverage

- **test_protocol.py** (24 tests): Parser state machine, chunked input, code collision (`<<` in
    C++), malformed signals, max length, flush behavior
- **test_stream.py** (13 tests): Normal completion, backtrack flow, multi-turn continuation, unknown
    checkpoints/modes, budget exhaustion, stale pruning, min-tokens guard, API errors, hint reset
- **test_inference.py** (6 tests): Mode parameters, cancel behavior, stream delegation

## Dependencies

```toml
[project]
dependencies = [
  "textual>=1.0",
  "anthropic>=0.45",
  "pydantic>=2.0",
  "typer>=0.15",
]
```

Dev: `pytest>=8.0`, `pytest-asyncio>=0.25`

## UX Improvement Proposals

Grounded in Laws of UX, mapped to Textual capabilities.

### P0 — High Impact

- ~~**1. Multiline ChatInput** (Jakob's Law) — TextArea subclass, Enter/Shift+Enter, auto-grow 1-6 lines~~ DONE
- **2. Disable Input During Inference** (Postel's Law) — `widget.loading` or `disabled`
- **3. Footer with Dynamic Key Bindings** (Hick's Law) — context-sensitive shortcut hints
- ~~**4. Cancel Inference with Esc** (Goal-Gradient) — `Binding("escape", ...)` + worker cancel~~ DONE

### P1 — Medium Impact

- **5. Dynamic Placeholder / Status Text** (Goal-Gradient) — contextual hints near input
- **6. Auto-refocus After Response** (Doherty Threshold) — `widget.focus()` in finally block
- **7. Toast Notifications** (Peak-End Rule) — `App.notify()` for state changes

### P2 — Nice to Have

- **8. Input History Navigation** (Zeigarnik + Serial Position) — Up/Down arrow cycles history
- **9. Slash-Command Autocomplete** (Hick's Law) — `/help`, `/clear`, `/model`
- **10. Command Palette** (Miller's Law) — Ctrl+P fuzzy command search

### P3 — Polish

- **11. Smooth Height Animation** (Doherty Threshold) — `styles.animate("height", ...)`
- ~~**12. Focus-Aware Styling** (Common Region + Aesthetic-Usability) — `:focus` pseudo-class~~ DONE (ChatInput)
- **13. Streaming Markdown Optimization** (Doherty Threshold) — verify `MarkdownStream` batching
- **14. Character Count Indicator** (Miller's Law) — char count when input > 500 chars
