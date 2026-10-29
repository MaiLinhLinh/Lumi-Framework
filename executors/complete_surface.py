"""Executor for the runtime-owned final Surface Assembly task."""

from __future__ import annotations

import inspect
import logging
from dataclasses import replace
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from lumi_openui.remote_tasks import (
    AsyncTaskManifestError,
    AsyncTaskManifestRegistry,
    ResourceStore,
    ResourceStoreError,
    normalized_resource_output_contract,
)
from lumi_openui.artifacts import ArtifactStore
from lumi_openui.executors.registry import COMPLETE_SURFACE_TASK, ExecutorRegistry
from lumi_openui.surface.assembly import SurfaceAssemblyAgent, SurfaceAssemblyError
from lumi_openui.surface.contracts import AnchorManifest, CompiledSurface, SurfaceDraft
from lumi_openui.surface.final_compiler import FinalSurfaceCompiler
from lumi_openui.task_manager import (
    Artifact,
    SurfacePlanTask,
    TaskExecutionContext,
    TaskResult,
)


logger = logging.getLogger("lumi.complete_surface")


class CompleteSurfaceTaskExecutor:
    """Assemble once after the barrier and retain semantic plus compiled output."""

    def __init__(
        self,
        assembly_agent: SurfaceAssemblyAgent,
        final_compiler: FinalSurfaceCompiler,
        artifact_store: ArtifactStore,
        media_reference_store: Any,
        *,
        async_task_manifest_registry: AsyncTaskManifestRegistry | None = None,
        resource_store: ResourceStore | None = None,
        resource_session_id: str | None = None,
        on_complete_ready: Callable[
            [Any, CompiledSurface, Mapping[str, Any]], Awaitable[CompiledSurface | None] | CompiledSurface | None
        ] | None = None,
    ) -> None:
        if not isinstance(assembly_agent, SurfaceAssemblyAgent):
            raise TypeError("assembly_agent must be a SurfaceAssemblyAgent.")
        if not isinstance(final_compiler, FinalSurfaceCompiler):
            raise TypeError("final_compiler must be a FinalSurfaceCompiler.")
        if not isinstance(artifact_store, ArtifactStore):
            raise TypeError("artifact_store must be an ArtifactStore.")
        if not callable(getattr(media_reference_store, "register_worker_result", None)):
            raise TypeError("media_reference_store must provide register_worker_result().")
        self._assembly_agent = assembly_agent
        self._final_compiler = final_compiler
        self._artifact_store = artifact_store
        self._media_reference_store = media_reference_store
        self._async_task_manifest_registry = async_task_manifest_registry
        self._resource_store = resource_store
        self._resource_session_id = resource_session_id
        self._on_complete_ready = on_complete_ready

    async def __call__(self, context: TaskExecutionContext) -> TaskResult:
        task = context.task
        if task.task_type != COMPLETE_SURFACE_TASK:
            raise ValueError("CompleteSurfaceTaskExecutor requires complete_surface_task.")
        draft_artifact = self._latest_surface_draft(context.run_id)
        draft = _draft_from_artifact(draft_artifact)
        evidence_by_ref = self._artifact_store.evidence_by_ref(context.run_id)
        if context.evidence_task_ids is not None:
            evidence_by_ref = {}
            for task_id in context.evidence_task_ids:
                artifact = self._artifact_store.artifact_for_task(context.run_id, task_id)
                if artifact is not None and artifact.evidence_ref is not None:
                    evidence_by_ref[artifact.evidence_ref] = artifact
        logger.info(
            "[COMPLETE:START] run=%s task=%s draft_artifact=%s blocks=%s evidence_refs=%s",
            context.run_id,
            task.task_id,
            draft_artifact.artifact_id,
            len(draft.blocks),
            list(evidence_by_ref),
        )
        task_outputs_by_ref = {}
        for plan_task in context.surface_plan.tasks:
            task_output = _task_output_for_artifact(
                plan_task,
                evidence_by_ref.get(plan_task.evidence_ref),
                self._async_task_manifest_registry,
                self._resource_store,
                self._resource_session_id,
            )
            if task_output is not None:
                task_outputs_by_ref[plan_task.evidence_ref] = task_output
        complete = await self._assembly_agent.assemble(
            draft=draft,
            surface_plan=context.surface_plan,
            task_outputs_by_ref=task_outputs_by_ref,
            run_id=context.run_id,
        )
        complete = replace(complete, owner_task_id=task.task_id, owner_run_id=context.run_id)
        _register_media_references(
            self._media_reference_store,
            session_id=context.run_id,
            evidence_by_ref=evidence_by_ref,
        )
        surface_id = draft_artifact.data.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id.strip():
            surface_id = f"panel-{context.run_id}"
        anchor_manifest = _anchor_manifest_from_artifact(draft_artifact)
        compiled = self._final_compiler.compile(
            complete,
            surface_id=surface_id,
            media_session_id=context.run_id,
            anchor_manifest=anchor_manifest,
        )
        warnings = _placeholder_warnings(compiled)
        surface_completion = _surface_completion(compiled)
        logger.info(
            "[COMPLETE:COMPILED] run=%s task=%s surface=%s revision=%s components=%s warnings=%s",
            context.run_id,
            task.task_id,
            compiled.surface_id,
            compiled.revision,
            len(compiled.components),
            list(warnings),
        )
        if self._on_complete_ready is not None:
            delivered = self._on_complete_ready(complete, compiled, surface_completion)
            if inspect.isawaitable(delivered):
                delivered = await delivered
            if isinstance(delivered, CompiledSurface):
                compiled = delivered
            elif isinstance(delivered, Mapping) and isinstance(delivered.get('compiled'), CompiledSurface):
                compiled = delivered['compiled']
                complete = replace(complete, draft=SurfaceDraft(delivered['semantic_blocks']),
                                   openui_program=delivered['openui_program'])
            warnings = _placeholder_warnings(compiled)
            surface_completion = _surface_completion(compiled)
        complete_artifact = Artifact(
            artifact_id=f"complete_surface_{task.task_id}",
            artifact_type="complete_surface",
            run_id=context.run_id,
            produced_by=task.task_id,
            data={
                "semantic_surface": complete.to_dict(),
                "compiled_surface": compiled.to_dict(),
                "surface_completion": surface_completion,
            },
        )
        logger.info(
            "[COMPLETE:ARTIFACT_READY] run=%s task=%s artifact=%s",
            context.run_id,
            task.task_id,
            complete_artifact.artifact_id,
        )
        return TaskResult(
            task_id=task.task_id,
            status="succeeded",
            artifact=complete_artifact,
            warnings=warnings,
            metrics=surface_completion,
        )

    def _latest_surface_draft(self, run_id: str) -> Artifact:
        drafts = [
            artifact
            for artifact in self._artifact_store.artifacts_for_run(run_id)
            if artifact.artifact_type == "surface_draft"
        ]
        if not drafts:
            raise SurfaceAssemblyError("complete_surface_task requires a Surface Draft Artifact.")
        if len(drafts) != 1:
            raise SurfaceAssemblyError("complete_surface_task requires exactly one Surface Draft Artifact.")
        return drafts[0]


