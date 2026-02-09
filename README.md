# Palimpsest

> Where AI learns to use the backspace key.

Palimpsest (`pali`) is a terminal UI that enables an AI model to **rewind its own token stream
mid-generation**. The model places invisible checkpoints as it writes, detects suboptimal paths, and
signals the system to cancel inference, truncate context back to a checkpoint, and restart with a hint
about what went wrong.

This is cognitive backtracking — not error recovery, but **thinking in drafts**.

## How it works

The model's output contains inline markup signals that are invisible to the user but intercepted by a
streaming parser:

```
<<checkpoint:opening>>       — mark a decision point
<<backtrack:opening|reason>> — rewind to checkpoint, discard text after it, retry
```

On backtrack, the system cancels the running inference, truncates the response to the checkpoint, injects
the backtrack reason as a hint, and restarts generation. The user sees old text disappear and new text
stream in — a model that revises itself in real time.

### Signal protocol

```
<<checkpoint:ID>>                                   — place checkpoint
<<backtrack:ID|reason>>                             — simple backtrack
<<backtrack:ID|reason|rephrase:text>>               — reframe the question
<<backtrack:ID|reason|mode:precise>>                — shift cognitive mode
<<backtrack:ID|reason|rephrase:text|mode:precise>>  — full form
```

### Architecture

```
User Input -> StreamProcessor.run() -> InferenceManager.stream() -> Anthropic API
                    |                        |
              SignalParser <---- async token deltas
                    |
        TextChunk / Checkpoint / Backtrack
                    |
            Callbacks -> Textual Widgets
```

| Module             | Role                                                                               |
| ------------------ | ---------------------------------------------------------------------------------- |
| `protocol.py`      | Streaming state machine that parses checkpoint/backtrack signals                   |
| `stream.py`        | Orchestrator — manages retry loop, checkpoints, budget, continuation               |
| `inference.py`     | Wraps `AsyncAnthropic` for streaming with cancel/restart support                   |
| `system_prompt.py` | Dynamic prompt template with backtrack hint injection                              |
| `config.py`        | Settings and cognitive modes (`precise`, `exploratory`, `adversarial`, `balanced`) |
| `app.py`           | Textual TUI app wiring                                                             |
| `widgets.py`       | Custom Textual widgets for chat display                                            |

### Cognitive modes

The model can shift its temperature mid-response via the `mode` parameter:

| Mode          | Temperature | Use case                |
| ------------- | ----------- | ----------------------- |
| `balanced`    | 0.5         | Default                 |
| `precise`     | 0.2         | Focused, deterministic  |
| `exploratory` | 0.9         | Creative, divergent     |
| `adversarial` | 0.7         | Critical, stress-tested |

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- `ANTHROPIC_API_KEY` environment variable

## Installation

```bash
git clone https://github.com/titusz/pali.git
cd pali
uv sync
```

## Usage

```bash
uv run pali                      # launch TUI
uv run pali --debug              # with debug panel + trace file (pali_debug.log)
uv run pali --model <model-id>   # use a specific Anthropic model (default: claude-opus-4-6)
```

## Development

```bash
# run tests
uv run pytest

# run tests with coverage (80% minimum)
uv run pytest --cov=pali --cov-fail-under=80

# run all quality gates
uv run prek run --all-files

# install git pre-commit hook
uv run prek install
```

### Quality gates

Pre-commit hooks managed by [prek](https://github.com/j178/prek):

- **ruff** — lint + format (line-length 100, Python 3.12 target)
- **ty** — type checking
- **pytest** — tests with 80% coverage gate
- **mdformat** — markdown formatting
- Whitespace, line endings, YAML/TOML validation, large file checks

## Design decisions

- **Multi-turn continuation (not prefill)**: Retry sends
    `[user, assistant(preserved_text), user(continue_instruction)]` — works with all models.
- **Committed vs transient state**: Only complete user/assistant pairs are stored in conversation
    history. In-progress responses are transient until successful.
- **Budget enforcement**: Maximum 3 backtracks per response, minimum 30 tokens between signals.
- **Stale checkpoint pruning**: After rewind to checkpoint A, all checkpoints beyond A are removed.

## Stack

Python 3.12+ / [Textual](https://textual.textualize.io/) /
[Anthropic SDK](https://docs.anthropic.com/en/api/client-sdks/python) /
[Pydantic](https://docs.pydantic.dev/) / [Typer](https://typer.tiangolo.com/) /
[Hatchling](https://hatch.pypa.io/)
