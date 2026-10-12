"""Concrete executor for the independent media-retrieval agent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from final_framework.agent_tasks import AgentTaskRequest, AgentTaskRequestFactory
from final_framework.executors.registry import ExecutorRegistry, SEARCH_IMAGE_TASK
from final_framework.knowledge import (
    SEARCH_LOCAL_MEDIA,
    KnowledgeAccessContext,
    KnowledgeGateway,
)
from final_framework.media.retrieval import MediaRetrievalResult
from final_framework.task_manager import Artifact, TaskExecutionContext, TaskResult


class MediaAgent(Protocol):
    async def retrieve(
        self,
        request: AgentTaskRequest,
        gateway: KnowledgeGateway,
        access_context: KnowledgeAccessContext,
    ) -> MediaRetrievalResult: ...


class SearchImageTaskExecutor:
    """Store a validated media result and its trusted internal references."""

    def __init__(
        self,
        agent: MediaAgent,
        request_factory: AgentTaskRequestFactory,
        gateway: KnowledgeGateway,
    ) -> None:
        if not callable(getattr(agent, "retrieve", None)):
            raise TypeError("agent must provide async retrieve(request, gateway, access_context).")
        if not isinstance(request_factory, AgentTaskRequestFactory):
            raise TypeError("request_factory must be an AgentTaskRequestFactory.")
        if not isinstance(gateway, KnowledgeGateway):
            raise TypeError("gateway must be a KnowledgeGateway.")
        self._agent = agent
        self._request_factory = request_factory
        self._gateway = gateway

    async def __call__(self, context: TaskExecutionContext) -> TaskResult:
        task = context.task
        if task.task_type != SEARCH_IMAGE_TASK:
            raise ValueError("SearchImageTaskExecutor requires search_image_task.")
        if task.evidence_ref is None:
            raise ValueError("search_image_task requires evidence_ref.")
        request = self._request_factory.build(
            context,
            task_input={"query": task.instruction},
            knowledge_catalog=tuple(self._gateway.registry.package_catalog()),
            allowed_tools=(SEARCH_LOCAL_MEDIA,),
        )
        access_context = self._gateway.issue_access_context(
            run_id=task.run_id,
            task_id=task.task_id,
            allowed_tools=(SEARCH_LOCAL_MEDIA,),
        )
        try:
            result = await self._agent.retrieve(
                request,
                self._gateway,
                access_context,
            )
        finally:
            self._gateway.grant_registry.revoke(access_context)
        usable_image_ids = {
            media_id.strip()
            for reference in result.references
            if isinstance(reference, Mapping)
            and isinstance((media_id := reference.get("media_id")), str)
            and media_id.strip()
            and isinstance((mime_type := reference.get("mime_type")), str)
            and mime_type.strip().lower().startswith("image/")
        }
        usable_image_count = sum(
            1
            for item in result.items
            if isinstance(item, Mapping)
            and isinstance((media_id := item.get("media_id")), str)
            and media_id.strip() in usable_image_ids
        )
        artifact = Artifact(
            artifact_id=f"media_{task.task_id}",
            artifact_type="media",
            run_id=task.run_id,
            produced_by=task.task_id,
            evidence_ref=task.evidence_ref,
            data={
                "items": [dict(item) for item in result.items],
                "references": [dict(reference) for reference in result.references],
            },
        )
        return TaskResult(
            task_id=task.task_id,
            status="succeeded",
            artifact=artifact,
            evidence_ref=task.evidence_ref,
            metrics={
                "duration_ms": result.duration_ms,
                "local_search_count": result.local_search_count,
                "google_grounding_used": result.google_grounding_used,
                "usable_resource_count": usable_image_count,
                "usable_resource_kinds": ["image"] if usable_image_count else [],
            },
        )


def bind_search_image_task(
    registry: ExecutorRegistry,
    agent: MediaAgent,
    request_factory: AgentTaskRequestFactory,
    gateway: KnowledgeGateway,
) -> SearchImageTaskExecutor:
    """Bind only the independent ``search_image_task`` executor."""

    executor = SearchImageTaskExecutor(agent, request_factory, gateway)
    registry.bind(SEARCH_IMAGE_TASK, executor)
    return executor
