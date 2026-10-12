"""In-memory execution-state registry for remote Async Agent jobs.

The registry is owned by one ``LiveTaskRuntime``.  Records therefore do not
carry a Live ``session_id``: their identity is the Runtime task key
``(run_id, task_id)``.  This V1 implementation is intentionally non-persistent
and cannot recover remote jobs after a process restart.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone


_ACTIVE_STATES = frozenset({"queued", "running"})
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
_REMOTE_STATES = _ACTIVE_STATES | _TERMINAL_STATES


class RemoteJobRegistryError(RuntimeError):
    """Remote-job state is missing, stale or violates the lifecycle contract."""


class StaleRemoteJobAttemptError(RemoteJobRegistryError):
    """An old executor attempt tried to mutate the current remote job."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RemoteJobRegistryError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _attempt(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RemoteJobRegistryError("attempt must be a non-negative integer.")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RemoteJobRegistryError(f"{field_name} must be a timezone-aware datetime.")
    return value


def _optional_timestamp(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field_name)


def _state(value: object, *, terminal: bool | None = None) -> str:
    state = _text(value, "normalized_remote_state").lower()
    allowed = _REMOTE_STATES
    if terminal is True:
        allowed = _TERMINAL_STATES
    elif terminal is False:
        allowed = _ACTIVE_STATES
    if state not in allowed:
        expected = ", ".join(sorted(allowed))
        raise RemoteJobRegistryError(
            f"normalized_remote_state must be one of: {expected}."
        )
    return state


@dataclass(frozen=True, slots=True)
class RemoteJobRecord:
    """Latest known execution state for one Runtime task's remote job."""

    run_id: str
    task_id: str
    attempt: int
    manifest_id: str
    job_id: str
    normalized_remote_state: str
    last_remote_state: str
    submitted_at: datetime
    last_poll_at: datetime | None = None
    terminal_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    cancel_error: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "task_id", "manifest_id", "job_id", "last_remote_state"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "attempt", _attempt(self.attempt))
        state = _state(self.normalized_remote_state)
        object.__setattr__(self, "normalized_remote_state", state)
        object.__setattr__(self, "submitted_at", _timestamp(self.submitted_at, "submitted_at"))
        for name in ("last_poll_at", "terminal_at", "cancel_requested_at"):
            object.__setattr__(
                self,
                name,
                _optional_timestamp(getattr(self, name), name),
            )
        if state in _TERMINAL_STATES and self.terminal_at is None:
            raise RemoteJobRegistryError("terminal remote state requires terminal_at.")
        if state in _ACTIVE_STATES and self.terminal_at is not None:
            raise RemoteJobRegistryError("active remote state must not have terminal_at.")
        if self.cancel_error is not None:
            object.__setattr__(self, "cancel_error", _text(self.cancel_error, "cancel_error"))
            if self.cancel_requested_at is None:
                raise RemoteJobRegistryError(
                    "cancel_error requires cancel_requested_at."
                )

    @property
    def key(self) -> tuple[str, str]:
        return self.run_id, self.task_id

    @property
    def terminal(self) -> bool:
        return self.normalized_remote_state in _TERMINAL_STATES


class RemoteJobRegistry(ABC):
    """Storage-independent async interface for remote-job execution state."""

    @abstractmethod
    async def register(self, record: RemoteJobRecord) -> RemoteJobRecord:
        """Register a first job or atomically replace it with a newer attempt."""

    @abstractmethod
    async def get(self, run_id: str, task_id: str) -> RemoteJobRecord | None:
        """Return the current record for one Runtime task, if it exists."""

    @abstractmethod
    async def update_state(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
        normalized_remote_state: str,
        remote_state: str,
    ) -> RemoteJobRecord:
        """Store one non-terminal poll response for the current attempt."""

    @abstractmethod
    async def mark_terminal(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
        normalized_remote_state: str,
        remote_state: str,
    ) -> RemoteJobRecord:
        """Store one terminal poll response for the current attempt."""

    @abstractmethod
    async def mark_cancel_requested(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
    ) -> RemoteJobRecord:
        """Record that best-effort remote cancellation has been requested."""

    @abstractmethod
    async def record_cancel_error(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
        error: str,
    ) -> RemoteJobRecord:
        """Retain a cancellation error without changing the remote state."""

    @abstractmethod
    async def clear(self) -> None:
        """Discard all records owned by this LiveTaskRuntime."""


