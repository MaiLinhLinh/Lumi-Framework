"""Generic HTTP executor for manifest-defined asynchronous Agent Services."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncContextManager, AsyncIterator, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from lumi_openui.agent_contracts import AgentTaskRequestFactory
from lumi_openui.knowledge import (
    KNOWLEDGE_TOKEN_HEADER,
    KnowledgeAccessContext,
    KnowledgeGrantRegistry,
)
from lumi_openui.task_manager import Artifact, TaskExecutionContext, TaskResult

from .jsonpath import CompiledJsonPath, resolve_json_path
from .manifest import AsyncTaskManifest
from .remote_jobs import RemoteJobRecord, RemoteJobRegistry, RemoteJobRegistryError
from .resources import (
    ResourceBatchLifecycle,
    ResourceReference,
    ResourceStore,
    resource_kind,
)
from .templates import render_input_template, render_job_url


class AsyncTaskExecutionError(RuntimeError):
    """A manifest task could not produce one validated final output."""


class AsyncTaskHttpError(AsyncTaskExecutionError):
    """An Agent Service request failed before a valid response was available."""


class AsyncTaskResponseError(AsyncTaskExecutionError):
    """An Agent Service response violates its manifest contract."""


class AsyncTaskTimeoutError(AsyncTaskExecutionError):
    """The complete remote submit-and-poll lifecycle exceeded its timeout."""


class RemoteAsyncTaskError(AsyncTaskExecutionError):
    """The Agent Service reported a failed or cancelled terminal job."""


class AsyncHttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def aiter_bytes(self, chunk_size: int | None = None) -> AsyncIterator[bytes]: ...


class AsyncHttpClient(Protocol):
    def stream(
        self, method: str, url: str, **kwargs: Any
    ) -> AsyncContextManager[AsyncHttpResponse]: ...


@dataclass(frozen=True, slots=True)
class _BufferedHttpResponse:
    status_code: int
    content: bytes


@dataclass(frozen=True, slots=True)
class AsyncTaskExecutionLimits:
    """Framework-owned safety limits supplied from Settings, never a manifest."""

    max_job_id_chars: int
    max_response_bytes: int
    max_json_depth: int
    max_collection_items: int
    cancel_timeout_seconds: float

    def __post_init__(self) -> None:
        for name in (
            "max_job_id_chars",
            "max_response_bytes",
            "max_json_depth",
            "max_collection_items",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        timeout = self.cancel_timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            raise ValueError("cancel_timeout_seconds must be a positive finite number.")
        object.__setattr__(self, "cancel_timeout_seconds", float(timeout))

    @classmethod
    def from_settings(cls, settings: object) -> "AsyncTaskExecutionLimits":
        """Build limits from the framework Settings object at composition time."""

        return cls(
            max_job_id_chars=getattr(settings, "async_task_max_job_id_chars"),
            max_response_bytes=getattr(settings, "async_task_max_response_bytes"),
            max_json_depth=getattr(settings, "async_task_max_json_depth"),
            max_collection_items=getattr(settings, "async_task_max_collection_items"),
            cancel_timeout_seconds=getattr(
                settings, "async_task_cancel_timeout_seconds"
            ),
        )


class GenericHttpAsyncAgentExecutor:
    # AgentTaskRequest/input templates contain no Surface bindings.
    surface_binding_independent = True
    """Execute one manifest-defined Async Agent Task through its remote lifecycle."""

    def __init__(
        self,
        manifest: AsyncTaskManifest,
        remote_jobs: RemoteJobRegistry,
        http_client: AsyncHttpClient,
        limits: AsyncTaskExecutionLimits,
        *,
        resource_store: ResourceStore | None = None,
        resource_session_id: str | None = None,
        resource_id_factory: Callable[[], str] | None = None,
        request_factory: AgentTaskRequestFactory | None = None,
        knowledge_catalog: tuple[Mapping[str, Any], ...] = (),
        knowledge_grant_registry: KnowledgeGrantRegistry | None = None,
        environment: Mapping[str, str] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(manifest, AsyncTaskManifest):
            raise TypeError("manifest must be an AsyncTaskManifest.")
        if manifest.output.resources and (
            not isinstance(resource_store, ResourceStore)
            or not isinstance(resource_session_id, str)
            or not resource_session_id.strip()
        ):
            raise AsyncTaskExecutionError(
                "resource manifests require ResourceStore and resource_session_id."
            )
        if not isinstance(remote_jobs, RemoteJobRegistry):
            raise TypeError("remote_jobs must implement RemoteJobRegistry.")
        if not callable(getattr(http_client, "stream", None)):
            raise TypeError("http_client must provide stream(method, url, ...).")
        if not isinstance(limits, AsyncTaskExecutionLimits):
            raise TypeError("limits must be AsyncTaskExecutionLimits.")
        if not callable(sleep):
            raise TypeError("sleep must be callable.")
        if resource_id_factory is not None and not callable(resource_id_factory):
            raise TypeError("resource_id_factory must be callable when provided.")
        if request_factory is not None and not isinstance(
            request_factory, AgentTaskRequestFactory
        ):
            raise TypeError("request_factory must be an AgentTaskRequestFactory.")
        if not isinstance(knowledge_catalog, tuple):
            raise TypeError("knowledge_catalog must be a tuple.")
        if knowledge_grant_registry is not None and not isinstance(
            knowledge_grant_registry, KnowledgeGrantRegistry
        ):
            raise TypeError(
                "knowledge_grant_registry must be a KnowledgeGrantRegistry."
            )
        if manifest.knowledge.allowed_tools and knowledge_grant_registry is None:
            raise AsyncTaskExecutionError(
                "knowledge-enabled manifests require KnowledgeGrantRegistry."
            )
        self._manifest = manifest
        self._remote_jobs = remote_jobs
        self._http = http_client
        self._limits = limits
        self._resource_store = resource_store
        self._resource_session_id = (
            resource_session_id.strip() if isinstance(resource_session_id, str) else None
        )
        self._resource_id_factory = resource_id_factory or (
            lambda: f"resource_{uuid.uuid4().hex}"
        )
        self._request_factory = request_factory or AgentTaskRequestFactory()
        self._knowledge_catalog = knowledge_catalog
        self._knowledge_grant_registry = knowledge_grant_registry
        self._environment = os.environ if environment is None else environment
        self._sleep = sleep
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._remote_state_map = {
            remote_state: canonical
            for canonical, remote_states in manifest.executor.status.states.items()
            for remote_state in remote_states
        }

    async def __call__(self, context: TaskExecutionContext) -> TaskResult:
        self._validate_context(context)
        try:
            return await asyncio.wait_for(
                self._execute(context),
                timeout=self._manifest.executor.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._cancel_after_interruption(context)
            raise AsyncTaskTimeoutError(
                f"Async Agent Task '{self._manifest.id}' exceeded "
                f"{self._manifest.executor.timeout_seconds:g} seconds."
            ) from exc
        except asyncio.CancelledError:
            await self._cancel_after_interruption(context)
            raise

    async def _execute(self, context: TaskExecutionContext) -> TaskResult:
        task_input = render_input_template(
            self._manifest.input.payload,
            self._template_variables(context),
        )
        if not isinstance(task_input, dict):
            raise AsyncTaskExecutionError("rendered input payload must be a JSON object.")
        self._validate_json_limits(task_input, "rendered input payload")
        self._validate_schema(task_input, self._manifest.input.schema, "input payload")
        request = self._request_factory.build(
            context,
            task_input=task_input,
            knowledge_catalog=self._knowledge_catalog,
            allowed_tools=self._manifest.knowledge.allowed_tools,
        )
        access_context = self._issue_knowledge_access(context)
        try:
            return await self._submit_and_poll(
                context,
                request.to_dict(),
                access_context,
            )
        finally:
            if access_context is not None:
                assert self._knowledge_grant_registry is not None
                self._knowledge_grant_registry.revoke(access_context)

    async def _submit_and_poll(
        self,
        context: TaskExecutionContext,
        payload: Mapping[str, Any],
        access_context: KnowledgeAccessContext | None,
    ) -> TaskResult:
        submit = self._manifest.executor.submit
        submit_response = await self._request_json(
            submit.method,
            submit.url,
            payload=payload,
            knowledge_access=access_context,
        )
        job_id = self._required_string(
            submit_response,
            submit.job_id_path,
            "submit job_id_path",
        )
        if len(job_id) > self._limits.max_job_id_chars:
            raise AsyncTaskResponseError(
                "remote job_id exceeds ASYNC_TASK_MAX_JOB_ID_CHARS."
            )
        submitted_at = self._utcnow()
        if submitted_at.tzinfo is None or submitted_at.utcoffset() is None:
            raise AsyncTaskExecutionError("utcnow must return a timezone-aware datetime.")
        await self._remote_jobs.register(RemoteJobRecord(
            run_id=context.run_id,
            task_id=context.task.task_id,
            attempt=context.task.attempt,
            manifest_id=self._manifest.id,
            job_id=job_id,
            normalized_remote_state="queued",
            last_remote_state="submitted",
            submitted_at=submitted_at,
        ))

        status_definition = self._manifest.executor.status
        status_url = render_job_url(
            status_definition.url,
            job_id,
            max_chars=self._limits.max_job_id_chars,
        )
        while True:
            status_response = await self._request_json(
                status_definition.method,
                status_url,
            )
            remote_state = self._required_string(
                status_response,
                status_definition.state_path,
                "status state_path",
            )
            try:
                normalized_state = self._remote_state_map[remote_state]
            except KeyError as exc:
                raise AsyncTaskResponseError(
                    f"remote state '{remote_state}' is not mapped by the manifest."
                ) from exc

            if normalized_state in {"queued", "running"}:
                await self._remote_jobs.update_state(
                    context.run_id,
                    context.task.task_id,
                    context.task.attempt,
                    normalized_state,
                    remote_state,
                )
                await self._sleep(self._manifest.executor.poll_interval_seconds)
                continue

            await self._remote_jobs.mark_terminal(
                context.run_id,
                context.task.task_id,
                context.task.attempt,
                normalized_state,
                remote_state,
            )
            if normalized_state != "succeeded":
                detail = self._remote_error(status_response)
                suffix = f": {detail}" if detail else ""
                raise RemoteAsyncTaskError(
                    f"remote job entered terminal state '{normalized_state}'{suffix}"
                )

            output = self._required_object(
                status_response,
                self._manifest.output.path,
                "output.path",
            )
            self._validate_schema(output, self._manifest.output.schema, "output")
            normalized_output, lifecycle, metrics = self._normalize_resources(output, context)
            try:
                artifact = Artifact(
                    artifact_id=f"async_task_output_{context.task.task_id}",
                    artifact_type="async_task_output",
                    run_id=context.run_id,
                    produced_by=context.task.task_id,
                    evidence_ref=context.task.evidence_ref,
                    data=normalized_output,
                )
                return TaskResult(
                    task_id=context.task.task_id,
                    status="succeeded",
                    artifact=artifact,
                    evidence_ref=context.task.evidence_ref,
                    metrics=metrics,
                    lifecycle=lifecycle,
                )
            except Exception:
                if lifecycle is not None:
                    lifecycle.rollback()
                raise

    def _issue_knowledge_access(
        self,
        context: TaskExecutionContext,
    ) -> KnowledgeAccessContext | None:
        allowed_tools = self._manifest.knowledge.allowed_tools
        if not allowed_tools:
            return None
        assert self._knowledge_grant_registry is not None
        package_ids = tuple(
            entry["package_id"] for entry in self._knowledge_catalog
        )
        return self._knowledge_grant_registry.issue(
            run_id=context.run_id,
            task_id=context.task.task_id,
            allowed_tools=allowed_tools,
            allowed_package_ids=package_ids,
        )

    def _normalize_resources(
        self,
        output: Mapping[str, Any],
        context: TaskExecutionContext,
    ) -> tuple[dict[str, Any], ResourceBatchLifecycle | None, dict[str, Any]]:
        rules = self._manifest.output.resources
        if not rules:
            return dict(output), None, {
                "usable_resource_count": 0,
                "usable_resource_kinds": [],
            }
        assert self._resource_store is not None
        assert self._resource_session_id is not None

        normalized = copy.deepcopy(dict(output))
        references: list[ResourceReference] = []
        selected_items: list[tuple[dict[str, Any], CompiledJsonPath]] = []
        selected_item_ids: set[int] = set()
        resource_ids: set[str] = set()
        for rule in rules:
            for item in resolve_json_path(normalized, rule.items_path):
                if not isinstance(item, dict):
                    raise AsyncTaskResponseError(
                        "output.resources items_path must resolve to JSON objects."
                    )
                if id(item) in selected_item_ids:
                    raise AsyncTaskResponseError(
                        "one output item may be selected by only one resource rule."
                    )
                if "resource_id" in item:
                    raise AsyncTaskResponseError(
                        "remote output must not predefine framework resource_id."
                    )
                uri = self._required_string(item, rule.uri_path, "resource uri_path")
                mime_type = self._required_string(
                    item,
                    rule.mime_type_path,
                    "resource mime_type_path",
                ).lower()
                caption = ""
                if rule.caption_path is not None:
                    captions = resolve_json_path(item, rule.caption_path)
                    if len(captions) > 1 or (
                        captions and not isinstance(captions[0], str)
                    ):
                        raise AsyncTaskResponseError(
                            "resource caption_path must resolve to no value or one string."
                        )
                    if captions:
                        caption = captions[0].strip()
                resource_id = self._resource_id_factory()
                if (
                    not isinstance(resource_id, str)
                    or not resource_id.strip()
                    or resource_id in resource_ids
                ):
                    raise AsyncTaskExecutionError(
                        "resource_id_factory must return unique non-empty strings."
                    )
                resource_id = resource_id.strip()
                resource_ids.add(resource_id)
                references.append(ResourceReference(
                    resource_id=resource_id,
                    owner_task_id=context.task.task_id,
                    attempt=context.task.attempt,
                    manifest_id=self._manifest.id,
                    kind=resource_kind(mime_type),
                    mime_type=mime_type,
                    source_uri=uri,
                    caption=caption,
                ))
                selected_items.append((item, rule.uri_path))
                selected_item_ids.add(id(item))

        if not references:
            return normalized, None, {
                "usable_resource_count": 0,
                "usable_resource_kinds": [],
            }

        batch = tuple(references)
        self._resource_store.register_batch(self._resource_session_id, batch)
        try:
            for (item, uri_path), reference in zip(
                selected_items,
                references,
                strict=True,
            ):
                _delete_json_path(item, uri_path)
                item["resource_id"] = reference.resource_id
        except Exception:
            self._resource_store.discard_batch(
                self._resource_session_id,
                context.task.task_id,
                context.task.attempt,
            )
            raise

        lifecycle = ResourceBatchLifecycle(
            self._resource_store,
            session_id=self._resource_session_id,
            owner_task_id=context.task.task_id,
            attempt=context.task.attempt,
        )
        return normalized, lifecycle, {
            "usable_resource_count": len(references),
            "usable_resource_kinds": sorted({reference.kind for reference in references}),
        }

    def _validate_context(self, context: TaskExecutionContext) -> None:
        if not isinstance(context, TaskExecutionContext):
            raise TypeError("context must be a TaskExecutionContext.")
        if context.task.task_type != self._manifest.id:
            raise AsyncTaskExecutionError(
                f"manifest '{self._manifest.id}' cannot execute task type "
                f"'{context.task.task_type}'."
            )
        if context.task.evidence_ref is None:
            raise AsyncTaskExecutionError("Async Agent Task requires evidence_ref.")

    @staticmethod
    def _template_variables(context: TaskExecutionContext) -> dict[str, Any]:
        return {
            "task.instruction": context.task.instruction,
            "task.task_id": context.task.task_id,
            "task.task_type": context.task.task_type,
            "task.evidence_ref": context.task.evidence_ref,
            "run.run_id": context.run_id,
        }

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        timeout: float | None = None,
        knowledge_access: KnowledgeAccessContext | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            method,
            url,
            payload=payload,
            timeout=timeout,
            knowledge_access=knowledge_access,
        )
        return self._decode_json_response(response)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        timeout: float | None = None,
        knowledge_access: KnowledgeAccessContext | None = None,
    ) -> _BufferedHttpResponse:
        kwargs: dict[str, Any] = {
            "headers": self._headers(knowledge_access),
            "timeout": timeout or self._manifest.executor.timeout_seconds,
            "follow_redirects": False,
        }
        if payload is not None:
            kwargs["json"] = dict(payload)
        try:
            async with self._http.stream(method, url, **kwargs) as response:
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, bool) or not isinstance(status_code, int):
                    raise AsyncTaskHttpError(
                        "Async Agent Service response has no HTTP status code."
                    )
                if not 200 <= status_code < 300:
                    raise AsyncTaskHttpError(
                        f"Async Agent Service returned HTTP {status_code}."
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise AsyncTaskResponseError(
                            "HTTP response Content-Length must be an integer."
                        ) from exc
                    if declared_size < 0:
                        raise AsyncTaskResponseError(
                            "HTTP response Content-Length must not be negative."
                        )
                    if declared_size > self._limits.max_response_bytes:
                        raise AsyncTaskResponseError(
                            "HTTP response exceeds ASYNC_TASK_MAX_RESPONSE_BYTES."
                        )
                content = bytearray()
                chunk_size = min(self._limits.max_response_bytes + 1, 64 * 1024)
                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    content.extend(chunk)
                    if len(content) > self._limits.max_response_bytes:
                        raise AsyncTaskResponseError(
                            "HTTP response exceeds ASYNC_TASK_MAX_RESPONSE_BYTES."
                        )
                return _BufferedHttpResponse(status_code, bytes(content))
        except asyncio.CancelledError:
            raise
        except AsyncTaskExecutionError:
            raise
        except Exception as exc:
            raise AsyncTaskHttpError(
                f"{method} request to Async Agent Service failed: {type(exc).__name__}."
            ) from exc

    def _headers(
        self,
        knowledge_access: KnowledgeAccessContext | None = None,
    ) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        auth = self._manifest.executor.auth
        if auth is not None:
            token = self._environment.get(auth.env, "").strip()
            if not token:
                raise AsyncTaskExecutionError(
                    f"required Async Agent Service credential '{auth.env}' is not configured."
                )
            headers["Authorization"] = f"Bearer {token}"
        if knowledge_access is not None:
            headers[KNOWLEDGE_TOKEN_HEADER] = knowledge_access.transport_token
        return headers

    def _decode_json_response(self, response: _BufferedHttpResponse) -> dict[str, Any]:
        content = getattr(response, "content", None)
        if not isinstance(content, bytes):
            raise AsyncTaskResponseError("HTTP response content must be bytes.")
        if len(content) > self._limits.max_response_bytes:
            raise AsyncTaskResponseError(
                "HTTP response exceeds ASYNC_TASK_MAX_RESPONSE_BYTES."
            )
        try:
            value = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_json_number,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            AsyncTaskResponseError,
        ) as exc:
            raise AsyncTaskResponseError("HTTP response must contain valid UTF-8 JSON.") from exc
        if not isinstance(value, dict):
            raise AsyncTaskResponseError("HTTP response JSON root must be an object.")
        self._validate_json_limits(value, "HTTP response")
        return value

    def _validate_json_limits(self, value: object, field: str) -> None:
        pending: list[tuple[object, int]] = [(value, 1)]
        while pending:
            current, depth = pending.pop()
            if depth > self._limits.max_json_depth:
                raise AsyncTaskResponseError(
                    f"{field} exceeds ASYNC_TASK_MAX_JSON_DEPTH."
                )
            if isinstance(current, Mapping):
                if len(current) > self._limits.max_collection_items:
                    raise AsyncTaskResponseError(
                        f"{field} object exceeds ASYNC_TASK_MAX_COLLECTION_ITEMS."
                    )
                pending.extend((child, depth + 1) for child in current.values())
            elif isinstance(current, list):
                if len(current) > self._limits.max_collection_items:
                    raise AsyncTaskResponseError(
                        f"{field} array exceeds ASYNC_TASK_MAX_COLLECTION_ITEMS."
                    )
                pending.extend((child, depth + 1) for child in current)

    @staticmethod
    def _validate_schema(
        value: Mapping[str, Any],
        schema: Mapping[str, Any],
        field: str,
    ) -> None:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        if not errors:
            return
        error = errors[0]
        path = ".".join(str(part) for part in error.path)
        location = f" at '{path}'" if path else ""
        raise AsyncTaskResponseError(
            f"{field} does not match its manifest schema{location}: {error.message}"
        )

    @staticmethod
    def _required_string(
        document: object,
        path: CompiledJsonPath,
        field: str,
    ) -> str:
        values = resolve_json_path(document, path)
        if len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
            raise AsyncTaskResponseError(
                f"{field} must resolve to exactly one non-empty string."
            )
        return values[0]

    @staticmethod
    def _required_object(
        document: object,
        path: CompiledJsonPath,
        field: str,
    ) -> dict[str, Any]:
        values = resolve_json_path(document, path)
        if len(values) != 1 or not isinstance(values[0], Mapping):
            raise AsyncTaskResponseError(
                f"{field} must resolve to exactly one JSON object."
            )
        return dict(values[0])

    def _remote_error(self, document: Mapping[str, Any]) -> str | None:
        path = self._manifest.executor.status.error_path
        if path is None:
            return None
        values = resolve_json_path(document, path)
        if not values:
            return None
        if len(values) != 1 or isinstance(values[0], (Mapping, list)):
            raise AsyncTaskResponseError(
                "status error_path must resolve to no value or one scalar."
            )
        if values[0] is None:
            return None
        return str(values[0])

    async def _cancel_after_interruption(self, context: TaskExecutionContext) -> None:
        try:
            await asyncio.wait_for(
                self._best_effort_cancel(context),
                timeout=self._limits.cancel_timeout_seconds,
            )
        except TimeoutError:
            await self._record_cancel_error(context, "remote cancel timed out")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._record_cancel_error(
                context,
                f"remote cancel failed: {type(exc).__name__}",
            )

    async def _best_effort_cancel(self, context: TaskExecutionContext) -> None:
        cancel = self._manifest.executor.cancel
        if cancel is None:
            return
        record = await self._remote_jobs.get(context.run_id, context.task.task_id)
        if record is None or record.attempt != context.task.attempt or record.terminal:
            return
        await self._remote_jobs.mark_cancel_requested(
            context.run_id,
            context.task.task_id,
            context.task.attempt,
        )
        cancel_url = render_job_url(
            cancel.url,
            record.job_id,
            max_chars=self._limits.max_job_id_chars,
        )
        response = await self._request(
            cancel.method,
            cancel_url,
            timeout=self._limits.cancel_timeout_seconds,
        )
        if not response.content:
            return
        document = self._decode_json_response(response)
        values = resolve_json_path(document, self._manifest.executor.status.state_path)
        if len(values) != 1 or not isinstance(values[0], str):
            return
        if self._remote_state_map.get(values[0]) == "cancelled":
            await self._remote_jobs.mark_terminal(
                context.run_id,
                context.task.task_id,
                context.task.attempt,
                "cancelled",
                values[0],
            )

    async def _record_cancel_error(
        self,
        context: TaskExecutionContext,
        error: str,
    ) -> None:
        try:
            record = await self._remote_jobs.get(context.run_id, context.task.task_id)
            if (
                record is None
                or record.attempt != context.task.attempt
                or record.cancel_requested_at is None
            ):
                return
            await self._remote_jobs.record_cancel_error(
                context.run_id,
                context.task.task_id,
                context.task.attempt,
                error,
            )
        except RemoteJobRegistryError:
            return


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AsyncTaskResponseError(f"duplicate JSON field '{key}'.")
        result[key] = value
    return result


def _delete_json_path(document: object, path: CompiledJsonPath) -> None:
    """Delete one already-validated relative URI path from a resource item."""

    if not path.segments:
        raise AsyncTaskResponseError("resource uri_path may not select the whole item.")
    current = document
    for segment in path.segments[:-1]:
        if segment.kind == "field" and isinstance(current, dict):
            current = current.get(segment.value)
        elif (
            segment.kind == "index"
            and isinstance(current, list)
            and isinstance(segment.value, int)
            and segment.value < len(current)
        ):
            current = current[segment.value]
        else:
            raise AsyncTaskResponseError("resource uri_path cannot be removed safely.")
    leaf = path.segments[-1]
    if leaf.kind == "field" and isinstance(current, dict) and leaf.value in current:
        del current[leaf.value]
        return
    if (
        leaf.kind == "index"
        and isinstance(current, list)
        and isinstance(leaf.value, int)
        and leaf.value < len(current)
    ):
        del current[leaf.value]
        return
    raise AsyncTaskResponseError("resource uri_path cannot be removed safely.")


def _reject_non_json_number(value: str) -> None:
    raise AsyncTaskResponseError(f"non-JSON numeric constant '{value}' is not allowed.")
