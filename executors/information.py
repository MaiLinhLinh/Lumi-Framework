"""Concrete executor for the independent information-retrieval agent."""

from __future__ import annotations

from typing import Protocol

from final_framework.agent_tasks import AgentTaskRequest, AgentTaskRequestFactory
from final_framework.knowledge import (
    SEARCH_LOCAL_TEXT,
    KnowledgeAccessContext,
    KnowledgeGateway,
)
from final_framework.knowledge.information_retrieval import InformationRetrievalResult
from final_framework.task_manager import Artifact, TaskExecutionContext, TaskResult
from final_framework.executors.registry import ExecutorRegistry, SEARCH_INFORMATION_TASK


class InformationAgent(Protocol):
    async def retrieve(
        self,
        request: AgentTaskRequest,
        gateway: KnowledgeGateway,
        access_context: KnowledgeAccessContext,
    ) -> InformationRetrievalResult: ...


class SearchInformationTaskExecutor:
    """Store one validated information result as a Knowledge Artifact."""

    def __init__(
        self,
        agent: InformationAgent,
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
        if task.task_type != "search_information_task":
            raise ValueError("SearchInformationTaskExecutor requires search_information_task.")
        if task.evidence_ref is None:
            raise ValueError("search_information_task requires evidence_ref.")
        request = self._request_factory.build(
            context,
            task_input={"query": task.instruction},
            knowledge_catalog=tuple(self._gateway.registry.package_catalog()),
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        access_context = self._gateway.issue_access_context(
            run_id=task.run_id,
            task_id=task.task_id,
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        try:
            result = await self._agent.retrieve(
                request,
                self._gateway,
                access_context,
            )
        finally:
            self._gateway.grant_registry.revoke(access_context)
        artifact = Artifact(
            artifact_id=f"knowledge_{task.task_id}",
            artifact_type="knowledge",
            run_id=task.run_id,
            produced_by=task.task_id,
            evidence_ref=task.evidence_ref,
            data={"items": list(result.items)},
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
            },
        )


def bind_search_information_task(
    registry: ExecutorRegistry,
    agent: InformationAgent,
    request_factory: AgentTaskRequestFactory,
    gateway: KnowledgeGateway,
) -> SearchInformationTaskExecutor:
    """Bind only the CP5 information executor to a declared registry.

    Runtime composition will call this alongside the independent bindings for
    the other task types; it never creates or configures those other agents.
    """

    executor = SearchInformationTaskExecutor(agent, request_factory, gateway)
    registry.bind(SEARCH_INFORMATION_TASK, executor)
    return executor