class InMemoryRemoteJobRegistry(RemoteJobRegistry):
    """Async-lock-protected, per-LiveTaskRuntime V1 registry."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._records: dict[tuple[str, str], RemoteJobRecord] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def register(self, record: RemoteJobRecord) -> RemoteJobRecord:
        if not isinstance(record, RemoteJobRecord):
            raise RemoteJobRegistryError("record must be a RemoteJobRecord.")
        async with self._lock:
            current = self._records.get(record.key)
            if current is None or record.attempt > current.attempt:
                self._records[record.key] = record
                return record
            if record.attempt < current.attempt:
                raise StaleRemoteJobAttemptError(
                    self._stale_message(record.key, record.attempt, current.attempt)
                )
            if record == current:
                return current
            raise RemoteJobRegistryError(
                "remote job is already registered for "
                f"run_id='{record.run_id}', task_id='{record.task_id}', "
                f"attempt={record.attempt}."
            )

    async def get(self, run_id: str, task_id: str) -> RemoteJobRecord | None:
        key = _text(run_id, "run_id"), _text(task_id, "task_id")
        async with self._lock:
            return self._records.get(key)

    async def update_state(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
        normalized_remote_state: str,
        remote_state: str,
    ) -> RemoteJobRecord:
        state = _state(normalized_remote_state, terminal=False)
        raw_state = _text(remote_state, "remote_state")
        async with self._lock:
            current = self._current(run_id, task_id, attempt)
            if current.terminal:
                raise RemoteJobRegistryError("terminal remote job cannot return to an active state.")
            updated = replace(
                current,
                normalized_remote_state=state,
                last_remote_state=raw_state,
                last_poll_at=self._now(),
            )
            self._records[current.key] = updated
            return updated

    async def mark_terminal(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
        normalized_remote_state: str,
        remote_state: str,
    ) -> RemoteJobRecord:
        state = _state(normalized_remote_state, terminal=True)
        raw_state = _text(remote_state, "remote_state")
        async with self._lock:
            current = self._current(run_id, task_id, attempt)
            if current.terminal:
                if (
                    current.normalized_remote_state == state
                    and current.last_remote_state == raw_state
                ):
                    return current
                raise RemoteJobRegistryError("terminal remote job state cannot be changed.")
            timestamp = self._now()
            updated = replace(
                current,
                normalized_remote_state=state,
                last_remote_state=raw_state,
                last_poll_at=timestamp,
                terminal_at=timestamp,
            )
            self._records[current.key] = updated
            return updated

    async def mark_cancel_requested(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
    ) -> RemoteJobRecord:
        async with self._lock:
            current = self._current(run_id, task_id, attempt)
            if current.cancel_requested_at is not None:
                return current
            updated = replace(current, cancel_requested_at=self._now())
            self._records[current.key] = updated
            return updated

    async def record_cancel_error(
        self,
        run_id: str,
        task_id: str,
        attempt: int,
        error: str,
    ) -> RemoteJobRecord:
        detail = _text(error, "error")
        async with self._lock:
            current = self._current(run_id, task_id, attempt)
            if current.cancel_requested_at is None:
                raise RemoteJobRegistryError(
                    "cancel must be requested before recording a cancel error."
                )
            updated = replace(current, cancel_error=detail)
            self._records[current.key] = updated
            return updated

    async def clear(self) -> None:
        async with self._lock:
            self._records.clear()

    def _current(self, run_id: str, task_id: str, attempt: int) -> RemoteJobRecord:
        key = _text(run_id, "run_id"), _text(task_id, "task_id")
        safe_attempt = _attempt(attempt)
        try:
            current = self._records[key]
        except KeyError as exc:
            raise RemoteJobRegistryError(
                f"remote job is not registered for run_id='{key[0]}', task_id='{key[1]}'."
            ) from exc
        if current.attempt != safe_attempt:
            raise StaleRemoteJobAttemptError(
                self._stale_message(key, safe_attempt, current.attempt)
            )
        return current

    def _now(self) -> datetime:
        return _timestamp(self._clock(), "clock result")

    @staticmethod
    def _stale_message(
        key: tuple[str, str],
        attempted: int,
        current: int,
    ) -> str:
        return (
            "stale remote job attempt for "
            f"run_id='{key[0]}', task_id='{key[1]}': "
            f"received {attempted}, current {current}."
        )
