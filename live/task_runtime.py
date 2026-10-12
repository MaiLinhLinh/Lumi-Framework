"""Per-Live-session bridge from Task Manager events to presentation outputs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from final_framework.agent_tasks import AgentTaskRequestFactory
from final_framework.async_tasks import (
    AsyncTaskExecutionLimits,
    GenericHttpAsyncAgentExecutor,
    InMemoryRemoteJobRegistry,
    InMemoryResourceStore,
    ResourceMediaReferenceAdapter,
)
from final_framework.artifacts import ArtifactStore
from final_framework.executors import (
    bind_complete_surface_task,
    build_default_executor_registry,
)
from final_framework.executors.information import bind_search_information_task
from final_framework.executors.media import bind_search_image_task
from final_framework.executors.draw import bind_draw_image_task
from final_framework.knowledge.information_retrieval import InformationRetrievalAgent
from final_framework.knowledge import (
    KNOWLEDGE_TOOL_REGISTRY,
    KnowledgeGateway,
    KnowledgeGrantRegistry,
    KnowledgeRegistry,
)
from final_framework.media.gemini_draw import GeminiDrawImageAgent
from final_framework.media.retrieval import MediaRetrievalAgent
from final_framework.surface import (
    CompiledSurface,
    DraftPresentation,
    DraftPresenter,
    DraftSurfaceCompiler,
    SurfaceDraft,
)
from final_framework.surface.assembly import SurfaceAssemblyAgent
from final_framework.surface.final_compiler import FinalSurfaceCompiler
from final_framework.task_manager import SurfaceRetrievalPlan, TaskEvent, TaskManager, TaskRunSnapshot

logger = logging.getLogger("lumi.live_task_runtime")


TaskEventCallback = Callable[[TaskEvent], Awaitable[None] | None]
DraftCallback = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None] | None]
CompleteCallback = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class PreparedSurfacePlan:
    """A Draft whose browser payload and Stage Map passed presentation preflight."""

    draft: SurfaceDraft
    compiled: CompiledSurface
    presentation: DraftPresentation


class LiveTaskRuntime:
    """Own exactly one Task Manager and artifact store for a Live session.

    The runtime deliberately receives independent information/media agents. A
    configuration error binds a failing executor for that task type instead of
    preventing Gemini Live or unrelated tasks from starting.
    """

    def __init__(
        self,
        *,
        session_id: str,
        settings: Any,
        registries: Any,
        media_reference_store: Any,
        on_task_event: TaskEventCallback,
        on_draft: DraftCallback,
        on_complete: CompleteCallback,
        knowledge_gateway: KnowledgeGateway | None = None,
        async_http_client: Any | None = None,
        assembly_client_factory: Any | None = None,
    ) -> None:
        self._session_id = session_id
        self._registries = registries
        self._media_reference_store = media_reference_store
        self._artifact_store = ArtifactStore()
        self._on_draft = on_draft
        self._on_complete = on_complete
        self._draft_presentations: dict[str, DraftPresentation] = {}
        self._remote_jobs = InMemoryRemoteJobRegistry()
        self._resource_store = InMemoryResourceStore(
            tuple(getattr(settings, "async_task_resource_allowed_mime_types"))
        )
        self._resource_media_references = ResourceMediaReferenceAdapter(
            self._resource_store,
            resource_session_id=session_id,
            legacy_store=media_reference_store,
        )
        self._async_http_client = async_http_client or httpx.AsyncClient()
        self._owns_async_http_client = async_http_client is None
        self._agent_task_request_factory = AgentTaskRequestFactory(
            KNOWLEDGE_TOOL_REGISTRY
        )
        unified_knowledge_registry = getattr(registries, "knowledge_registry", None)
        if unified_knowledge_registry is not None and not isinstance(
            unified_knowledge_registry, KnowledgeRegistry
        ):
            raise TypeError("registries.knowledge_registry must be a KnowledgeRegistry.")
        if knowledge_gateway is not None and not isinstance(
            knowledge_gateway, KnowledgeGateway
        ):
            raise TypeError("knowledge_gateway must be a KnowledgeGateway.")
        if (
            knowledge_gateway is not None
            and unified_knowledge_registry is not None
            and knowledge_gateway.registry is not unified_knowledge_registry
        ):
            raise ValueError(
                "knowledge_gateway must use registries.knowledge_registry."
            )
        self._knowledge_gateway = knowledge_gateway or (
            KnowledgeGateway(
                unified_knowledge_registry,
                KnowledgeGrantRegistry(
                    default_ttl_seconds=settings.knowledge_tool_token_ttl_seconds
                ),
            )
            if unified_knowledge_registry is not None
            else None
        )
        self._run_ids: set[str] = set()
        enabled_task_types = None
        if not getattr(settings, "enable_search_image_task", True):
            enabled_task_types = (
                "search_information_task",
                "draw_image_task",
                "complete_surface_task",
            )
        executor_registry = build_default_executor_registry(enabled_task_types=enabled_task_types)
        self._bind_manifest_tasks(executor_registry, settings)
        assets = registries.asset_catalog()

        logger.info(
            "[LIVE_TASK_RUNTIME:INIT] session=%s widgets=%s assets=%s",
            session_id,
            len(registries.widget_registry.widget_index()),
            len(assets.assets),
        )

        self._draft_compiler = DraftSurfaceCompiler(
            registries.widget_registry,
            asset_catalog=assets,
        )
        self._draft_presenter = DraftPresenter(
            widget_registry=registries.widget_registry,
            asset_catalog=assets,
            asset_urls={asset.id: f"/assets/resources/{asset.id}" for asset in assets.assets},
            sink=self._present_draft,
        )

        self._bind_information(executor_registry, settings)
        if getattr(settings, "enable_search_image_task", True):
            self._bind_media(executor_registry, settings)
        else:
            logger.info(
                "[LIVE_TASK_RUNTIME:DISABLED] session=%s task_type=search_image_task",
                self._session_id,
            )
        self._bind_draw(executor_registry, settings)
        bind_complete_surface_task(
            executor_registry,
            SurfaceAssemblyAgent(
                settings,
                registries.widget_registry,
                client_factory=assembly_client_factory,
            ),
            FinalSurfaceCompiler(
                registries.widget_registry,
                asset_catalog=assets,
                media_reference_store=self._resource_media_references,
            ),
            self._artifact_store,
            self._resource_media_references,
            async_task_manifest_registry=registries.task_manifest_registry,
            resource_store=self._resource_store,
            resource_session_id=session_id,
            on_complete_ready=self._present_complete,
        )
        self.manager = TaskManager(
            executor_registry,
            self._artifact_store,
            event_sink=on_task_event,
        )

    @property
    def remote_job_registry(self) -> InMemoryRemoteJobRegistry:
        return self._remote_jobs

    @property
    def resource_store(self) -> InMemoryResourceStore:
        return self._resource_store

    async def close(self) -> None:
        """Release Runtime-only state owned by this Live session."""

        if self._knowledge_gateway is not None:
            for run_id in self._run_ids:
                self._knowledge_gateway.grant_registry.revoke_run(run_id)
        self._run_ids.clear()
        await self._remote_jobs.clear()
        self._resource_store.clear_session(self._session_id)
        if self._owns_async_http_client:
            await self._async_http_client.aclose()

    def _bind_manifest_tasks(self, registry: Any, settings: Any) -> None:
        limits = AsyncTaskExecutionLimits.from_settings(settings)
        for manifest in self._registries.task_manifest_registry.definitions():
            registry.declare(manifest.id)
            registry.bind(
                manifest.id,
                GenericHttpAsyncAgentExecutor(
                    manifest,
                    self._remote_jobs,
                    self._async_http_client,
                    limits,
                    resource_store=self._resource_store,
                    resource_session_id=self._session_id,
                    request_factory=self._agent_task_request_factory,
                    knowledge_catalog=(
                        tuple(self._knowledge_gateway.registry.package_catalog())
                        if self._knowledge_gateway is not None
                        else ()
                    ),
                    knowledge_grant_registry=(
                        self._knowledge_gateway.grant_registry
                        if self._knowledge_gateway is not None
                        else None
                    ),
                ),
            )
            logger.info(
                "[LIVE_TASK_RUNTIME:BOUND] session=%s task_type=%s executor=http_async_agent",
                self._session_id,
                manifest.id,
            )

    def preflight_surface_plan(self, plan: SurfaceRetrievalPlan) -> PreparedSurfacePlan:
        """Compile and materialize a Draft without creating or scheduling a run."""

        if not isinstance(plan, SurfaceRetrievalPlan):
            raise TypeError("plan must be a SurfaceRetrievalPlan.")
        draft = SurfaceDraft(blocks=plan.blocks)
        compiled = self._draft_compiler.compile(draft)
        presentation = self._draft_presenter.build(draft, compiled)
        return PreparedSurfacePlan(
            draft=draft,
            compiled=compiled,
            presentation=presentation,
        )

    async def submit_surface_plan(
        self,
        plan: SurfaceRetrievalPlan,
        *,
        active_surface: dict[str, Any] | None,
        prepared: PreparedSurfacePlan | None = None,
    ) -> TaskRunSnapshot:
        """Schedule a plan only after its Draft presentation passes preflight."""

        prepared = prepared or self.preflight_surface_plan(plan)
        if not isinstance(prepared, PreparedSurfacePlan):
            raise TypeError("prepared must be a PreparedSurfacePlan.")
        if prepared.draft.blocks != plan.blocks:
            raise ValueError("prepared Draft must exactly match Surface Plan blocks.")
        logger.info(
            "[LIVE_TASK_RUNTIME:SURFACE_PLAN_SUBMIT] session=%s blocks=%s tasks=%s active_surface=%s",
            self._session_id,
            len(plan.blocks),
            len(plan.tasks),
            active_surface is not None,
        )
        snapshot = await self.manager.submit_surface_plan(
            plan,
            draft=prepared.draft,
            compiled_draft=prepared.compiled,
            active_surface=active_surface,
        )
        self._run_ids.add(snapshot.run_id)
        self._draft_presentations[snapshot.run_id] = prepared.presentation
        return snapshot

    async def present_draft(self, run_id: str) -> None:
        """Render the already-validated Draft after Live marks the run active."""

        try:
            presentation = self._draft_presentations.pop(run_id)
        except KeyError as exc:
            raise ValueError(f"unknown pending Draft for run '{run_id}'.") from exc
        await self._present_draft(presentation)

    def _bind_information(self, registry: Any, settings: Any) -> None:
        try:
            if self._knowledge_gateway is None:
                raise RuntimeError("Knowledge Gateway is not configured.")
            agent = InformationRetrievalAgent(settings)
        except Exception as exc:
            logger.exception(
                "[LIVE_TASK_RUNTIME:BIND_FAILED] session=%s task_type=search_information_task",
                self._session_id,
            )
            registry.bind("search_information_task", _unavailable_executor(str(exc)))
            return
        bind_search_information_task(
            registry,
            agent,
            self._agent_task_request_factory,
            self._knowledge_gateway,
        )
        logger.info(
            "[LIVE_TASK_RUNTIME:BOUND] session=%s task_type=search_information_task model=%s",
            self._session_id,
            agent.model_name,
        )

    def _bind_media(self, registry: Any, settings: Any) -> None:
        try:
            if self._knowledge_gateway is None:
                raise RuntimeError("Knowledge Gateway is not configured.")
            agent = MediaRetrievalAgent(settings)
        except Exception as exc:
            logger.exception(
                "[LIVE_TASK_RUNTIME:BIND_FAILED] session=%s task_type=search_image_task",
                self._session_id,
            )
            registry.bind("search_image_task", _unavailable_executor(str(exc)))
            return
        bind_search_image_task(
            registry,
            agent,
            self._agent_task_request_factory,
            self._knowledge_gateway,
        )
        logger.info(
            "[LIVE_TASK_RUNTIME:BOUND] session=%s task_type=search_image_task model=%s",
            self._session_id,
            agent.model_name,
        )

    def _bind_draw(self, registry: Any, settings: Any) -> None:
        try:
            agent = GeminiDrawImageAgent(settings)
            media_store = self._media_reference_store.media_store
        except Exception as exc:
            logger.exception(
                "[LIVE_TASK_RUNTIME:BIND_FAILED] session=%s task_type=draw_image_task",
                self._session_id,
            )
            registry.bind("draw_image_task", _unavailable_executor(str(exc)))
            return
        knowledge_catalog = (
            tuple(self._knowledge_gateway.registry.package_catalog())
            if self._knowledge_gateway is not None
            else ()
        )
        bind_draw_image_task(
            registry,
            agent,
            media_store,
            self._agent_task_request_factory,
            knowledge_catalog,
        )
        logger.info(
            "[LIVE_TASK_RUNTIME:BOUND] session=%s task_type=draw_image_task model=%s",
            self._session_id,
            agent.model_name,
        )

    async def _present_draft(self, presentation: Any) -> None:
        payload = {
            "panel": dict(presentation.browser_payload),
            "compiled": presentation.compiled,
        }
        logger.info(
            "[PRESENTATION:DRAFT_READY] session=%s surface=%s revision=%s components=%s ascii_chars=%s",
            self._session_id,
            presentation.compiled.surface_id,
            presentation.compiled.revision,
            len(presentation.compiled.components),
            len(presentation.ascii_map),
        )
        await _maybe_await(self._on_draft(payload, {"visual_stage_map": presentation.ascii_map}))

    async def _present_complete(
        self,
        _complete: Any,
        compiled: Any,
        surface_completion: dict[str, Any],
    ) -> None:
        # CompiledSurface is intentionally installed by the domainless panel
        # runtime in the session callback, where the browser payload and ASCII
        # map are produced from the exact same materialized panel state.
        logger.info(
            "[PRESENTATION:COMPLETE_READY] session=%s surface=%s revision=%s components=%s",
            self._session_id,
            compiled.surface_id,
            compiled.revision,
            len(compiled.components),
        )
        await _maybe_await(self._on_complete({
            "compiled": compiled,
            "surface_completion": dict(surface_completion),
        }, {}))


def _unavailable_executor(detail: str):
    async def execute(_context: Any):
        raise RuntimeError(detail or "Task executor is not configured.")

    return execute


async def _maybe_await(value: Any) -> None:
    if hasattr(value, "__await__"):
        await value
