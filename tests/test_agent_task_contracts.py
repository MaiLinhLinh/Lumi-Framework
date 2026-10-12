"""CP1 contracts shared by built-in and external Agent Tasks."""

import unittest

from final_framework.agent_tasks import (
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    AgentFrameworkContext,
    AgentTaskContractError,
    AgentTaskIdentity,
    AgentTaskRequest,
    AgentTaskRequestFactory,
    AgentToolRegistryError,
    build_default_agent_tool_registry,
)
from final_framework.task_manager import TaskExecutionContext, TaskRecord
from final_framework.tests.plan_fixtures import make_surface_plan


KNOWLEDGE_CATALOG = (
    {
        "package_id": "wikipedia_vi",
        "description": "Kiến thức bách khoa tiếng Việt.",
        "languages": ["vi"],
        "content_types": ["text", "image"],
    },
)


def make_execution_context() -> TaskExecutionContext:
    plan = make_surface_plan(
        (("#E1", "research_story_task", "Tìm tư liệu cho câu chuyện."),)
    )
    task = TaskRecord(
        run_id="run_123",
        task_id="task_456",
        task_type="research_story_task",
        status="running",
        evidence_ref="#E1",
        instruction="Tìm tư liệu cho câu chuyện.",
    )
    return TaskExecutionContext("run_123", task, plan)


class AgentTaskContractTests(unittest.TestCase):
    def test_round_trips_common_request_without_surface_bindings(self) -> None:
        request = AgentTaskRequest(
            task=AgentTaskIdentity(
                run_id="run_123",
                task_id="task_456",
                task_type="research_story_task",
                evidence_ref="#E1",
                instruction="Tìm tư liệu cho câu chuyện.",
            ),
            input={"query": "Tìm tư liệu cho câu chuyện."},
            framework_context=AgentFrameworkContext(
                knowledge_catalog=KNOWLEDGE_CATALOG,
                tool_grants=(SEARCH_LOCAL_TEXT,),
            ),
        )

        serialized = request.to_dict()
        self.assertEqual(AgentTaskRequest.from_dict(serialized), request)
        self.assertEqual(
            set(serialized), {"task", "input", "framework_context"}
        )
        self.assertNotIn("target_slots", repr(serialized))
        self.assertNotIn("surface", serialized)
        self.assertNotIn("widget", repr(serialized))
        self.assertNotIn("grid", repr(serialized))

    def test_factory_does_not_copy_target_slots_from_surface_plan(self) -> None:
        context = make_execution_context()
        self.assertEqual(context.surface_plan.tasks[0].target_slots, ("slot_1",))
        self.assertFalse(hasattr(context.task, "target_slots"))

        request = AgentTaskRequestFactory().build(
            context,
            task_input={"query": context.task.instruction},
            knowledge_catalog=KNOWLEDGE_CATALOG,
            allowed_tools=(SEARCH_LOCAL_TEXT, SEARCH_LOCAL_MEDIA),
        )

        serialized = request.to_dict()
        self.assertEqual(serialized["task"]["evidence_ref"], "#E1")
        self.assertNotIn("target_slots", serialized["task"])
        self.assertNotIn("target_slots", repr(serialized))

    def test_rejects_invalid_identity_input_catalog_and_duplicate_grants(self) -> None:
        with self.assertRaisesRegex(AgentTaskContractError, "task.run_id"):
            AgentTaskIdentity("", "task_1", "type", "#E1", "Làm việc")

        with self.assertRaisesRegex(AgentTaskContractError, "input must be an object"):
            AgentTaskRequest(
                AgentTaskIdentity("run", "task", "type", "#E1", "Làm việc"),
                [],
                AgentFrameworkContext((), ()),
            )

        duplicate_catalog = KNOWLEDGE_CATALOG + KNOWLEDGE_CATALOG
        with self.assertRaisesRegex(AgentTaskContractError, "duplicate package_id"):
            AgentFrameworkContext(duplicate_catalog, ())

        with self.assertRaisesRegex(AgentTaskContractError, "must not contain duplicates"):
            AgentFrameworkContext((), (SEARCH_LOCAL_TEXT, SEARCH_LOCAL_TEXT))

    def test_factory_rejects_unknown_tool_grant(self) -> None:
        with self.assertRaisesRegex(AgentToolRegistryError, "unknown Agent Task tool"):
            AgentTaskRequestFactory().build(
                make_execution_context(),
                task_input={"query": "Tìm tư liệu"},
                knowledge_catalog=KNOWLEDGE_CATALOG,
                allowed_tools=("search_everything",),
            )

    def test_default_tool_registry_exposes_two_canonical_tools(self) -> None:
        registry = build_default_agent_tool_registry()
        self.assertEqual(
            registry.names(), (SEARCH_LOCAL_TEXT, SEARCH_LOCAL_MEDIA)
        )
        for name in registry.names():
            definition = registry.get(name).to_dict()
            self.assertEqual(definition["name"], name)
            self.assertEqual(
                definition["input_schema"]["required"],
                ["query", "package_ids"],
            )

    def test_factory_requires_evidence_and_instruction_for_agent_tasks(self) -> None:
        plan = make_surface_plan()
        task = TaskRecord("run", "complete", "complete_surface_task", "running")
        context = TaskExecutionContext("run", task, plan)
        with self.assertRaisesRegex(AgentTaskContractError, "evidence_ref"):
            AgentTaskRequestFactory().build(
                context,
                task_input={},
                knowledge_catalog=(),
                allowed_tools=(),
            )


if __name__ == "__main__":
    unittest.main()
