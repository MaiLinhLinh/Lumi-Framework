"""Contracts for Live requests and Agent surface plans."""

import unittest

from final_framework.task_manager import (
    RequestTaskManager,
    SurfaceRetrievalPlan,
    TaskContractError,
    request_task_manager_from_call,
)


class TaskManagerContractTests(unittest.TestCase):
    def test_parses_minimal_request_task_manager_call(self) -> None:
        command = request_task_manager_from_call({
            "name": "request_task_manager",
            "arguments": {"request": "Tạo flashcard học tiếng Anh về con mèo cho trẻ em."},
        })

        self.assertIsInstance(command, RequestTaskManager)
        self.assertEqual(command.request.request, "Tạo flashcard học tiếng Anh về con mèo cho trẻ em.")
        self.assertEqual(command.to_dict()["arguments"], {"request": command.request.request})

    def test_rejects_extra_request_task_manager_arguments(self) -> None:
        with self.assertRaisesRegex(TaskContractError, "exactly the request field"):
            request_task_manager_from_call({
                "name": "request_task_manager",
                "arguments": {"request": "Tạo panel.", "tasks": []},
            })

    def test_parses_surface_retrieval_plan_and_validates_slot_mapping(self) -> None:
        plan = SurfaceRetrievalPlan.from_dict({
            "action": "create_surface_plan",
            "template_description": "Một thẻ từ vựng có thể lật.",
            "surface": {
                "blocks": [{
                    "widget_id": "flashcard",
                    "slot_role": "cat_card",
                    "description": "Thẻ học có ảnh con mèo.",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 8},
                    "props": {},
                }],
            },
            "tasks": [{
                "evidence_ref": "#E1",
                "task_type": "search_image_task",
                "target_slots": ["cat_card"],
                "instruction": "Tìm ảnh con mèo rõ chủ thể.",
            }],
        })

        self.assertEqual(plan.tasks[0].target_slots, ("cat_card",))
        self.assertEqual(plan.to_dict()["action"], "create_surface_plan")

    def test_rejects_plan_task_target_without_matching_slot(self) -> None:
        with self.assertRaisesRegex(TaskContractError, "existing slot_role"):
            SurfaceRetrievalPlan.from_dict({
                "action": "create_surface_plan",
                "template_description": None,
                "surface": {
                    "blocks": [{
                        "widget_id": "text",
                        "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                        "props": {"content": "Tiêu đề"},
                    }],
                },
                "tasks": [{
                    "evidence_ref": "#E1",
                    "task_type": "search_information_task",
                    "target_slots": ["missing_slot"],
                    "instruction": "Tìm dữ liệu.",
                }],
            })

    def test_rejects_legacy_worker_type_instead_of_concrete_task_type(self) -> None:
        with self.assertRaisesRegex(TaskContractError, "unsupported fields"):
            SurfaceRetrievalPlan.from_dict({
                "action": "create_surface_plan",
                "template_description": None,
                "surface": {"blocks": [{
                    "widget_id": "image",
                    "slot_role": "illustration",
                    "description": "Image pending.",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 8},
                    "props": {},
                }]},
                "tasks": [{
                    "evidence_ref": "#E1",
                    "worker_type": "media",
                    "target_slots": ["illustration"],
                    "instruction": "Find an image.",
                }],
            })

    def test_rejects_old_live_tool_name(self):
        with self.assertRaisesRegex(TaskContractError, "request_task_manager"):
            request_task_manager_from_call({"name": "submit_experience_plan", "arguments": {"request": "Tạo panel"}})

    def test_rejects_duplicate_evidence_in_surface_plan(self):
        from final_framework.tests.plan_fixtures import make_surface_plan
        raw = make_surface_plan((("#E1", "search_information_task", "Tìm dữ liệu"),)).to_dict()
        raw["tasks"].append(dict(raw["tasks"][0]))
        with self.assertRaisesRegex(TaskContractError, "duplicate evidence_ref"):
            SurfaceRetrievalPlan.from_dict(raw)

    def test_rejects_extra_surface_plan_fields(self):
        from final_framework.tests.plan_fixtures import make_surface_plan
        raw = make_surface_plan().to_dict()
        raw["slots"] = []
        with self.assertRaisesRegex(TaskContractError, "unsupported fields"):
            SurfaceRetrievalPlan.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