def bind_complete_surface_task(
    registry: ExecutorRegistry,
    assembly_agent: SurfaceAssemblyAgent,
    final_compiler: FinalSurfaceCompiler,
    artifact_store: ArtifactStore,
    media_reference_store: Any,
    *,
    async_task_manifest_registry: AsyncTaskManifestRegistry | None = None,
    resource_store: ResourceStore | None = None,
    resource_session_id: str | None = None,
    on_complete_ready: Callable[
        [Any, CompiledSurface, Mapping[str, Any]], Awaitable[CompiledSurface | None] | CompiledSurface | None
    ] | None = None,
) -> CompleteSurfaceTaskExecutor:
    """Bind only runtime-owned ``complete_surface_task``."""

    executor = CompleteSurfaceTaskExecutor(
        assembly_agent,
        final_compiler,
        artifact_store,
        media_reference_store,
        async_task_manifest_registry=async_task_manifest_registry,
        resource_store=resource_store,
        resource_session_id=resource_session_id,
        on_complete_ready=on_complete_ready,
    )
    registry.bind(COMPLETE_SURFACE_TASK, executor)
    return executor


def _draft_from_artifact(artifact: Artifact) -> SurfaceDraft:
    surface = artifact.data.get("surface")
    try:
        return SurfaceDraft.from_dict({"surface": surface})
    except Exception as exc:
        raise SurfaceAssemblyError("Surface Draft Artifact is invalid.") from exc


