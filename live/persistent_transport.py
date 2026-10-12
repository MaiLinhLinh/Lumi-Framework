"""One Gemini Live connection and receiver task per application session.

This module is intentionally domain-agnostic. It owns the connection lifecycle
and moves raw Gemini messages into a queue for shared orchestration.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from google.genai import types


class PersistentLiveTransportError(RuntimeError):
    """Raised when a persistent transport is unavailable or has closed."""


_CLOSED = object()


class PersistentLiveTransport:
    """Own a single Live connection and its single background receive task."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._context_manager: Any | None = None
        self._session: Any | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._messages: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = True

    @property
    def connected(self) -> bool:
        return self._session is not None and not self._closed

    async def connect(self, context_manager: Any) -> None:
        """Open the Gemini context once and start exactly one receive task."""

        if self.connected:
            return
        # A remote Gemini close ends the receiver first.  Release that stale
        # context before replacing it, then start the new receiver with an
        # empty queue so an old ``_CLOSED`` sentinel cannot end the new turn.
        if self._context_manager is not None or self._session is not None:
            await self.close()
        self._messages = asyncio.Queue()
        self._context_manager = context_manager
        self._session = await context_manager.__aenter__()
        self._closed = False
        self._receive_task = asyncio.create_task(
            self._receive_loop(), name=f"gemini-live-receive:{self.session_id}"
        )

    async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
        session = self._require_session()
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=turn_complete,
        )

    async def send_audio(self, pcm: bytes, *, sample_rate_hz: int = 16_000) -> None:
        session = self._require_session()
        await session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={sample_rate_hz}")
        )

    async def end_audio(self) -> None:
        await self._require_session().send_realtime_input(audio_stream_end=True)

    async def send_tool_responses(self, function_responses: list[types.FunctionResponse]) -> None:
        await self._require_session().send_tool_response(function_responses=function_responses)

    async def receive(self) -> AsyncIterator[Any]:
        """Yield raw Gemini messages collected by the sole receive task."""

        while True:
            item = await self._messages.get()
            if item is _CLOSED:
                return
            if isinstance(item, BaseException):
                raise PersistentLiveTransportError(str(item)) from item
            yield item

    async def close(self) -> None:
        """Cancel receive first, then release the Gemini async context exactly once."""

        if self._context_manager is None and self._session is None and self._receive_task is None:
            return
        self._closed = True
        receive_task, self._receive_task = self._receive_task, None
        if receive_task is not None and receive_task is not asyncio.current_task():
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive_task
        context_manager, self._context_manager = self._context_manager, None
        self._session = None
        if context_manager is not None:
            await context_manager.__aexit__(None, None, None)
        await self._messages.put(_CLOSED)

    def discard_pending_messages(self) -> int:
        """Discard stale Gemini events after a browser reconnect.

        No surface context is removed: verified context lives in SessionMemoryStore.
        This only prevents old PCM/transcript events from a disconnected page
        being delivered to the newly attached browser.
        """

        discarded = 0
        while True:
            try:
                self._messages.get_nowait()
                discarded += 1
            except asyncio.QueueEmpty:
                return discarded

    def _require_session(self) -> Any:
        if not self.connected:
            raise PersistentLiveTransportError("Gemini Live persistent transport is not connected.")
        return self._session

    async def _receive_loop(self) -> None:
        try:
            session = self._require_session()
            # google-genai may end one ``receive()`` iterator at a model turn
            # boundary while keeping the underlying Live socket open.  Keep the
            # sole reader alive and obtain the next iterator instead of closing
            # the persistent application session after its first turn.
            while not self._closed:
                received_any = False
                async for message in session.receive():
                    received_any = True
                    await self._messages.put(message)
                if not received_any:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # The orchestrator decides recovery in CP-05.
            await self._messages.put(exc)
        finally:
            # Do not leave a remote-closed socket looking reusable.  The next
            # microphone turn can then establish a fresh Gemini session and
            # rehydrate its server-owned memory/context.
            self._closed = True
            await self._messages.put(_CLOSED)


class PersistentLiveTransportStore:
    """Map application ``session_id`` to one reusable Gemini transport."""

    def __init__(self) -> None:
        self._transports: dict[str, PersistentLiveTransport] = {}

    def get(self, session_id: str) -> PersistentLiveTransport:
        return self._transports.setdefault(session_id, PersistentLiveTransport(session_id))

    async def close(self, session_id: str) -> None:
        transport = self._transports.pop(session_id, None)
        if transport is not None:
            await transport.close()

    async def close_all(self) -> None:
        transports, self._transports = list(self._transports.values()), {}
        for transport in transports:
            await transport.close()
