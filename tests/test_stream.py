"""Tests for the stream processor orchestrator."""

from __future__ import annotations

import asyncio

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

    async def stream(self, messages, system, mode="balanced", temperature=None):
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
        async def stream(self, messages, system, mode="balanced", temperature=None):
            messages_seen.append(messages)
            async for t in super().stream(messages, system, mode, temperature):
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
        async def stream(self, messages, system, mode="balanced", temperature=None):
            modes_used.append(mode)
            async for t in super().stream(messages, system, mode, temperature):
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
        async def stream(self, messages, system, mode="balanced", temperature=None):
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
        async def stream(self, messages, system, mode="balanced", temperature=None):
            modes_used.append(mode)
            async for t in super().stream(messages, system, mode, temperature):
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
        async def stream(self, messages, system, mode="balanced", temperature=None):
            systems_seen.append(system)
            async for t in super().stream(messages, system, mode, temperature):
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
    assert "Constraints for This Attempt" in systems_seen[1]  # q1 retry has hint
    assert "Constraints for This Attempt" not in systems_seen[2]  # q2 is clean


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


@pytest.mark.asyncio
async def test_cancellation_cleans_up_user_message(settings):
    """CancelledError should roll back the pending user message."""

    class HangingInference:
        async def stream(self, messages, system, mode="balanced", temperature=None):
            await asyncio.sleep(999)
            yield ""  # make it a generator

        async def cancel(self):
            pass

    proc = StreamProcessor(HangingInference(), settings)
    cb = Callbacks()

    task = asyncio.create_task(
        proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)
    )
    await asyncio.sleep(0)  # let task start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(proc.messages) == 0


# --- Debug trace format ---


@pytest.mark.asyncio
async def test_debug_trace_emits_text_and_user_lines(settings):
    """Debug trace should emit user: and text: lines, not raw: lines."""
    debug_lines = []
    fake = FakeInference(
        [
            ["<<checkpoint:a>>", "Hello", "<<backtrack:a|bad>>"],
            ["Better"],
        ]
    )
    proc = StreamProcessor(fake, settings, on_debug=debug_lines.append)
    cb = Callbacks()

    await proc.run("hi there", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    plain = list(debug_lines)
    # user: line at start with the user's message
    assert any("user:" in line and "hi there" in line for line in plain)
    # text: lines for clean parser output
    assert any("text:" in line and "Hello" in line for line in plain)
    assert any("text:" in line and "Better" in line for line in plain)
    # No raw: lines
    assert not any("raw:" in line for line in plain)
    # Signal events still present
    assert any("checkpoint:" in line and "a" in line for line in plain)
    assert any("BACKTRACK:" in line for line in plain)
    assert any("done:" in line for line in plain)


# --- First checkpoint on retry ---


@pytest.mark.asyncio
async def test_first_checkpoint_allowed_on_retry():
    """Checkpoint at the start of a retry branch should be accepted."""
    settings = Settings(min_tokens_between_signals=10)
    fake = FakeInference(
        [
            # First attempt: checkpoint, enough text, then backtrack
            [
                "<<checkpoint:a>>",
                "enough text here",
                "<<backtrack:a|bad>>",
            ],
            # Retry: immediate checkpoint should be accepted, then enough text, then backtrack
            [
                "<<checkpoint:b>>",
                "enough text here",
                "<<backtrack:b|also bad>>",
            ],
            # Final retry
            ["done"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # Both backtracks should succeed — checkpoint 'b' was accepted on retry
    assert len(cb.backtracks) == 2
    assert cb.backtracks[0][0].checkpoint_id == "a"
    assert cb.backtracks[1][0].checkpoint_id == "b"


# --- Temperature override ---


@pytest.mark.asyncio
async def test_backtrack_with_temperature_override(settings):
    """Temperature in backtrack signal should be passed to inference."""
    temps_used = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced", temperature=None):
            temps_used.append(temperature)
            async for t in super().stream(messages, system, mode, temperature):
                yield t

    fake = SpyInference(
        [
            ["<<checkpoint:a>>", "text", "<<backtrack:a|reason|temp:0.7>>"],
            ["fixed"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # First call: no override, second call: 0.7
    assert temps_used == [None, 0.7]


@pytest.mark.asyncio
async def test_backtrack_with_temp_out_of_range(settings):
    """Temperature outside [0.0, 1.0] should be discarded."""
    temps_used = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced", temperature=None):
            temps_used.append(temperature)
            async for t in super().stream(messages, system, mode, temperature):
                yield t

    fake = SpyInference(
        [
            ["<<checkpoint:a>>", "text", "<<backtrack:a|reason|temp:1.5>>"],
            ["fixed"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # Both calls should have None (1.5 discarded)
    assert temps_used == [None, None]


@pytest.mark.asyncio
async def test_backtrack_with_temp_and_mode(settings):
    """When both temp and mode specified, temp wins at inference layer."""
    temps_used = []
    modes_used = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced", temperature=None):
            temps_used.append(temperature)
            modes_used.append(mode)
            async for t in super().stream(messages, system, mode, temperature):
                yield t

    fake = SpyInference(
        [
            ["<<checkpoint:a>>", "text", "<<backtrack:a|reason|mode:precise|temp:0.8>>"],
            ["fixed"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # Mode changes to precise, but temp override of 0.8 is passed
    assert modes_used == ["balanced", "precise"]
    assert temps_used == [None, 0.8]


@pytest.mark.asyncio
async def test_system_prompt_includes_temperature(settings):
    """System prompt should contain current temperature and mode state."""
    systems_seen = []

    class SpyInference(FakeInference):
        async def stream(self, messages, system, mode="balanced", temperature=None):
            systems_seen.append(system)
            async for t in super().stream(messages, system, mode, temperature):
                yield t

    fake = SpyInference(
        [
            ["<<checkpoint:a>>", "text", "<<backtrack:a|reason|temp:0.7>>"],
            ["fixed"],
        ]
    )
    proc = StreamProcessor(fake, settings)
    cb = Callbacks()

    await proc.run("test", cb.on_text, cb.on_backtrack, cb.on_error, cb.on_done)

    # First call: default temperature 0.6 (balanced)
    assert "Temperature: 0.6 (balanced)" in systems_seen[0]
    # Retry: temperature 0.7 (still balanced mode)
    assert "Temperature: 0.7 (balanced)" in systems_seen[1]
    # Both should list available modes
    assert "Available modes:" in systems_seen[0]
    assert "Available modes:" in systems_seen[1]