def _anchor_manifest_from_artifact(artifact: Artifact) -> AnchorManifest | None:
    value = artifact.data.get("anchor_manifest")
    if value is None:
        logger.warning(
            "[COMPLETE:LEGACY_NO_ANCHOR_MANIFEST] run=%s artifact=%s",
            artifact.run_id,
            artifact.artifact_id,
        )
        return None
    try:
        return AnchorManifest.from_dict(value)
    except Exception as exc:
        raise SurfaceAssemblyError("Surface Draft anchor manifest is invalid.") from exc


def _register_media_references(
    store: Any,
    *,
    session_id: str,
    evidence_by_ref: Mapping[str, Artifact],
) -> None:
    evidence: list[dict[str, Any]] = []
    for artifact in evidence_by_ref.values():
        if artifact.artifact_type not in {"media", "generated_image"}:
            continue
        references = artifact.data.get("references", ())
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, Mapping):
                continue
            package_id = reference.get("package_id")
            if not isinstance(package_id, str) or not package_id.strip():
                continue
            payload = {
                key: reference.get(key)
                for key in (
                    "media_id", "object_key", "mime_type", "source_uri",
                    "caption", "local_file_missing",
                )
            }
            evidence.append({"package_id": package_id, "payload": payload})
    if evidence:
        store.register_worker_result(session_id=session_id, result={"evidence": evidence})


def _task_output_for_artifact(
    task: SurfacePlanTask,
    artifact: Artifact | None,
    manifest_registry: AsyncTaskManifestRegistry | None,
    resource_store: ResourceStore | None,
    resource_session_id: str | None,
) -> dict[str, Any] | None:
    """Create one provider-neutral task/output contract for Assembly.

    Static executors are projected through compatibility contracts. Manifest
    executors contribute their own output schema without exposing HTTP,
    provider, authentication or remote-job details to Assembly.
    """

    if artifact is None:
        return None
    if artifact.artifact_type == "async_task_output":
        if manifest_registry is None:
            raise SurfaceAssemblyError(
                f"Async task '{task.task_type}' completed but no manifest registry is available."
            )
        try:
            manifest = manifest_registry.get(task.task_type)
        except AsyncTaskManifestError as exc:
            raise SurfaceAssemblyError(
                f"Async task output has no manifest for task type '{task.task_type}'."
            ) from exc
        output_data = _json_value(artifact.data)
        if not output_data:
            return None
        output_contract: Mapping[str, Any] = manifest.output.schema
        if manifest.output.resources:
            if (
                not isinstance(resource_store, ResourceStore)
                or not isinstance(resource_session_id, str)
                or not resource_session_id.strip()
            ):
                raise SurfaceAssemblyError(
                    f"Async task '{task.task_type}' requires ResourceStore context."
                )
            _validate_registered_resources(
                output_data,
                artifact,
                manifest.id,
                resource_store,
                resource_session_id,
            )
            try:
                output_contract = normalized_resource_output_contract(
                    manifest.output.schema,
                    manifest.output.resources,
                )
            except ResourceStoreError as exc:
                raise SurfaceAssemblyError(str(exc)) from exc
        return _assembly_task_output(task, output_contract, output_data)

    if artifact.artifact_type == "knowledge":
        items = artifact.data.get("items")
        passages = (
            [
                projected_item
                for item in items
                if isinstance(item, Mapping)
                if (projected_item := _knowledge_item(item))
            ]
            if isinstance(items, list)
            else []
        )
        if not passages:
            return None
        return _assembly_task_output(
            task,
            _legacy_knowledge_contract(),
            {"items": passages},
        )

    if artifact.artifact_type in {"media", "generated_image"}:
        items = _media_items(artifact)
        if not items:
            return None
        return _assembly_task_output(task, _legacy_media_contract(), {"items": items})

    raise SurfaceAssemblyError(
        f"Artifact type '{artifact.artifact_type}' has no Assembly output contract."
    )


