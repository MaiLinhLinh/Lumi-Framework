"""CP3 tests for per-LiveTaskRuntime remote-job execution state."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from final_framework.async_tasks import (
    InMemoryRemoteJobRegistry,
    RemoteJobRecord,
    RemoteJobRegistryError,
    StaleRemoteJobAttemptError,
)


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current


def _record(
    *,
    run_id: str = "run_1",
    task_id: str = "task_1",
    attempt: int = 0,
    job_id: str = "job_1",
) -> RemoteJobRecord:
    return RemoteJobRecord(
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        manifest_id="search_image_task",
        job_id=job_id,
        normalized_remote_state="queued",
        last_remote_state="accepted",
        submitted_at=datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc),
    )


class RemoteJobRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_get_update_and_terminal_state(self) -> None:
        registry = InMemoryRemoteJobRegistry(clock=_Clock())
        original = _record()

        self.assertEqual(await registry.register(original), original)
        self.assertEqual(await registry.get("run_1", "task_1"), original)

        running = await registry.update_state(
            "run_1", "task_1", 0, "running", "processing"
        )
        self.assertEqual(running.normalized_remote_state, "running")
        self.assertEqual(running.last_remote_state, "processing")
        self.assertIsNotNone(running.last_poll_at)
        self.assertIsNone(running.terminal_at)

        completed = await registry.mark_terminal(
            "run_1", "task_1", 0, "succeeded", "completed"
        )
        self.assertTrue(completed.terminal)
        self.assertEqual(completed.normalized_remote_state, "succeeded")
        self.assertEqual(completed.last_remote_state, "completed")
        self.assertEqual(completed.last_poll_at, completed.terminal_at)
        self.assertEqual(await registry.get("run_1", "task_1"), completed)

        with self.assertRaisesRegex(RemoteJobRegistryError, "cannot return"):
            await registry.update_state(
                "run_1", "task_1", 0, "running", "processing-again"
            )

    async def test_concurrent_updates_leave_one_complete_record(self) -> None:
        registry = InMemoryRemoteJobRegistry(clock=_Clock())
        await registry.register(_record())
        raw_states = {f"processing-{index}" for index in range(50)}

        await asyncio.gather(*(
            registry.update_state("run_1", "task_1", 0, "running", raw_state)
            for raw_state in raw_states
        ))

        current = await registry.get("run_1", "task_1")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.normalized_remote_state, "running")
        self.assertIn(current.last_remote_state, raw_states)
        self.assertIsNotNone(current.last_poll_at)
        self.assertEqual(current.job_id, "job_1")
        self.assertEqual(current.attempt, 0)

    async def test_new_retry_replaces_record_and_rejects_old_attempt(self) -> None:
        registry = InMemoryRemoteJobRegistry(clock=_Clock())
        await registry.register(_record(attempt=0, job_id="job_old"))
        retry = _record(attempt=1, job_id="job_retry")

        await registry.register(retry)

        with self.assertRaisesRegex(StaleRemoteJobAttemptError, "received 0, current 1"):
            await registry.update_state(
                "run_1", "task_1", 0, "running", "late-old-response"
            )
        with self.assertRaisesRegex(StaleRemoteJobAttemptError, "received 0, current 1"):
            await registry.register(_record(attempt=0, job_id="another-old-job"))

        self.assertEqual(await registry.get("run_1", "task_1"), retry)

    async def test_cancel_metadata_preserves_last_remote_state(self) -> None:
        registry = InMemoryRemoteJobRegistry(clock=_Clock())
        await registry.register(_record())
        running = await registry.update_state(
            "run_1", "task_1", 0, "running", "processing"
        )

        cancel_requested = await registry.mark_cancel_requested(
            "run_1", "task_1", 0
        )
        failed_cancel = await registry.record_cancel_error(
            "run_1", "task_1", 0, "remote cancel timed out"
        )

        self.assertIsNotNone(cancel_requested.cancel_requested_at)
        self.assertEqual(failed_cancel.cancel_requested_at, cancel_requested.cancel_requested_at)
        self.assertEqual(failed_cancel.cancel_error, "remote cancel timed out")
        self.assertEqual(
            failed_cancel.normalized_remote_state,
            running.normalized_remote_state,
        )
        self.assertEqual(failed_cancel.last_remote_state, running.last_remote_state)

    async def test_cancel_error_requires_cancel_request(self) -> None:
        registry = InMemoryRemoteJobRegistry()
        await registry.register(_record())

        with self.assertRaisesRegex(RemoteJobRegistryError, "must be requested"):
            await registry.record_cancel_error(
                "run_1", "task_1", 0, "remote cancel failed"
            )

    async def test_clear_is_scoped_to_the_registry_instance(self) -> None:
        first_session_registry = InMemoryRemoteJobRegistry()
        second_session_registry = InMemoryRemoteJobRegistry()
        first = _record(job_id="job_first")
        second = _record(job_id="job_second")
        await first_session_registry.register(first)
        await second_session_registry.register(second)

        await first_session_registry.clear()

        self.assertIsNone(await first_session_registry.get("run_1", "task_1"))
        self.assertEqual(
            await second_session_registry.get("run_1", "task_1"),
            second,
        )

    async def test_unknown_job_and_invalid_record_are_rejected(self) -> None:
        registry = InMemoryRemoteJobRegistry()
        self.assertIsNone(await registry.get("missing_run", "missing_task"))
        with self.assertRaisesRegex(RemoteJobRegistryError, "not registered"):
            await registry.update_state(
                "missing_run", "missing_task", 0, "running", "processing"
            )
        with self.assertRaisesRegex(RemoteJobRegistryError, "timezone-aware"):
            RemoteJobRecord(
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                manifest_id="search_image_task",
                job_id="job_1",
                normalized_remote_state="queued",
                last_remote_state="accepted",
                submitted_at=datetime(2026, 10, 7, 3, 0),
            )


if __name__ == "__main__":
    unittest.main()
