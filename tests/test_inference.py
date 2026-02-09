"""Tests for the inference manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pali.config import Settings
from pali.inference import InferenceManager


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def mock_client():
    return MagicMock()


def test_mode_parameters_precise(settings):
    """Verify precise mode maps to low temperature."""
    assert settings.modes["precise"]["temperature"] == 0.2


def test_mode_parameters_exploratory(settings):
    """Verify exploratory mode maps to high temperature."""
    assert settings.modes["exploratory"]["temperature"] == 0.9


def test_mode_parameters_balanced(settings):
    """Verify balanced mode is the default middle ground."""
    assert settings.modes["balanced"]["temperature"] == 0.5


@pytest.mark.asyncio
async def test_cancel_awaits_close(settings, mock_client):
    """cancel() should await close() on the active stream."""
    manager = InferenceManager(mock_client, settings)
    mock_stream = AsyncMock()
    manager._active_stream = mock_stream

    await manager.cancel()

    mock_stream.close.assert_awaited_once()
    assert manager._active_stream is None


@pytest.mark.asyncio
async def test_cancel_noop_when_no_stream(settings, mock_client):
    """cancel() should be safe to call with no active stream."""
    manager = InferenceManager(mock_client, settings)
    await manager.cancel()  # should not raise


@pytest.mark.asyncio
async def test_stream_yields_text_deltas(settings, mock_client):
    """stream() should yield text deltas from the API."""
    # Create a mock async context manager for the stream
    mock_text_stream = AsyncIteratorMock(["Hello", " world", "!"])
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_stream_ctx.text_stream = mock_text_stream

    mock_client.messages.stream.return_value = mock_stream_ctx

    manager = InferenceManager(mock_client, settings)
    collected = []
    async for text in manager.stream(
        messages=[{"role": "user", "content": "hi"}],
        system="test",
        mode="balanced",
    ):
        collected.append(text)

    assert collected == ["Hello", " world", "!"]
    # Verify API was called with correct parameters
    mock_client.messages.stream.assert_called_once()
    call_kwargs = mock_client.messages.stream.call_args.kwargs
    assert call_kwargs["temperature"] == 0.5
    assert "top_p" not in call_kwargs


class AsyncIteratorMock:
    """Helper to create an async iterator from a list."""

    def __init__(self, items: list):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None
