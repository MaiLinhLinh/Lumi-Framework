"""CP10 tests for domainless panel interaction and task-event coverage."""

from __future__ import annotations

import asyncio
import itertools
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from final_framework.tests.plan_fixtures import make_surface_plan, submit_plan
from final_framework.artifacts import ArtifactStore
from final_framework.catalogs.resources import SharedResourceRegistry
from final_framework.executors import (
    COMPLETE_SURFACE_TASK,
    SEARCH_INFORMATION_TASK,
    build_default_executor_registry,
)
from final_framework.extension_loader import ExtensionLoader
from final_framework.live.surface_runtime import SurfaceRuntime
from final_framework.live.gemini_session import (
    PersistentGeminiLiveConversation,
    _QueuedTaskMessage,
)
from final_framework.live.session_protocol import LiveSessionState
from final_framework.panel.contracts import AnchorBinding, ComponentChild, ComponentNode, GridRect
from final_framework.settings import Settings
from final_framework.surface.contracts import CompiledSurface
from final_framework.task_manager import (
    Artifact,
    TaskEvent,
    TaskManager,
    TaskResult,
)


ROOT = Path(__file__).resolve().parents[1]


class Cp10LiveRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extensions = ExtensionLoader(ROOT / "extensions").load()
        self.assets = SharedResourceRegistry(ROOT / "resources").load().assets

    def test_choice_interaction_resolves_against_domainless_compiled_surface(self) -> None:
        choice = self.extensions.widget_registry.get("choice")
        compiled = CompiledSurface(
            surface_id="panel_1",
            revision=1,
            components=(ComponentNode(
                id="1",
                type="choice",
                layout=GridRect(1, 1, 8, 3),
                props={},
                state=choice.materialize_initial_state({"visibility": "visible"}),
                children=(ComponentChild(type="text", props={"content": "Đáp án A"}),),
            ),),
            anchors=(AnchorBinding("a", "1", "choice"),),
        )
        runtime = SurfaceRuntime(
            widget_registry=self.extensions.widget_registry,
            asset_catalog=self.assets,
            effect_registry=self.extensions.effect_registry,
            asset_urls={},
        )
        payload = runtime.install_surface(session_id="s1", compiled=compiled)
        self.assertEqual(payload["surface"]["surface_id"], "panel_1")

        result = runtime.apply_panel_interaction(
            session_id="s1", surface_id="panel_1", revision=1, anchor_id="a", action="select"
        )
        # The copied choice contract declares `select` as allowed, but does
        # not declare a state transition.  The domainless runtime must keep
        # that contract: resolve the interaction without a panel mutation.
        self.assertEqual(result.interaction["revision"], 1)
        self.assertIsNone(result.panel_update)
        self.assertEqual(runtime.active_surface_context("s1")["revision"], 1)

    def test_install_surface_does_not_commit_when_presentation_preflight_fails(self) -> None:
        text = self.extensions.widget_registry.get("text")
        original = CompiledSurface(
            surface_id="panel_original",
            revision=1,
            components=(ComponentNode(
                id="1",
                type="text",
                layout=GridRect(1, 1, 16, 1),
                props={"content": "Original", "role": "body"},
                state=text.materialize_initial_state({"visibility": "visible"}),
            ),),
            anchors=(AnchorBinding("a1", "1", "text"),),
        )
        replacement = CompiledSurface(
            surface_id="panel_replacement",
            revision=1,
            components=(ComponentNode(
                id="1",
                type="text",
                layout=GridRect(1, 1, 16, 1),
                props={"content": "Replacement", "role": "body"},
                state=text.materialize_initial_state({"visibility": "visible"}),
            ),),
            anchors=(AnchorBinding("a1", "1", "text"),),
        )
        runtime = SurfaceRuntime(
            widget_registry=self.extensions.widget_registry,
            asset_catalog=self.assets,
            effect_registry=self.extensions.effect_registry,
            asset_urls={},
        )
        runtime.install_surface(session_id="s1", compiled=original)

        with patch.object(runtime, "_stage_map", side_effect=ValueError("stage map failed")):
            with self.assertRaisesRegex(ValueError, "stage map failed"):
                runtime.install_surface(session_id="s1", compiled=replacement)

        active = runtime.active_surface_for_task("s1")
        self.assertIsNotNone(active)
        self.assertEqual(active["surface_id"], "panel_original")

    def test_event_history_covers_accepted_started_success_failed_and_complete_ready(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            count = itertools.count(1)

            async def executor(context):
                task = context.task
                if task.task_type == SEARCH_INFORMATION_TASK:
                    raise RuntimeError("expected failure")
                return TaskResult(
                    task_id=task.task_id,
                    status="succeeded",
                    artifact=Artifact(
                        artifact_id=f"artifact_{task.task_id}",
                        artifact_type="knowledge" if task.task_type == SEARCH_INFORMATION_TASK else "complete_surface",
                        run_id=task.run_id,
                        produced_by=task.task_id,
                        evidence_ref=task.evidence_ref,
                        data={"items": []},
                    ),
                    evidence_ref=task.evidence_ref,
                )

            for task_type in (SEARCH_INFORMATION_TASK, COMPLETE_SURFACE_TASK):
                registry.bind(task_type, executor)
            manager = TaskManager(
                registry,
                ArtifactStore(),
                id_factory=lambda prefix: f"{prefix}_{next(count)}",
            )
            plan = make_surface_plan((("#E1", SEARCH_INFORMATION_TASK, "Tìm nội dung."),))
            snapshot = await submit_plan(manager, plan)
            await manager.wait_for_run(snapshot.run_id)
            return {event.event for event in manager.events(snapshot.run_id)}

        events = asyncio.run(scenario())
        self.assertTrue({
            "TASK_ACCEPTED", "TASK_STARTED", "TASK_SUCCEEDED", "TASK_FAILED",
            "SURFACE_DRAFT_READY", "COMPLETE_SURFACE_READY",
        }.issubset(events))

    def test_task_event_is_forwarded_to_gemini_when_session_is_listening(self) -> None:
        async def scenario():
            runtime = SurfaceRuntime(
                widget_registry=self.extensions.widget_registry,
                asset_catalog=self.assets,
                effect_registry=self.extensions.effect_registry,
                asset_urls={},
            )
            runtime.transition_session(session_id="s1", target=LiveSessionState.LISTENING)
            browser_events: list[dict] = []
            gemini_messages: list[str] = []

            class _Transport:
                async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
                    self.sent = (text, turn_complete)
                    gemini_messages.append(text)

            async def on_event(event: dict) -> None:
                browser_events.append(event)

            async def on_audio(*_args) -> None:
                return None

            def task_runtime_factory(*_args):
                return SimpleNamespace(manager=SimpleNamespace())

            settings = Settings(
                gemini_live_api_key="key",
                gemini_live_model="model",
                gemini_live_voice="voice",
                live_turn_timeout_seconds=45,
                live_idle_timeout_seconds=900,
                live_reconnect_grace_seconds=30,
                presentation_animation_delay_ms=0,
            )
            conversation = PersistentGeminiLiveConversation(
                session_id="s1",
                settings=settings,
                transport=_Transport(),
                surface_runtime=runtime,
                task_runtime_factory=task_runtime_factory,
                on_event=on_event,
                on_audio=on_audio,
            )
            await conversation._receive_task_event(TaskEvent(
                event="TASK_SUCCEEDED",
                run_id="run_1",
                task_id="task_1",
                task_type="search_information_task",
                status="SUCCEEDED",
                summary="knowledge artifact ready",
                evidence_ref="#E1",
                artifact_id="artifact_1",
            ))
            await conversation._receive_task_event(TaskEvent(
                event="TASK_STARTED",
                run_id="run_1",
                task_id="task_2",
                task_type="search_image_task",
                status="RUNNING",
                summary="media retrieval started",
                evidence_ref="#E2",
            ))
            await asyncio.sleep(0.3)
            return browser_events, gemini_messages

        browser_events, gemini_messages = asyncio.run(scenario())
        self.assertEqual(browser_events[0]["event"], "task_event")
        self.assertIn('"evidence_ref":"#E1"', browser_events[0]["content"])
        self.assertEqual(browser_events[1]["event"], "task_event")
        self.assertEqual(len(gemini_messages), 1)
        self.assertTrue(gemini_messages[0].startswith("TASK_EVENT\n"))
        self.assertIn('"task_type":"search_information_task"', gemini_messages[0])
        self.assertIn('"task_type":"search_image_task"', gemini_messages[0])

    def test_final_browser_confirmation_discards_stale_run_progress(self) -> None:
        async def scenario():
            runtime = SurfaceRuntime(
                widget_registry=self.extensions.widget_registry,
                asset_catalog=self.assets,
                effect_registry=self.extensions.effect_registry,
                asset_urls={},
            )
            runtime.transition_session(session_id="s1", target=LiveSessionState.LISTENING)
            gemini_messages: list[str] = []

            class _Transport:
                async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
                    gemini_messages.append(text)

            async def on_event(_event: dict) -> None:
                return None

            async def on_audio(*_args) -> None:
                return None

            def task_runtime_factory(*_args):
                return SimpleNamespace(manager=SimpleNamespace())

            settings = Settings(
                gemini_live_api_key="key",
                gemini_live_model="model",
                gemini_live_voice="voice",
                live_turn_timeout_seconds=45,
                live_idle_timeout_seconds=900,
                live_reconnect_grace_seconds=30,
                presentation_animation_delay_ms=0,
            )
            conversation = PersistentGeminiLiveConversation(
                session_id="s1",
                settings=settings,
                transport=_Transport(),
                surface_runtime=runtime,
                task_runtime_factory=task_runtime_factory,
                on_event=on_event,
                on_audio=on_audio,
            )
            conversation._active_task_run_id = "run_1"
            conversation._pending_task_messages.extend((
                _QueuedTaskMessage("TASK_EVENT old progress", run_id="run_1"),
                _QueuedTaskMessage("TASK_EVENT another run", run_id="run_2"),
            ))
            conversation._pending_final_surface = (
                "panel-final",
                2,
                "run_1",
                {
                    "surface_id": "panel-final",
                    "revision": 2,
                    "visual_stage_map": "FINAL MAP",
                    "visual_effects": [{"id": "highlight", "description": "Highlight."}],
                },
                {"surface_status": "ready", "missing_parts": []},
            )

            result = await conversation.confirm_surface_rendered(
                surface_id="panel-final", revision=2
            )
            await asyncio.sleep(0)
            return result, gemini_messages, tuple(conversation._pending_task_messages)

        result, gemini_messages, queued = asyncio.run(scenario())
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["discarded_progress_messages"], 1)
        self.assertTrue(gemini_messages[0].startswith("SURFACE_READY"))
        self.assertIn("FINAL MAP", gemini_messages[0])
        self.assertEqual(tuple(message.run_id for message in queued), ("run_2",))

    def test_partial_surface_confirmation_tells_gemini_what_is_missing(self) -> None:
        async def scenario():
            runtime = SurfaceRuntime(
                widget_registry=self.extensions.widget_registry,
                asset_catalog=self.assets,
                effect_registry=self.extensions.effect_registry,
                asset_urls={},
            )
            runtime.transition_session(session_id="s1", target=LiveSessionState.LISTENING)
            gemini_messages: list[str] = []

            class _Transport:
                async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
                    gemini_messages.append(text)

            async def no_event(_event: dict) -> None:
                return None

            conversation = PersistentGeminiLiveConversation(
                session_id="s1",
                settings=Settings(
                    gemini_live_api_key="key",
                    gemini_live_model="model",
                    gemini_live_voice="voice",
                    live_turn_timeout_seconds=45,
                    live_idle_timeout_seconds=900,
                    live_reconnect_grace_seconds=30,
                    presentation_animation_delay_ms=0,
                ),
                transport=_Transport(),
                surface_runtime=runtime,
                task_runtime_factory=lambda *_: SimpleNamespace(manager=SimpleNamespace()),
                on_event=no_event,
                on_audio=no_event,
            )
            conversation._active_task_run_id = "run_partial"
            conversation._pending_final_surface = (
                "panel-partial",
                2,
                "run_partial",
                {
                    "surface_id": "panel-partial",
                    "revision": 2,
                    "visual_stage_map": "PARTIAL MAP",
                    "visual_effects": [],
                },
                {
                    "surface_status": "partial",
                    "missing_parts": [{
                        "description": "Nhóm 3 quả táo dùng trong phép cộng.",
                        "reason": "CONTENT_NOT_RENDERED",
                    }],
                },
            )
            await conversation.confirm_surface_rendered(
                surface_id="panel-partial", revision=2
            )
            await asyncio.sleep(0)
            return gemini_messages

        messages = asyncio.run(scenario())
        self.assertTrue(messages[0].startswith("SURFACE_PARTIAL"))
        self.assertIn("Nhóm 3 quả táo dùng trong phép cộng.", messages[0])
        self.assertIn("PARTIAL MAP", messages[0])


if __name__ == "__main__":
    unittest.main()