def _assembly_task_output(
    task: SurfacePlanTask,
    output_contract: Mapping[str, Any],
    output_data: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "task": task.to_dict(),
        "output_contract": _json_value(output_contract),
        "output_data": _json_value(output_data),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def _validate_registered_resources(
    output_data: Mapping[str, Any],
    artifact: Artifact,
    manifest_id: str,
    resource_store: ResourceStore,
    resource_session_id: str,
) -> None:
    resource_ids = {
        value
        for value in _values_for_key(output_data, "resource_id")
        if isinstance(value, str) and value.strip()
    }
    for resource_id in resource_ids:
        try:
            reference = resource_store.get(resource_session_id, resource_id)
        except ResourceStoreError as exc:
            raise SurfaceAssemblyError(
                f"resource_id '{resource_id}' is not committed for this Live session."
            ) from exc
        if reference.owner_task_id != artifact.produced_by:
            raise SurfaceAssemblyError(
                f"resource_id '{resource_id}' belongs to another task."
            )
        if reference.manifest_id != manifest_id:
            raise SurfaceAssemblyError(
                f"resource_id '{resource_id}' belongs to another manifest."
            )


def _values_for_key(value: object, target_key: str) -> tuple[object, ...]:
    if isinstance(value, Mapping):
        return tuple(
            child_value
            for key, child in value.items()
            for child_value in (
                (child,) if key == target_key else _values_for_key(child, target_key)
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            found
            for child in value
            for found in _values_for_key(child, target_key)
        )
    return ()


def _legacy_knowledge_contract() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Verified knowledge passages returned by the information task.",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["passage"],
                    "properties": {
                        "passage": {
                            "type": "string",
                            "description": "Full verified passage; preserve every detail when rewording.",
                        }
                    },
                },
            }
        },
    }


def _legacy_media_contract() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Trusted media references already registered by Runtime.",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["media_id"],
                    "properties": {
                        "media_id": {
                            "type": "string",
                            "description": "Trusted media identifier to copy exactly into the widget.",
                        },
                        "caption": {
                            "type": "string",
                            "description": "Description of the referenced media.",
                        },
                    },
                },
            }
        },
    }


def _knowledge_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only model-usable knowledge content, never local candidate IDs."""

    payload = item.get("payload")
    if isinstance(payload, Mapping):
        passage = payload.get("passage")
        if isinstance(passage, str) and passage.strip():
            return {"passage": passage.strip()}
    return {}


def _media_items(artifact: Artifact) -> list[dict[str, str]]:
    captions = {
        reference.get("media_id"): reference.get("caption", "")
        for reference in artifact.data.get("references", ())
        if isinstance(reference, Mapping) and isinstance(reference.get("media_id"), str)
    }
    result: list[dict[str, str]] = []
    for item in artifact.data.get("items", ()):
        if not isinstance(item, Mapping):
            continue
        media_id = item.get("media_id")
        if not isinstance(media_id, str) or not media_id.strip():
            continue
        caption = captions.get(media_id, "")
        payload: dict[str, str] = {"media_id": media_id.strip()}
        if isinstance(caption, str) and caption.strip():
            payload["caption"] = caption.strip()
        result.append(payload)
    return result


def _placeholder_warnings(compiled: CompiledSurface) -> tuple[str, ...]:
    """Runtime, rather than Assembly, owns final partial-panel warnings."""

    return tuple(
        f"Component {component.id} ({component.type}) giữ placeholder: {component.draft_placeholder}"
        for component in compiled.components
        if component.draft_placeholder
    )


def _surface_completion(compiled: CompiledSurface) -> dict[str, Any]:
    """Describe render completeness without exposing compiler diagnostics."""

    missing_parts = [
        {
            "description": component.draft_placeholder,
            "reason": "CONTENT_NOT_RENDERED",
        }
        for component in compiled.components
        if component.draft_placeholder
    ]
    return {
        "surface_status": "partial" if missing_parts else "ready",
        "missing_parts": missing_parts,
    }
