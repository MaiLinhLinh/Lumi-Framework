"""Concrete executor for the independent image-generation task."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

from lumi_openui.agent_contracts import AgentTaskRequest, AgentTaskRequestFactory
from lumi_openui.executors.registry import DRAW_IMAGE_TASK, ExecutorRegistry
from lumi_openui.knowledge.media_store import FilesystemMediaStore
from lumi_openui.task_agents.draw_image.contracts import GeneratedImageResult
from lumi_openui.task_manager import Artifact, TaskExecutionContext, TaskResult


class DrawAgent(Protocol):
    async def generate(self, request: AgentTaskRequest) -> GeneratedImageResult: ...


class DrawImageTaskExecutor:
    """Persist a generated image and publish a versioned internal asset ref."""

    def __init__(
        self,
        agent: DrawAgent,
        media_store: FilesystemMediaStore,
        request_factory: AgentTaskRequestFactory,
        knowledge_catalog: tuple[Mapping[str, object], ...],
    ) -> None:
        if not callable(getattr(agent, "generate", None)):
            raise TypeError("agent must provide async generate(request).")
        if not isinstance(media_store, FilesystemMediaStore):
            raise TypeError("media_store must be a FilesystemMediaStore.")
        if not isinstance(request_factory, AgentTaskRequestFactory):
            raise TypeError("request_factory must be an AgentTaskRequestFactory.")
        if not isinstance(knowledge_catalog, tuple):
            raise TypeError("knowledge_catalog must be a tuple.")
        self._agent = agent
        self._media_store = media_store
        self._request_factory = request_factory
        self._knowledge_catalog = knowledge_catalog

    async def __call__(self, context: TaskExecutionContext) -> TaskResult:
        task = context.task
        if task.task_type != DRAW_IMAGE_TASK:
            raise ValueError("DrawImageTaskExecutor requires draw_image_task.")
        if task.evidence_ref is None:
            raise ValueError("draw_image_task requires evidence_ref.")
        if task.instruction is None:
            raise ValueError("draw_image_task requires instruction.")

        request = self._request_factory.build(
            context,
            task_input={"query": task.instruction},
            knowledge_catalog=self._knowledge_catalog,
            allowed_tools=(),
        )
        result = await self._agent.generate(request)
        if not isinstance(result, GeneratedImageResult):
            raise TypeError("Draw Agent must return GeneratedImageResult.")

        version = task.attempt + 1
        media_id = _generated_media_id(task.task_id, version)
        object_key = self._media_store.put(
            media_id,
            f"v{version}",
            result.mime_type,
            result.image_bytes,
        )
        reference = {
            "media_id": media_id,
            "package_id": "generated_image",
            "object_key": object_key,
            "mime_type": result.mime_type,
            "caption": result.caption,
            "source_uri": f"generated://{task.run_id}/{task.task_id}/v{version}",
            "local_file_missing": False,
            "generation_metadata": dict(result.generation_metadata),
        }
        artifact = Artifact(
            artifact_id=f"generated_image_{task.task_id}",
            artifact_type="generated_image",
            run_id=task.run_id,
            produced_by=task.task_id,
            evidence_ref=task.evidence_ref,
            data={
                "items": [{"media_id": media_id}],
                "references": [reference],
            },
        )
        return TaskResult(
            task_id=task.task_id,
            status="succeeded",
            artifact=artifact,
            evidence_ref=task.evidence_ref,
            metrics={"duration_ms": result.duration_ms},
        )


def bind_draw_image_task(
    registry: ExecutorRegistry,
    agent: DrawAgent,
    media_store: FilesystemMediaStore,
    request_factory: AgentTaskRequestFactory,
    knowledge_catalog: tuple[Mapping[str, object], ...],
) -> DrawImageTaskExecutor:
    """Bind only the independent ``draw_image_task`` executor."""

    executor = DrawImageTaskExecutor(
        agent,
        media_store,
        request_factory,
        knowledge_catalog,
    )
    registry.bind(DRAW_IMAGE_TASK, executor)
    return executor


def _generated_media_id(task_id: str, version: int) -> str:
    """Make the task-owned media ID safe for ``FilesystemMediaStore``."""

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", task_id).strip("-")
    if not normalized:
        normalized = "task"
    return f"generated-{normalized}-v{version}"
