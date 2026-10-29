"""Per-Live-session bridge from Task Manager events to presentation outputs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

import httpx

from lumi_openui.agent_contracts import AgentTaskRequestFactory
from lumi_openui.remote_tasks import (
    AsyncTaskExecutionLimits,
    GenericHttpAsyncAgentExecutor,
    InMemoryRemoteJobRegistry,
    InMemoryResourceStore,
    ResourceMediaReferenceAdapter,
)
from lumi_openui.artifacts import ArtifactStore
from lumi_openui.executors import (
    bind_complete_surface_task,
    build_default_executor_registry,
)
from lumi_openui.executors.information import bind_search_information_task
from lumi_openui.executors.media import bind_search_image_task
from lumi_openui.executors.draw import bind_draw_image_task
from lumi_openui.task_agents.search_information.agent import InformationRetrievalAgent
from lumi_openui.knowledge import (
    KNOWLEDGE_TOOL_REGISTRY,
    KnowledgeGateway,
    KnowledgeGrantRegistry,
    KnowledgeRegistry,
)
from lumi_openui.task_agents.draw_image.agent import GeminiDrawImageAgent
from lumi_openui.task_agents.search_image.agent import MediaRetrievalAgent
from lumi_openui.surface import (
    CompiledSurface,
    DraftSurfaceCompiler,
    SurfaceDraft,
)
from lumi_openui.surface.assembly import SurfaceAssemblyAgent
from lumi_openui.surface.final_compiler import FinalSurfaceCompiler
from lumi_openui.surface.assembly import CompleteSurface
from lumi_openui.surface.contracts import AnchorManifest
from lumi_openui.task_manager import SurfaceRetrievalPlan, TaskEvent, TaskManager, TaskRunSnapshot

logger = logging.getLogger("lumi.live_task_runtime")


TaskEventCallback = Callable[[TaskEvent], Awaitable[None] | None]
DraftCallback = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None] | None]
CompleteCallback = Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class PreparedSurfacePlan:
    """A validated Draft and its stable runtime identities."""

    draft: SurfaceDraft
    compiled: CompiledSurface


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
        self._draft_presentations: dict[str, CompiledSurface] = {}
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
        self._bind_information(executor_registry, settings)
        if getattr(settings, "enable_search_image_task", True):
            self._bind_media(executor_registry, settings)
        else:
            logger.info(
                "[LIVE_TASK_RUNTIME:DISABLED] session=%s task_type=search_image_task",
                self._session_id,
            )
        self._bind_draw(executor_registry, settings)
        self._assembly_agent = SurfaceAssemblyAgent(
            settings, registries.widget_registry, client_factory=assembly_client_factory,
            asset_catalog=assets,
        )
        self._final_compiler = FinalSurfaceCompiler(
            registries.widget_registry, asset_catalog=assets,
            media_reference_store=self._resource_media_references)
        bind_complete_surface_task(
            executor_registry,
            self._assembly_agent,
            self._final_compiler,
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

        for run_id in self._run_ids:
            await self.manager.cancel_run(run_id)
        for run_id in self._run_ids:
            await self.manager.wait_for_run(run_id)
        if self._knowledge_gateway is not None:
            for run_id in self._run_ids:
                self._knowledge_gateway.grant_registry.revoke_run(run_id)
        self._run_ids.clear()
        await self._assembly_agent.close()
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
        return PreparedSurfacePlan(
            draft=draft,
            compiled=compiled,
        )

    async def submit_surface_plan(
        self,
        plan: SurfaceRetrievalPlan,
        *,
        active_surface: dict[str, Any] | None,
        prepared: PreparedSurfacePlan | None = None,
        before_schedule: Callable[[], Awaitable[None] | None] | None = None,
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
            before_schedule=before_schedule,
        )
        self._run_ids.add(snapshot.run_id)
        self._draft_presentations[snapshot.run_id] = prepared.compiled
        return snapshot

    def preflight_surface_edit(self, plan: SurfaceRetrievalPlan, *, surface_runtime: Any,
                               run_id: str) -> PreparedSurfacePlan:
        draft = SurfaceDraft(plan.blocks)
        skeleton = self._draft_compiler.compile(draft)
        candidate = self._final_compiler.compile(CompleteSurface(draft), surface_id=skeleton.surface_id,
            media_session_id=run_id, anchor_manifest=AnchorManifest.from_compiled(skeleton))
        compiled = surface_runtime.prepare_edit(session_id=self._session_id,
            program=plan.openui_program, candidate=candidate)
        self.manager.validate_update(run_id, plan, compiled)
        return PreparedSurfacePlan(draft=draft, compiled=compiled)

    async def prepare_completion(self, payload: dict[str, Any], *, surface_runtime: Any) -> dict[str, Any]:
        """Reject stale completion; edits during Assembly are outside this phase."""
        accepted = surface_runtime.accepted_openui_program(self._session_id)
        if accepted is None or accepted.source != payload['base_source']:
            raise ValueError('Completion source is stale; the accepted program changed.')
        return payload

    async def present_draft(self, run_id: str) -> None:
        """Render the already-validated Draft after Live marks the run active."""

        try:
            compiled = self._draft_presentations.pop(run_id)
        except KeyError as exc:
            raise ValueError(f"unknown pending Draft for run '{run_id}'.") from exc
        await self._present_draft(compiled)

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

    async def _present_draft(self, compiled: CompiledSurface) -> None:
        payload = {
            "compiled": compiled,
        }
        logger.info(
            "[PRESENTATION:DRAFT_READY] session=%s surface=%s revision=%s components=%s",
            self._session_id,
            compiled.surface_id,
            compiled.revision,
            len(compiled.components),
        )
        await _maybe_await(self._on_draft(payload, {}))

    async def _present_complete(
        self,
        _complete: Any,
        compiled: Any,
        surface_completion: dict[str, Any],
    ) -> Any:
        # Runtime commits the validated OpenUI source together with Lumi state
        # bindings; the browser receives Lang, while Stage Map uses the parser
        # tree plus that same state/anchor revision.
        logger.info(
            "[PRESENTATION:COMPLETE_READY] session=%s surface=%s revision=%s components=%s",
            self._session_id,
            compiled.surface_id,
            compiled.revision,
            len(compiled.components),
        )
        return await _maybe_await(self._on_complete({
            "compiled": compiled,
            "surface_completion": dict(surface_completion),
            "openui_program": _complete.openui_program,
            "base_source": _complete.base_source,
            "owner_task_id": _complete.owner_task_id,
            "owner_run_id": _complete.owner_run_id,
            "semantic_blocks": _complete.draft.blocks,
        }, {}))


def _unavailable_executor(detail: str):
    async def execute(_context: Any):
        raise RuntimeError(detail or "Task executor is not configured.")

    return execute


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
