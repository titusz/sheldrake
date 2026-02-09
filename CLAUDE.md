# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@.claude/learnings.md

## Project Overview

**Palimpsest (sheldrake)** is a TUI demo application that enables an AI model to rewind its own token stream
mid-generation through **cognitive backtracking**. The model places invisible checkpoints during generation,
detects suboptimal paths, and signals the system to cancel inference, truncate context to a checkpoint, and
restart with a hint about what went wrong.

Stack: Python 3.12+, Textual (TUI), Anthropic SDK (async streaming), Pydantic, Typer. Packaged with
Hatchling, managed with uv.

## Commands

```bash
# Install and run
uv sync                          # Install dependencies
uv run sheldrake                      # Launch TUI
uv run sheldrake --debug              # Launch with debug panel + trace file (sheldrake_debug.log)
uv run sheldrake --model <model-id>   # Use specific Anthropic model

# Tests
uv run pytest                    # Run all tests (43 tests)
uv run pytest tests/test_protocol.py -v          # Parser tests only
uv run pytest tests/test_stream.py -v            # Orchestrator tests only
uv run pytest tests/test_protocol.py::test_name  # Single test
uv run pytest --cov=sheldrake --cov-fail-under=80     # With coverage (80% minimum)

# Quality gates (prek — pre-commit replacement)
uv run prek run --all-files      # Run all hooks on all files
uv run prek run                  # Run hooks on staged files only
uv run prek install              # Install git pre-commit hook
```

## Architecture

The system intercepts inline markup signals (`<<checkpoint:ID>>`, `<<backtrack:ID|reason>>`) from the
model's token stream to implement retry logic:

```
User Input → StreamProcessor.run() → InferenceManager.stream() → Anthropic API
                    ↓                        ↓
              SignalParser ←──── async token deltas
                    ↓
        TextChunk / Checkpoint / Backtrack
                    ↓
            Callbacks → Textual Widgets
```

### Key modules (`src/sheldrake/`)

- **protocol.py** — `SignalParser` streaming state machine. Parses `<<checkpoint:ID>>` and
    `<<backtrack:ID|reason|rephrase:text|mode:name|temp:X>>` from raw token deltas. Handles `<<`
    collisions with C++ code through strict prefix validation before entering signal mode.

- **stream.py** — `StreamProcessor` orchestrator. Manages the backtrack retry loop: tracks checkpoints,
    handles rewind on backtrack signal, builds multi-turn continuation messages (not prefill — Opus 4.6
    doesn't support it), enforces budget (max 8 backtracks) and min-token spacing. Uses `InferenceLike`
    Protocol so test doubles satisfy the type checker. Mutable run state lives in a `_RunCtx` dataclass.

- **inference.py** — `InferenceManager` wraps AsyncAnthropic for streaming with cancel/restart support.
    Maps cognitive modes to temperature values. Accepts optional temperature override from backtrack.

- **system_prompt.py** — Dynamic prompt template. `build_system_prompt()` injects accumulated backtrack
    hints and current generation state (temperature, mode) between retries. Hints reset per user message
    (not carried across turns).

- **config.py** — `Settings` (Pydantic model) and cognitive modes: `precise` (0.2), `exploratory` (0.9),
    `adversarial` (0.7), `balanced` (0.6 — default).

- **app.py** — Textual app orchestration. Lazy API client init (checks `ANTHROPIC_API_KEY` on mount).
    Uses `@work(exclusive=True)` for cancel-and-replace on new input.

- **widgets.py** — Custom Textual widgets: `UserMessage`, `AssistantMessage` (streaming markdown),
    `BacktrackIndicator`, `StatusBar`.

### Backtrack protocol signals

```
<<checkpoint:ID>>                                              # Place checkpoint
<<backtrack:ID|reason>>                                        # Simple backtrack
<<backtrack:ID|reason|rephrase:text>>                          # With prompt reframing
<<backtrack:ID|reason|mode:precise>>                           # With cognitive mode shift
<<backtrack:ID|reason|temp:0.7>>                               # With direct temperature
<<backtrack:ID|reason|rephrase:text|mode:precise|temp:0.3>>    # Full form
```

### Design decisions

- **Multi-turn continuation (not prefill)**: Backtrack retry sends `[user, assistant(preserved_text), user(continue_instruction)]`. Works with all models.
- **Committed vs transient state**: `self.messages` only stores complete user/assistant pairs. In-progress
    response is transient until success.
- **Stale checkpoint pruning**: After rewind to checkpoint A, all checkpoints beyond A are removed.
- **Temperature override**: `temp:X` in backtrack signals takes precedence over mode-derived temperature.
    Out-of-range values (outside 0.0-1.0) are discarded. The system prompt always shows the model its
    current temperature and mode.
- **`_maybe_await` pattern**: Callbacks can be sync (tests) or async (TUI) — helper inspects and
    conditionally awaits.

## Testing patterns

- `FakeInference` fixture yields scripted token sequences for deterministic testing of the streaming
    pipeline without API calls.
- `Callbacks` fixture collects callback invocations for assertions.
- Tests use `min_tokens_between_signals=0` to avoid spacing guards interfering with test logic.
- Coverage requirement: 80% minimum (enforced by pre-commit hook). TUI modules (`app.py`, `widgets.py`,
    `__main__.py`) are excluded from coverage since they require a running Textual app.

## Code quality gates

Pre-commit hooks managed by [prek](https://github.com/j178/prek) (`.pre-commit-config.yaml`):

| Hook                                               | Purpose                                     |
| -------------------------------------------------- | ------------------------------------------- |
| `trailing-whitespace`, `end-of-file-fixer`         | Whitespace cleanup                          |
| `mixed-line-ending --fix=lf`                       | Enforce LF line endings                     |
| `check-yaml`, `check-toml`, `check-merge-conflict` | File validation                             |
| `check-added-large-files`                          | Prevent accidental large file commits       |
| `ruff-check --fix`                                 | Lint (E, F, W, I, UP, B, C, SIM, RUF rules) |
| `ruff-format`                                      | Code formatting (line-length=100)           |
| `mdformat` with `mdformat-mkdocs[recommended]`     | Markdown formatting                         |
| `ty check`                                         | Type checking                               |
| `pytest --cov=sheldrake --cov-fail-under=80`       | Tests + coverage gate                       |

Ruff config: `line-length = 100`, `target-version = "py312"`, McCabe `max-complexity = 10` (grade B).

## Environment

- Requires `ANTHROPIC_API_KEY` environment variable (validated at TUI startup).
- Default model: `claude-opus-4-6`.
- Cross-platform support required (Windows, macOS, Linux).
