"""CP7 tests for the independent draw-image executor boundary."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from final_framework.agent_tasks import AgentTaskRequestFactory
from final_framework.executors.draw import DrawImageTaskExecutor, bind_draw_image_task
from final_framework.executors.registry import DRAW_IMAGE_TASK, build_default_executor_registry
from final_framework.knowledge.media_store import FilesystemMediaStore
from final_framework.knowledge import KNOWLEDGE_TOOL_REGISTRY
from final_framework.media.generation import (
    GeneratedImageResult,
    ImageGenerationError,
    validate_draw_configuration,
)
import final_framework.media.gemini_draw as gemini_draw
from final_framework.media.gemini_draw import GeminiDrawImageAgent
from final_framework.settings import Settings
from final_framework.tests.plan_fixtures import make_surface_plan
from final_framework.task_manager import TaskExecutionContext, TaskRecord


def _settings(**overrides: str) -> Settings:
    values = {
        "gemini_live_api_key": "live",
        "gemini_live_model": "live-model",
        "gemini_live_voice": "voice",
        "live_turn_timeout_seconds": 45,
        "live_idle_timeout_seconds": 900,
        "live_reconnect_grace_seconds": 30,
        "presentation_animation_delay_ms": 0,
        "gemini_draw_key": "draw-key",
        "gemini_draw_model": "draw-model",
    }
    values.update(overrides)
    return Settings(**values)


def _context(*, attempt: int = 0) -> TaskExecutionContext:
    plan = make_surface_plan((("#E3", DRAW_IMAGE_TASK, "Dữ liệu kiểm thử."),))
    return TaskExecutionContext(
        run_id="run_1",
        surface_plan=plan,
        task=TaskRecord(
            "run_1",
            "task_draw",
            DRAW_IMAGE_TASK,
            "QUEUED",
            attempt=attempt,
            evidence_ref="#E3",
            instruction="Vẽ cây vú sữa bằng bút chì màu.",
        ),
    )


KNOWLEDGE_CATALOG = (
    {
        "package_id": "wikipedia_vi",
        "description": "Bách khoa tiếng Việt.",
        "languages": ["vi"],
        "content_types": ["text", "image"],
    },
)


class _Agent:
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, request) -> GeneratedImageResult:
        self.requests.append(request)
        return GeneratedImageResult(
            image_bytes=b"generated-image",
            mime_type="image/png",
            caption="Minh họa cây vú sữa bằng bút chì màu.",
            generation_metadata={"provider": "fake", "model": "fake-draw"},
            duration_ms=17,
        )


class _Interactions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _GeminiClient:
    def __init__(self, interactions: _Interactions) -> None:
        self.interactions = interactions


class DrawImageTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.store = FilesystemMediaStore(Path(self._temporary.name) / "media")
        self._original_types = gemini_draw.types
        gemini_draw.types = SimpleNamespace(HttpOptions=lambda **kwargs: kwargs)
        self.factory = AgentTaskRequestFactory(KNOWLEDGE_TOOL_REGISTRY)

    def tearDown(self) -> None:
        gemini_draw.types = self._original_types
        self._temporary.cleanup()

    def test_executor_persists_generated_bytes_and_returns_artifact(self) -> None:
        agent = _Agent()
        result = asyncio.run(
            DrawImageTaskExecutor(
                agent,
                self.store,
                self.factory,
                KNOWLEDGE_CATALOG,
            )(_context())
        )

        self.assertEqual(result.evidence_ref, "#E3")
        self.assertEqual(result.metrics, {"duration_ms": 17})
        artifact = result.artifact
        self.assertEqual(artifact.artifact_type, "generated_image")
        self.assertFalse(hasattr(artifact, "target_slots"))
        self.assertEqual(artifact.data["items"], [{"media_id": "generated-task_draw-v1"}])
        reference = artifact.data["references"][0]
        self.assertEqual(reference["object_key"], "generated-task_draw-v1.v1.png")
        self.assertEqual(reference["source_uri"], "generated://run_1/task_draw/v1")
        self.assertEqual(self.store.read(reference["object_key"]), b"generated-image")
        self.assertEqual(agent.requests[0].framework_context.tool_grants, ())
        self.assertEqual(
            agent.requests[0].framework_context.knowledge_catalog[0]["package_id"],
            "wikipedia_vi",
        )
        self.assertEqual(agent.requests[0].input["query"], "Vẽ cây vú sữa bằng bút chì màu.")

    def test_retry_uses_attempt_scoped_media_but_one_unversioned_artifact(self) -> None:
        result = asyncio.run(
            DrawImageTaskExecutor(_Agent(), self.store, self.factory, ())(
                _context(attempt=1)
            )
        )

        artifact = result.artifact
        self.assertEqual(artifact.artifact_id, "generated_image_task_draw")
        self.assertFalse(hasattr(artifact, "version"))
        self.assertEqual(artifact.data["items"], [{"media_id": "generated-task_draw-v2"}])
        self.assertTrue(self.store.path("generated-task_draw-v2.v2.png").is_file())

    def test_bind_isolated_draw_executor(self) -> None:
        registry = build_default_executor_registry()
        bound = bind_draw_image_task(
            registry,
            _Agent(),
            self.store,
            self.factory,
            (),
        )

        self.assertIs(registry.resolve(DRAW_IMAGE_TASK), bound)

    def test_missing_draw_model_is_a_configuration_error_before_adapter_binding(self) -> None:
        with self.assertRaisesRegex(ImageGenerationError, "GEMINI_DRAW_MODEL"):
            validate_draw_configuration(_settings(gemini_draw_model=""))

    def test_gemini_adapter_returns_native_image_bytes_and_instruction_caption(self) -> None:
        interactions = _Interactions(SimpleNamespace(
            id="provider-request-1",
            output_image=SimpleNamespace(
                data=base64.b64encode(b"native-png").decode("ascii"),
                mime_type="image/png",
            ),
        ))
        agent = GeminiDrawImageAgent(
            _settings(gemini_draw_model="gemini-3.1-flash-lite-image"),
            client_factory=lambda **_: _GeminiClient(interactions),
        )

        request = self.factory.build(
            _context(),
            task_input={"query": "Vẽ cây vú sữa bằng bút chì màu."},
            knowledge_catalog=(),
            allowed_tools=(),
        )
        result = asyncio.run(agent.generate(request))

        self.assertEqual(result.image_bytes, b"native-png")
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual(result.caption, "Vẽ cây vú sữa bằng bút chì màu.")
        self.assertEqual(result.generation_metadata["model"], "gemini-3.1-flash-lite-image")
        self.assertEqual(interactions.calls[0]["model"], "gemini-3.1-flash-lite-image")
        self.assertIn("Vẽ cây vú sữa", interactions.calls[0]["input"])

    def test_gemini_adapter_rejects_response_without_image(self) -> None:
        agent = GeminiDrawImageAgent(
            _settings(),
            client_factory=lambda **_: _GeminiClient(_Interactions(SimpleNamespace(output_image=None))),
        )

        with self.assertRaisesRegex(ImageGenerationError, "no image output"):
            request = self.factory.build(
                _context(),
                task_input={"query": "Vẽ cây vú sữa bằng bút chì màu."},
                knowledge_catalog=(),
                allowed_tools=(),
            )
            asyncio.run(agent.generate(request))


if __name__ == "__main__":
    unittest.main()
