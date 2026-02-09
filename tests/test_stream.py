"""Tests for the stream processor orchestrator."""

from __future__ import annotations

import pytest

from pali.config import Settings
from pali.protocol import Backtrack
from pali.stream import StreamProcessor


class FakeInference:
    """Test double for InferenceManager that yields scripted token sequences.

    Each call to stream() pops the next sequence from the list.
    """

    def __init__(self, sequences: list[list[str]]) -> None:
        self._sequences = list(sequences)
        self.cancel_count = 0

    async def stream(self, messages, system, mode="balanced"):
        seq = self._sequences.pop(0)
        for token in seq:
            yield token

    async def cancel(self):
        self.cancel_count += 1


class Callbacks:
    """Collects callback invocations for assertions."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.backtracks: list[tuple[Backtrack, str]] = []
        self.errors: list[str] = []
        self.done: list[str] = []

    def on_text(self, t: str) -> None:
        self.texts.append(t)

    def on_backtrack(self, bt: Backtrack, text: str) -> None:
        self.backtracks.append((bt, text))

    def on_error(self, msg: str) -> None:
        self.errors.append(msg)

    def on_done(self, text: str) -> None:
        self.done.append(text)


@pytest.fixture
def settings():
    return Settings(min_tokens_between_signals=0)


# --- Normal completion ---


@pytest.mark.asyncio
async def test_normal_completion(settings):
    """Simple response with no signals — on_done called with full text."""
    fake = FakeInference([["Hello", " world"]])
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("hi", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert "".join(cb.texts) == "Hello world"
    assert len(cb.done) == 1
    assert cb.done[0] == "Hello world"
    assert len(cb.backtracks) == 0
    assert len(cb.errors) == 0


# --- Checkpoint + backtrack ---


@pytest.mark.asyncio
async def test_checkpoint_and_backtrack(settings):
    """Checkpoint followed by backtrack should truncate and retry."""
    fake = FakeInference(
        [
            # First attempt: checkpoint, some text, then backtrack
            ["<<checkpoint:intro>>", "Wrong start", "<<backtrack:intro|bad framing>>"],
            # Retry: correct text
            ["Good start, better content"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert len(cb.backtracks) == 1
    bt, truncated_text = cb.backtracks[0]
    assert bt.checkpoint_id == "intro"
    assert bt.reason == "bad framing"
    assert truncated_text == ""  # checkpoint was at start, so truncated to empty
    assert cb.done[0] == "Good start, better content"


@pytest.mark.asyncio
async def test_backtrack_preserves_text_before_checkpoint(settings):
    """Text before checkpoint should survive the backtrack."""
    fake = FakeInference(
        [
            ["Preamble. ", "<<checkpoint:mid>>", "Bad path", "<<backtrack:mid|wrong>>"],
            ["Better path"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    _, truncated = cb.backtracks[0]
    assert truncated == "Preamble. "
    assert cb.done[0] == "Preamble. Better path"


# --- Multi-turn continuation after backtrack ---


@pytest.mark.asyncio
async def test_retry_uses_multiturn_continuation(settings):
    """Retry after backtrack uses multi-turn (assistant + user) instead of prefill."""
    messages_seen = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced"):
            messages_seen.append(messages)
            async for t in super().stream(messages, system, mode):
                yield t

    fake = SpyInference(
        [
            ["Before ", "<<checkpoint:a>>", "After ", "<<backtrack:a|oops>>"],
            ["Fixed"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert len(messages_seen) == 2
    retry_msgs = messages_seen[1]
    # Should have: user, assistant (preserved text), user (continue instruction)
    assert retry_msgs[-2]["role"] == "assistant"
    assert retry_msgs[-2]["content"] == "Before "
    assert retry_msgs[-1]["role"] == "user"
    assert "Continue" in retry_msgs[-1]["content"]


# --- Unknown checkpoint ID ---


@pytest.mark.asyncio
async def test_unknown_checkpoint_id_ignored(settings):
    """Backtrack referencing non-existent checkpoint should be silently ignored."""
    fake = FakeInference(
        [
            ["<<checkpoint:real>>", "text", "<<backtrack:fake|oops>>", " more"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert len(cb.backtracks) == 0
    assert " more" in "".join(cb.texts)


# --- Unknown mode ---


@pytest.mark.asyncio
async def test_unknown_mode_falls_back(settings):
    """Backtrack with unknown mode should fall back to current mode."""
    modes_used = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced"):
            modes_used.append(mode)
            async for t in super().stream(messages, system, mode):
                yield t

    fake = SpyInference(
        [
            ["<<checkpoint:a>>", "text", "<<backtrack:a|reason|mode:nonexistent>>"],
            ["ok"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # Should have used balanced for both calls (fallback from nonexistent)
    assert modes_used == ["balanced", "balanced"]


# --- Budget exhaustion ---


@pytest.mark.asyncio
async def test_budget_exhaustion():
    """4th backtrack should be rejected with inline text."""
    settings = Settings(max_backtracks=2, min_tokens_between_signals=0)
    fake = FakeInference(
        [
            ["<<checkpoint:a>>", "try1", "<<backtrack:a|r1>>"],
            ["<<checkpoint:b>>", "try2", "<<backtrack:b|r2>>"],
            ["<<checkpoint:c>>", "try3", "<<backtrack:c|r3>>", " final"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert len(cb.backtracks) == 2
    combined = "".join(cb.texts)
    assert "[backtrack budget exhausted]" in combined


# --- Stale checkpoint pruning ---


@pytest.mark.asyncio
async def test_stale_checkpoints_pruned(settings):
    """Checkpoints from discarded branches should be removed after backtrack."""
    fake = FakeInference(
        [
            [
                "<<checkpoint:a>>",
                "text",
                "<<checkpoint:b>>",
                "more",
                "<<backtrack:a|wrong>>",
            ],
            # After rewinding to 'a', checkpoint 'b' should be gone
            ["<<backtrack:b|try to use pruned>>", "ok"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # Only 1 backtrack should succeed (to 'a'), the second ('b') should be ignored
    assert len(cb.backtracks) == 1
    assert cb.backtracks[0][0].checkpoint_id == "a"


# --- Min tokens between signals ---


@pytest.mark.asyncio
async def test_min_tokens_between_signals():
    """Checkpoint placed too soon after another signal should be ignored."""
    settings = Settings(min_tokens_between_signals=10)
    fake = FakeInference(
        [
            # 'a' accepted (first signal always allowed), 'b' rejected (only 5 chars between)
            ["<<checkpoint:a>>", "short", "<<checkpoint:b>>", "<<backtrack:b|r>>", "end"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # 'b' should be ignored (only 5 chars "short" between signals)
    # backtrack to 'b' should fail (unknown) → no backtracks
    assert len(cb.backtracks) == 0


# --- API error handling ---


@pytest.mark.asyncio
async def test_api_error_calls_on_error(settings):
    """API errors should call on_error and clean up message state."""

    class FailingInference:
        async def stream(self, messages, system, mode="balanced"):
            raise ConnectionError("network down")
            yield  # make it a generator

        async def cancel(self):
            pass

    proc = StreamProcessor(FailingInference(), settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert len(cb.errors) == 1
    assert "network down" in cb.errors[0]
    assert len(proc.messages) == 0  # user message cleaned up


# --- Backtrack with mode shift ---


@pytest.mark.asyncio
async def test_backtrack_with_mode_shift(settings):
    """Mode shift in backtrack signal should change inference mode."""
    modes_used = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced"):
            modes_used.append(mode)
            async for t in super().stream(messages, system, mode):
                yield t

    fake = SpyInference(
        [
            ["<<checkpoint:a>>", "text", "<<backtrack:a|reason|mode:precise>>"],
            ["precise text"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert modes_used == ["balanced", "precise"]


# --- Hints reset between runs ---


@pytest.mark.asyncio
async def test_hints_reset_between_runs(settings):
    """Backtrack hints should not carry over between separate run() calls."""
    systems_seen = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced"):
            systems_seen.append(system)
            async for t in super().stream(messages, system, mode):
                yield t

    fake = SpyInference(
        [
            # First run: has a backtrack
            ["<<checkpoint:a>>", "bad", "<<backtrack:a|was wrong>>"],
            ["good"],
            # Second run: clean
            ["clean response"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("q1", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)
    await proc.run("q2", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # The system prompt for q2's first attempt should NOT contain hints
    # systems_seen: [q1-attempt1, q1-attempt2, q2-attempt1]
    assert len(systems_seen) == 3
    assert "Active Backtrack Context" in systems_seen[1]  # q1 retry has hint
    assert "Active Backtrack Context" not in systems_seen[2]  # q2 is clean


# --- Message history ---


@pytest.mark.asyncio
async def test_committed_messages_after_success(settings):
    """Successful completion should commit user + assistant messages."""
    fake = FakeInference([["Hello!"]])
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("hi", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    assert len(proc.messages) == 2
    assert proc.messages[0] == {"role": "user", "content": "hi"}
    assert proc.messages[1] == {"role": "assistant", "content": "Hello!"}
