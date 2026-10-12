"""CP3 tests for the Gemini Live -> Task Manager Agent handoff."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from final_framework.live.gemini_session import PersistentGeminiLiveConversation
from final_framework.live.surface_runtime import SurfaceRuntime
from final_framework.registry_composition import load_framework_registries
from final_framework.settings import Settings
from final_framework.task_manager_agent import TaskManagerAgentError


ROOT = Path(__file__).resolve().parents[1]


class _TaskManagerAgent:
    def __init__(self) -> None:
        self.requests = []

    async def plan(self, request):
        self.requests.append(request)
        return SimpleNamespace(blocks=(object(),), tasks=(object(),))


class _TaskRuntime:
    def __init__(self) -> None:
        self.manager = SimpleNamespace(supersede_run=self._supersede_run)
        self.submissions = []
        self.presented = []
        self.superseded = []

    async def _supersede_run(self, run_id):
        self.superseded.append(run_id)

    def preflight_surface_plan(self, plan):
        return SimpleNamespace(plan=plan)

    async def submit_surface_plan(self, plan, *, active_surface, prepared):
        self.submissions.append((plan, active_surface, prepared))
        return SimpleNamespace(run_id="run_1")

    async def present_draft(self, run_id):
        self.presented.append(run_id)


class LiveTaskManagerRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registries = load_framework_registries(ROOT)

    def _planning_conversation(self, agent, events):
        runtime = SurfaceRuntime(
            widget_registry=self.registries.widget_registry,
            asset_catalog=self.registries.asset_catalog(),
            effect_registry=self.registries.effect_registry,
            asset_urls={},
        )

        async def on_event(event):
            events.append(event)

        async def on_audio(*_args):
            return None

        class Runtime(_TaskRuntime):
            async def present_draft(self, run_id):
                await super().present_draft(run_id)
                events.append({"type": "panel"})

        return PersistentGeminiLiveConversation(
            session_id="planning_test",
            settings=Settings(
                gemini_live_api_key="", gemini_live_model="test", gemini_live_voice="kore",
                live_turn_timeout_seconds=45, live_idle_timeout_seconds=900,
                live_reconnect_grace_seconds=30, presentation_animation_delay_ms=0,
            ),
            transport=object(), surface_runtime=runtime,
            task_runtime_factory=lambda *_args: Runtime(), task_manager_agent=agent,
            on_event=on_event, on_audio=on_audio,
        )

    def test_planning_spinner_starts_before_agent_and_stops_after_draft(self) -> None:
        events = []

        class Agent(_TaskManagerAgent):
            async def plan(self, request):
                events.append({"type": "agent_called"})
                return await super().plan(request)

        conversation = self._planning_conversation(Agent(), events)
        response = asyncio.run(conversation._request_task_manager({"request": "Tạo flashcard."}))
        self.assertEqual(response["status"], "planned")
        self.assertEqual(events, [
            {"type": "surface:planning", "status": "started"},
            {"type": "agent_called"}, {"type": "panel"},
            {"type": "surface:planning", "status": "stopped"},
        ])

    def test_rejected_plan_stops_spinner(self) -> None:
        events = []

        class Agent:
            async def plan(self, _request):
                raise TaskManagerAgentError("invalid plan")

        conversation = self._planning_conversation(Agent(), events)
        response = asyncio.run(conversation._request_task_manager({"request": "Tạo flashcard."}))
        self.assertEqual(response["status"], "rejected")
        self.assertEqual([event["status"] for event in events], ["started", "stopped"])

    def test_cancelled_planning_stops_spinner(self) -> None:
        async def scenario():
            events = []
            entered = asyncio.Event()

            class Agent:
                async def plan(self, _request):
                    entered.set()
                    await asyncio.Event().wait()

            conversation = self._planning_conversation(Agent(), events)
            task = asyncio.create_task(conversation._request_task_manager({"request": "Tạo flashcard."}))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return events

        events = asyncio.run(scenario())
        self.assertEqual([event["status"] for event in events], ["started", "stopped"])

    def test_invalid_request_does_not_start_spinner(self) -> None:
        events = []
        conversation = self._planning_conversation(_TaskManagerAgent(), events)
        response = asyncio.run(conversation._request_task_manager({"request": ""}))
        self.assertEqual(response["status"], "rejected")
        self.assertEqual(events, [])

    def test_draft_preflight_failure_keeps_previous_run_and_schedules_nothing(self) -> None:
        events = []

        class Runtime(_TaskRuntime):
            def preflight_surface_plan(self, _plan):
                raise ValueError("stage map preflight failed")

        runtime = Runtime()
        conversation = self._planning_conversation(_TaskManagerAgent(), events)
        conversation._task_runtime = runtime
        conversation._active_task_run_id = "run_previous"

        response = asyncio.run(conversation._request_task_manager({
            "request": "Táº¡o phÃ©p cá»™ng thÃº vá»‹.",
        }))

        self.assertEqual(response, {
            "status": "rejected",
            "message": "stage map preflight failed",
        })
        self.assertEqual(runtime.superseded, [])
        self.assertEqual(runtime.submissions, [])
        self.assertEqual(conversation._active_task_run_id, "run_previous")

    def test_complete_presentation_failure_is_reported_and_propagated(self) -> None:
        events = []
        conversation = self._planning_conversation(_TaskManagerAgent(), events)

        with patch.object(
            conversation._surface_runtime,
            "install_surface",
            side_effect=ValueError("stage map failed"),
        ):
            with self.assertRaisesRegex(ValueError, "stage map failed"):
                asyncio.run(conversation._receive_complete_presentation(
                    {"compiled": object(), "surface_completion": {}},
                    {},
                ))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "live:debug_trace")
        self.assertEqual(events[0]["event"], "complete_surface_dropped")
        self.assertEqual(events[0]["content"], "stage map failed")

    def test_request_task_manager_delegates_only_natural_language_request(self) -> None:
        async def scenario():
            surface_runtime = SurfaceRuntime(
                widget_registry=self.registries.widget_registry,
                asset_catalog=self.registries.asset_catalog(),
                effect_registry=self.registries.effect_registry,
                asset_urls={},
            )
            surface_runtime.remember_turn(
                session_id="session_1",
                user_text="Chào Lumi",
                assistant_text="Chào bạn",
            )
            agent = _TaskManagerAgent()
            task_runtime = _TaskRuntime()

            async def on_event(_event):
                return None

            async def on_audio(_pcm, _rate, _marker, _turn_id):
                return None

            conversation = PersistentGeminiLiveConversation(
                session_id="session_1",
                settings=Settings(
                    gemini_live_api_key="",
                    gemini_live_model="gemini-live-test",
                    gemini_live_voice="kore",
                    live_turn_timeout_seconds=45,
                    live_idle_timeout_seconds=900,
                    live_reconnect_grace_seconds=30,
                    presentation_animation_delay_ms=0,
                ),
                transport=object(),
                surface_runtime=surface_runtime,
                task_runtime_factory=lambda *_args: task_runtime,
                task_manager_agent=agent,
                on_event=on_event,
                on_audio=on_audio,
            )
            response = await conversation._request_task_manager({
                "request": "Tạo flashcard học tiếng Anh về con mèo cho trẻ em.",
            })
            return response, agent.requests, task_runtime

        response, requests, task_runtime = asyncio.run(scenario())

        self.assertEqual(response, {"status": "planned", "run_id": "run_1"})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].request, "Tạo flashcard học tiếng Anh về con mèo cho trẻ em.")
        self.assertEqual(requests[0].recent_history[0]["role"], "user")
        self.assertIsNone(requests[0].active_surface_summary)
        self.assertEqual(len(task_runtime.submissions), 1)
        self.assertEqual(task_runtime.presented, ["run_1"])


if __name__ == "__main__":
    unittest.main()
