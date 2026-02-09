# Plan: ChatInput Widget + Footer

## Context

The current chat input uses Textual's single-line `Input` widget (`app.py:63`). This prevents
multiline input, collapses pasted newlines, and lacks the Enter/Shift+Enter behavior users
expect from chat apps (Jakob's Law). We're replacing it with a `TextArea` subclass that
auto-grows with content and submits on Enter. Also adding Footer with dynamic key bindings.

## Changes

### 1. New `ChatInput` widget in `widgets.py`

Subclass `TextArea` with:

- **`Submitted` message** — custom message carrying the input value, mirroring `Input.Submitted`
- **`_on_key` override** — Enter (no shift) posts `Submitted` and clears; Shift+Enter inserts
    newline (default TextArea behavior, just don't intercept)
- **CSS** — `height: auto; max-height: 6;` for auto-grow capped at 6 lines. Bottom-docked.
    Disable line numbers, set `show_line_numbers=False`, `tab_behavior="focus"` (Tab moves focus,
    not indent). Theme-aware border styling with `:focus` pseudo-class.
- **Empty submit guard** — strip whitespace, ignore if empty

### 2. Update `app.py`

- Replace `Input` import with `ChatInput` from `pali.widgets`
- Replace `yield Input(...)` with `yield ChatInput(id="input")`
- Rename handler from `on_input_submitted` to `on_chat_input_submitted`
- Update `on_mount` focus call: `self.query_one("#input", ChatInput).focus()`
- Update handler to use `event.value` from `ChatInput.Submitted`

### 3. Add `Footer` with dynamic key bindings

- Add `Footer` widget to `compose()`
- Add `BINDINGS` with `Binding("escape", "cancel_inference", "Cancel", show=False)`
- Implement `action_cancel_inference` to cancel the current worker
- Use `check_action` to show Esc binding only when inference is running

## Files Modified

| File                  | Change                                                                            |
| --------------------- | --------------------------------------------------------------------------------- |
| `src/pali/widgets.py` | Add `ChatInput(TextArea)` class                                                   |
| `src/pali/app.py`     | Replace `Input` with `ChatInput`, add `Footer`, update handler, add cancel action |

## Pre-implementation: Create `.claude/handoff.md`

Before modifying source files, create `.claude/handoff.md` with all 14 UX improvement proposals
(the full list from our conversation) so they're preserved for future sessions.

## Verification

1. `uv run pali` — type text, verify Enter submits, Shift+Enter adds newline
1. Paste a multiline block — should preserve newlines
1. Type 6+ lines — input should cap at 6 lines and scroll internally
1. Submit empty input — should be silently ignored
1. Footer shows key hints, Esc cancels during inference
1. `uv run prek run --all-files` — all quality gates pass

---

## Appendix: Full UX Improvement Proposals (for handoff.md)

Grounded in Laws of UX, mapped to Textual capabilities.

### P0 — High Impact

- **1. Multiline ChatInput** (Jakob's Law) — TextArea subclass, Enter/Shift+Enter, auto-grow 1-6 lines
- **2. Disable Input During Inference** (Postel's Law) — `widget.loading` or `disabled`
- **3. Footer with Dynamic Key Bindings** (Hick's Law) — context-sensitive shortcut hints
- **4. Cancel Inference with Esc** (Goal-Gradient) — `Binding("escape", ...)` + worker cancel

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
- **12. Focus-Aware Styling** (Common Region + Aesthetic-Usability) — `:focus` pseudo-class
- **13. Streaming Markdown Optimization** (Doherty Threshold) — verify `MarkdownStream` batching
- **14. Character Count Indicator** (Miller's Law) — char count when input > 500 chars
