"""CP2 tests for the standalone Task Manager Agent."""

from __future__ import annotations

import asyncio
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from final_framework.registry_composition import load_framework_registries
from final_framework.surface import DraftSurfaceCompiler
from final_framework.task_manager_agent import TaskManagerAgent, TaskManagerAgentError, TaskManagerAgentRequest


class _Function:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = json.dumps(arguments)


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict) -> None:
        self.id = call_id
        self.function = _Function(name, arguments)


class _FakeCompletions:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _completion(*, tool_calls=(), content: str | None = None):
    message = SimpleNamespace(tool_calls=list(tool_calls), content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class TaskManagerAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registries = load_framework_registries(Path("final_framework"))

    def _agent(self, client, **kwargs):
        return TaskManagerAgent(
            api_key="test-key", model="test-manager", widget_registry=self.registries.widget_registry,
            template_catalog=self.registries.template_catalog(),
            draft_compiler=DraftSurfaceCompiler(self.registries.widget_registry, self.registries.asset_catalog()),
            client_factory=lambda **_: client, **kwargs,
        )

    @staticmethod
    def _title_plan():
        return {
            "action": "create_surface_plan", "template_description": None, "tasks": [],
            "surface": {"blocks": [{"widget_id": "text", "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1}, "props": {"content": "Tiêu đề"}}]},
        }

    def test_agent_creates_timeline_with_text_evidence_task(self):
        raw = {
            "action": "create_surface_plan", "template_description": "Dòng thời gian lịch sử.",
            "surface": {"blocks": [{
                "widget_id": "timeline", "slot_role": "history", "description": "Các mốc lịch sử đã xác minh.",
                "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 8}, "props": {"title": "Lịch sử", "items": [{}, {}, {}, {}, {}]},
            }]},
            "tasks": [{"evidence_ref": "#E1", "task_type": "search_information_task", "target_slots": ["history"], "instruction": "Tìm các mốc lịch sử có nguồn."}],
        }
        client = _FakeClient([
            _completion(tool_calls=(_ToolCall("timeline", "describe_widget", {"widget_id": "timeline"}),)),
            _completion(content=json.dumps(raw)),
        ])
        plan = asyncio.run(self._agent(client).plan(TaskManagerAgentRequest(request="Tạo timeline lịch sử.")))
        self.assertEqual(plan.blocks[0].widget_id, "timeline")
        self.assertEqual(plan.tasks[0].target_slots, ("history",))
        self.assertEqual(plan.tasks[0].task_type, "search_information_task")

    def test_compiler_feedback_repairs_invalid_draft_in_same_agent_loop(self):
        valid = self._title_plan()
        invalid = copy.deepcopy(valid)
        invalid["surface"]["blocks"][0]["props"] = {}
        client = _FakeClient([
            _completion(tool_calls=(_ToolCall("text", "describe_widget", {"widget_id": "text"}),)),
            _completion(content=json.dumps(invalid)),
            _completion(content=json.dumps(valid)),
        ])
        plan = asyncio.run(self._agent(client).plan(TaskManagerAgentRequest(request="Tạo tiêu đề.")))
        self.assertEqual(plan.blocks[0].props["content"], "Tiêu đề")
        feedback = json.loads(client.chat.completions.calls[2]["messages"][-1]["content"])
        self.assertEqual(feedback["type"], "surface_validation_feedback")
        self.assertIn("content", feedback["errors"][0]["detail"])

    def test_task_bound_fake_placeholder_is_rejected_and_repaired(self):
        valid = {
            "action": "create_surface_plan",
            "template_description": "Dòng thời gian lịch sử.",
            "surface": {"blocks": [{
                "widget_id": "timeline",
                "slot_role": "history",
                "description": "Hai mốc lịch sử đã xác minh.",
                "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 6},
                "props": {"items": [{}, {}]},
            }]},
            "tasks": [{
                "evidence_ref": "#E1",
                "task_type": "search_information_task",
                "target_slots": ["history"],
                "instruction": "Tìm hai mốc lịch sử có nguồn.",
            }],
        }
        invalid = copy.deepcopy(valid)
        invalid["surface"]["blocks"][0]["props"]["items"] = [
            {"label": "1", "title": "Mốc 1", "description": "Đang chờ dữ liệu"},
            {"label": "2", "title": "Mốc 2", "description": "Đang chờ dữ liệu"},
        ]
        client = _FakeClient([
            _completion(tool_calls=(
                _ToolCall("timeline", "describe_widget", {"widget_id": "timeline"}),
            )),
            _completion(content=json.dumps(invalid, ensure_ascii=False)),
            _completion(content=json.dumps(valid, ensure_ascii=False)),
        ])
        plan = asyncio.run(self._agent(client).plan(
            TaskManagerAgentRequest(request="Tạo timeline lịch sử.")
        ))
        self.assertEqual(plan.blocks[0].props["items"], [{}, {}])
        feedback = json.loads(client.chat.completions.calls[2]["messages"][-1]["content"])
        self.assertIn("fake placeholder", feedback["errors"][0]["detail"])

    def test_widget_must_be_described_before_plan_is_accepted(self):
        client = _FakeClient([_completion(content=json.dumps(self._title_plan()))])
        with self.assertRaisesRegex(TaskManagerAgentError, "correction turn limit"):
            asyncio.run(self._agent(client, max_planning_turns=1).plan(TaskManagerAgentRequest(request="Tạo tiêu đề.")))

    def test_agent_can_describe_template_from_global_catalog(self):
        template_id = self.registries.template_catalog().entries[0].id
        client = _FakeClient([
            _completion(tool_calls=(
                _ToolCall("template", "describe_template", {"template_id": template_id}),
                _ToolCall("text", "describe_widget", {"widget_id": "text"}),
            )),
            _completion(content=json.dumps(self._title_plan())),
        ])
        asyncio.run(self._agent(client).plan(TaskManagerAgentRequest(request="Tạo panel từ catalog.")))
        responses = [json.loads(m["content"]) for m in client.chat.completions.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(responses[0]["template_id"], template_id)
        self.assertIn("blocks", responses[0])

    def test_agent_describes_widgets_and_returns_surface_retrieval_plan(self) -> None:
        plan_json = {
            "action": "create_surface_plan",
            "template_description": "Một flashcard lớn để trẻ lật thẻ.",
            "surface": {"blocks": [
                {
                    "widget_id": "text",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                    "props": {"content": "Học tiếng Anh cùng chú mèo", "role": "title"},
                },
                {
                    "widget_id": "flashcard",
                    "slot_role": "cat_flashcard",
                    "description": "Thẻ học từ vựng cần ảnh con mèo rõ chủ thể ở mặt trước.",
                    "grid": {"col": 3, "row": 2, "col_span": 10, "row_span": 7},
                    "props": {
                        "front": {"text": "Con vật này là gì?"},
                        "back": {"word": "cat", "phonetic": "/kæt/", "meaning": "con mèo"},
                    },
                },
                {
                    "widget_id": "text",
                    "grid": {"col": 1, "row": 9, "col_span": 16, "row_span": 1},
                    "props": {"content": "Chạm vào thẻ để lật.", "role": "subtitle"},
                },
            ]},
            "tasks": [{
                "evidence_ref": "#E1",
                "task_type": "search_image_task",
                "target_slots": ["cat_flashcard"],
                "instruction": "Tìm ảnh một con mèo rõ chủ thể, thân thiện với trẻ em.",
            }],
        }
        fake_client = _FakeClient([
            _completion(tool_calls=(
                _ToolCall("text", "describe_widget", {"widget_id": "text"}),
                _ToolCall("flashcard", "describe_widget", {"widget_id": "flashcard"}),
            )),
            _completion(content=json.dumps(plan_json, ensure_ascii=False)),
        ])
        agent = TaskManagerAgent(
            api_key="test-key",
            model="qwen-3.8-27b",
            widget_registry=self.registries.widget_registry,
            template_catalog=self.registries.template_catalog(),
            draft_compiler=DraftSurfaceCompiler(
                self.registries.widget_registry, self.registries.asset_catalog()
            ),
            client_factory=lambda **_: fake_client,
        )

        plan = asyncio.run(agent.plan(TaskManagerAgentRequest(
            request="Tạo flashcard học tiếng Anh về con mèo cho trẻ em."
        )))

        self.assertEqual(plan.tasks[0].evidence_ref, "#E1")
        self.assertEqual(plan.blocks[1].slot_role, "cat_flashcard")
        self.assertEqual(agent.model_name, "qwen-3.8-27b")
        first_payload = json.loads(fake_client.chat.completions.calls[0]["messages"][1]["content"])
        self.assertEqual(first_payload["request"], "Tạo flashcard học tiếng Anh về con mèo cho trẻ em.")
        self.assertNotIn("experience_plan", first_payload)
        self.assertEqual(
            {entry["task_type"] for entry in first_payload["task_catalog"]},
            {"search_information_task", "search_image_task", "draw_image_task"},
        )


if __name__ == "__main__":
    unittest.main()
