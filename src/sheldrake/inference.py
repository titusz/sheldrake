"""Anthropic SDK wrapper with async streaming and cancel support."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from sheldrake.config import Settings

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic, AsyncMessageStream


class InferenceManager:
    """Manages Anthropic API streaming with cancel/restart support."""

    def __init__(self, client: AsyncAnthropic, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._active_stream: AsyncMessageStream | None = None

    async def stream(
        self,
        messages: list[dict],
        system: str,
        mode: str = "balanced",
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Start streaming inference. Yields text deltas."""
        params = self.settings.modes[mode]
        effective_temp = temperature if temperature is not None else params["temperature"]
        kwargs: dict = {
            "model": self.settings.model,
            "max_tokens": 4096,
            "system": system,
            "messages": messages,
            "temperature": effective_temp,
        }
        if "top_p" in params:
            kwargs["top_p"] = params["top_p"]
        manager = self.client.messages.stream(**kwargs)
        async with manager as stream:
            self._active_stream = stream
            async for text in stream.text_stream:
                yield text

    async def cancel(self) -> None:
        """Cancel active stream."""
        if self._active_stream is not None:
            await self._active_stream.close()
            self._active_stream = None
