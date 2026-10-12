"""Strict manifest contract and independent registry for Async Agent Tasks."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError

from final_framework.agent_tasks.tool_registry import AgentToolRegistryError
from final_framework.knowledge.tools import KNOWLEDGE_TOOL_REGISTRY

from .jsonpath import AsyncTaskJsonPathError, CompiledJsonPath, compile_json_path
from .templates import (
    AsyncTaskTemplateError,
    validate_endpoint_url_template,
    validate_input_template,
)
from .resources import ResourceStoreError, normalized_resource_output_contract


_TASK_ID = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_CANONICAL_STATES = ("queued", "running", "succeeded", "failed", "cancelled")


class AsyncTaskManifestError(ValueError):
    """A manifest package cannot safely enter the Async Task Registry."""


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


@dataclass(frozen=True, slots=True)
class AsyncTaskInputDefinition:
    payload: Mapping[str, Any]
    schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AsyncTaskKnowledgeDefinition:
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AsyncTaskSubmitDefinition:
    url: str
    method: str
    job_id_path: CompiledJsonPath


@dataclass(frozen=True, slots=True)
class AsyncTaskStatusDefinition:
    url: str
    method: str
    state_path: CompiledJsonPath
    states: Mapping[str, tuple[str, ...]]
    error_path: CompiledJsonPath | None


@dataclass(frozen=True, slots=True)
class AsyncTaskCancelDefinition:
    url: str
    method: str


@dataclass(frozen=True, slots=True)
class AsyncTaskAuthDefinition:
    type: str
    env: str


@dataclass(frozen=True, slots=True)
class AsyncTaskExecutorDefinition:
    type: str
    submit: AsyncTaskSubmitDefinition
    status: AsyncTaskStatusDefinition
    cancel: AsyncTaskCancelDefinition | None
    auth: AsyncTaskAuthDefinition | None
    poll_interval_seconds: float
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class AsyncTaskResourceDefinition:
    items_path: CompiledJsonPath
    uri_path: CompiledJsonPath
    mime_type_path: CompiledJsonPath
    caption_path: CompiledJsonPath | None


@dataclass(frozen=True, slots=True)
class AsyncTaskOutputDefinition:
    path: CompiledJsonPath
    schema: Mapping[str, Any]
    resources: tuple[AsyncTaskResourceDefinition, ...]


@dataclass(frozen=True, slots=True)
class AsyncTaskManifest:
    id: str
    description: str
    knowledge: AsyncTaskKnowledgeDefinition
    input: AsyncTaskInputDefinition
    executor: AsyncTaskExecutorDefinition
    output: AsyncTaskOutputDefinition
    package_root: Path

    def catalog_entry(self) -> dict[str, str]:
        return {"task_type": self.id, "description": self.description}


class AsyncTaskManifestRegistry:
    """Immutable-definition registry kept independent from production Runtime in CP1."""

    def __init__(self, definitions: tuple[AsyncTaskManifest, ...] = ()) -> None:
        self._definitions: dict[str, AsyncTaskManifest] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: AsyncTaskManifest) -> None:
        if not isinstance(definition, AsyncTaskManifest):
            raise TypeError("Async Task Registry accepts AsyncTaskManifest values only.")
        if definition.id in self._definitions:
            raise AsyncTaskManifestError(f"duplicate Async Task manifest id '{definition.id}'.")
        self._definitions[definition.id] = definition

    def get(self, task_type: str) -> AsyncTaskManifest:
        try:
            return self._definitions[task_type]
        except KeyError as exc:
            raise AsyncTaskManifestError(f"unknown Async Agent Task '{task_type}'.") from exc

    def definitions(self) -> tuple[AsyncTaskManifest, ...]:
        return tuple(self._definitions.values())

    def task_types(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def public_catalog(self) -> tuple[dict[str, str], ...]:
        return tuple(definition.catalog_entry() for definition in self._definitions.values())


class AsyncTaskManifestLoader:
    """Load ``extensions/tasks/*/manifest.json`` without wiring task execution."""

    def __init__(self, tasks_root: Path) -> None:
        self._root = tasks_root.resolve()

    @property
    def tasks_root(self) -> Path:
        return self._root

    def load(self) -> AsyncTaskManifestRegistry:
        self._root.mkdir(parents=True, exist_ok=True)
        invalid = sorted(path.name for path in self._root.iterdir() if not path.is_dir())
        if invalid:
            raise AsyncTaskManifestError(
                f"Async Task root '{self._root}' may contain package directories only: {invalid}."
            )
        definitions = tuple(
            self._load_package(path)
            for path in sorted(self._root.iterdir(), key=lambda candidate: candidate.name)
        )
        return AsyncTaskManifestRegistry(definitions)

    def _load_package(self, package_root: Path) -> AsyncTaskManifest:
        manifest_path = package_root / "manifest.json"
        try:
            raw = json.loads(
                manifest_path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_non_json_number,
            )
        except (OSError, json.JSONDecodeError, AsyncTaskManifestError) as exc:
            raise AsyncTaskManifestError(
                f"cannot load Async Task manifest '{manifest_path}': {exc}"
            ) from exc
        manifest = _require_object(raw, "manifest")
        _require_fields(
            manifest,
            "manifest",
            {"id", "description", "knowledge", "input", "executor", "output"},
        )

        task_id = _task_id(manifest["id"], "manifest.id")
        directory_id = _task_id(package_root.name, "Async Task package directory")
        if task_id != directory_id:
            raise AsyncTaskManifestError(
                f"manifest.id '{task_id}' must match package directory '{directory_id}'."
            )
        description = _text(manifest["description"], "manifest.description")
        try:
            knowledge_definition = _parse_knowledge(manifest["knowledge"])
            input_definition = _parse_input(manifest["input"])
            executor_definition = _parse_executor(manifest["executor"])
            output_definition = _parse_output(manifest["output"])
        except (AsyncTaskJsonPathError, AsyncTaskTemplateError) as exc:
            raise AsyncTaskManifestError(str(exc)) from exc
        return AsyncTaskManifest(
            id=task_id,
            description=description,
            knowledge=knowledge_definition,
            input=input_definition,
            executor=executor_definition,
            output=output_definition,
            package_root=package_root.resolve(),
        )


def _parse_knowledge(value: object) -> AsyncTaskKnowledgeDefinition:
    raw = _require_object(value, "manifest.knowledge")
    _require_fields(raw, "manifest.knowledge", {"allowed_tools"})
    allowed_tools = raw["allowed_tools"]
    if not isinstance(allowed_tools, list):
        raise AsyncTaskManifestError(
            "manifest.knowledge.allowed_tools must be an array."
        )
    normalized = tuple(
        _text(item, "manifest.knowledge.allowed_tools[]")
        for item in allowed_tools
    )
    if len(normalized) != len(set(normalized)):
        raise AsyncTaskManifestError(
            "manifest.knowledge.allowed_tools must not contain duplicates."
        )
    try:
        KNOWLEDGE_TOOL_REGISTRY.validate_grants(normalized)
    except AgentToolRegistryError as exc:
        raise AsyncTaskManifestError(
            f"manifest.knowledge.allowed_tools: {exc}"
        ) from exc
    return AsyncTaskKnowledgeDefinition(normalized)


def _parse_input(value: object) -> AsyncTaskInputDefinition:
    raw = _require_object(value, "manifest.input")
    _require_fields(raw, "manifest.input", {"payload", "schema"})
    payload = _require_object(raw["payload"], "manifest.input.payload")
    validate_input_template(payload)
    schema = _validate_schema(raw["schema"], "manifest.input.schema")
    return AsyncTaskInputDefinition(_freeze_json(payload), _freeze_json(schema))


def _parse_executor(value: object) -> AsyncTaskExecutorDefinition:
    raw = _require_object(value, "manifest.executor")
    _require_fields(
        raw,
        "manifest.executor",
        {"type", "submit", "status", "poll_interval_seconds", "timeout_seconds"},
        {"cancel", "auth"},
    )
    if raw["type"] != "http_async_agent":
        raise AsyncTaskManifestError("manifest.executor.type must be 'http_async_agent'.")

    submit_raw = _require_object(raw["submit"], "manifest.executor.submit")
    _require_fields(submit_raw, "manifest.executor.submit", {"url", "method", "job_id_path"})
    submit = AsyncTaskSubmitDefinition(
        validate_endpoint_url_template(
            submit_raw["url"], field="manifest.executor.submit.url", require_job_id=False
        ),
        _http_method(submit_raw["method"], "manifest.executor.submit.method"),
        compile_json_path(
            submit_raw["job_id_path"],
            field="manifest.executor.submit.job_id_path",
            cardinality="single",
        ),
    )

    status_raw = _require_object(raw["status"], "manifest.executor.status")
    _require_fields(
        status_raw,
        "manifest.executor.status",
        {"url", "method", "state_path", "states"},
        {"error_path"},
    )
    status = AsyncTaskStatusDefinition(
        validate_endpoint_url_template(
            status_raw["url"], field="manifest.executor.status.url", require_job_id=True
        ),
        _http_method(status_raw["method"], "manifest.executor.status.method"),
        compile_json_path(
            status_raw["state_path"],
            field="manifest.executor.status.state_path",
            cardinality="single",
        ),
        _parse_states(status_raw["states"]),
        compile_json_path(
            status_raw["error_path"],
            field="manifest.executor.status.error_path",
            cardinality="single",
        )
        if "error_path" in status_raw
        else None,
    )

    cancel = None
    if "cancel" in raw:
        cancel_raw = _require_object(raw["cancel"], "manifest.executor.cancel")
        _require_fields(cancel_raw, "manifest.executor.cancel", {"url", "method"})
        cancel = AsyncTaskCancelDefinition(
            validate_endpoint_url_template(
                cancel_raw["url"], field="manifest.executor.cancel.url", require_job_id=True
            ),
            _http_method(cancel_raw["method"], "manifest.executor.cancel.method"),
        )

    auth = None
    if "auth" in raw:
        auth_raw = _require_object(raw["auth"], "manifest.executor.auth")
        _require_fields(auth_raw, "manifest.executor.auth", {"type", "env"})
        if auth_raw["type"] != "bearer_env":
            raise AsyncTaskManifestError("manifest.executor.auth.type must be 'bearer_env'.")
        env = _text(auth_raw["env"], "manifest.executor.auth.env")
        if not _ENV_NAME.fullmatch(env):
            raise AsyncTaskManifestError(
                "manifest.executor.auth.env must name an environment variable."
            )
        auth = AsyncTaskAuthDefinition("bearer_env", env)

    poll_interval = _positive_number(
        raw["poll_interval_seconds"], "manifest.executor.poll_interval_seconds"
    )
    timeout = _positive_number(raw["timeout_seconds"], "manifest.executor.timeout_seconds")
    if timeout < poll_interval:
        raise AsyncTaskManifestError(
            "manifest.executor.timeout_seconds must be greater than or equal to poll_interval_seconds."
        )
    return AsyncTaskExecutorDefinition(
        "http_async_agent", submit, status, cancel, auth, poll_interval, timeout
    )


def _parse_output(value: object) -> AsyncTaskOutputDefinition:
    raw = _require_object(value, "manifest.output")
    _require_fields(raw, "manifest.output", {"path", "schema"}, {"resources"})
    output_path = compile_json_path(
        raw["path"], field="manifest.output.path", cardinality="single"
    )
    schema = _validate_schema(raw["schema"], "manifest.output.schema")
    resources_raw = raw.get("resources", [])
    if not isinstance(resources_raw, list):
        raise AsyncTaskManifestError("manifest.output.resources must be an array.")
    resources: list[AsyncTaskResourceDefinition] = []
    for index, value in enumerate(resources_raw):
        field = f"manifest.output.resources[{index}]"
        resource = _require_object(value, field)
        _require_fields(
            resource,
            field,
            {"items_path", "uri_path", "mime_type_path"},
            {"caption_path"},
        )
        resources.append(
            AsyncTaskResourceDefinition(
                compile_json_path(
                    resource["items_path"], field=f"{field}.items_path", cardinality="many"
                ),
                compile_json_path(
                    resource["uri_path"], field=f"{field}.uri_path", cardinality="single"
                ),
                compile_json_path(
                    resource["mime_type_path"],
                    field=f"{field}.mime_type_path",
                    cardinality="single",
                ),
                compile_json_path(
                    resource["caption_path"],
                    field=f"{field}.caption_path",
                    cardinality="single",
                )
                if "caption_path" in resource
                else None,
            )
        )
    definition = AsyncTaskOutputDefinition(
        output_path,
        _freeze_json(schema),
        tuple(resources),
    )
    try:
        normalized_resource_output_contract(definition.schema, definition.resources)
    except ResourceStoreError as exc:
        raise AsyncTaskManifestError(f"manifest.output.resources: {exc}") from exc
    return definition


def _validate_schema(value: object, field: str) -> Mapping[str, Any]:
    schema = _require_object(value, field)
    if schema.get("type") != "object":
        raise AsyncTaskManifestError(f"{field} root type must be 'object'.")
    _reject_remote_refs(schema, field)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker())
    except SchemaError as exc:
        raise AsyncTaskManifestError(f"{field} is not valid Draft 2020-12 JSON Schema: {exc.message}") from exc
    return schema


def _reject_remote_refs(value: object, field: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_field = f"{field}.{key}"
            if key == "$ref":
                if not isinstance(child, str) or not child.startswith("#/$defs/"):
                    raise AsyncTaskManifestError(
                        f"{child_field} must be a local reference under '#/$defs/'."
                    )
            _reject_remote_refs(child, child_field)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_remote_refs(child, f"{field}[{index}]")


def _parse_states(value: object) -> Mapping[str, tuple[str, ...]]:
    raw = _require_object(value, "manifest.executor.status.states")
    _require_fields(raw, "manifest.executor.status.states", set(_CANONICAL_STATES))
    normalized: dict[str, tuple[str, ...]] = {}
    owner_by_remote_state: dict[str, str] = {}
    for canonical in _CANONICAL_STATES:
        remote_values = raw[canonical]
        if not isinstance(remote_values, list) or not remote_values:
            raise AsyncTaskManifestError(
                f"manifest.executor.status.states.{canonical} must be a non-empty array."
            )
        values = tuple(
            _text(item, f"manifest.executor.status.states.{canonical}")
            for item in remote_values
        )
        if len(values) != len(set(values)):
            raise AsyncTaskManifestError(
                f"manifest.executor.status.states.{canonical} contains duplicate values."
            )
        for remote_state in values:
            previous = owner_by_remote_state.get(remote_state)
            if previous is not None:
                raise AsyncTaskManifestError(
                    f"remote state '{remote_state}' is mapped to both '{previous}' and '{canonical}'."
                )
            owner_by_remote_state[remote_state] = canonical
        normalized[canonical] = values
    return MappingProxyType(normalized)


def _require_object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AsyncTaskManifestError(f"{field} must be an object.")
    return value


def _require_fields(
    value: Mapping[str, Any],
    field: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    actual = set(value)
    missing = sorted(required - actual)
    unsupported = sorted(actual - required - optional)
    if missing or unsupported:
        raise AsyncTaskManifestError(
            f"{field} is missing {missing} or has unsupported fields {unsupported}."
        )


def _task_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
        raise AsyncTaskManifestError(
            f"{field} must match ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$."
        )
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AsyncTaskManifestError(f"{field} must be a non-empty string.")
    return value.strip()


def _http_method(value: object, field: str) -> str:
    if not isinstance(value, str) or value.upper() not in _HTTP_METHODS:
        raise AsyncTaskManifestError(f"{field} must be one of {sorted(_HTTP_METHODS)}.")
    if value != value.upper():
        raise AsyncTaskManifestError(f"{field} must use uppercase HTTP method syntax.")
    return value


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AsyncTaskManifestError(f"{field} must be a positive number.")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise AsyncTaskManifestError(f"{field} must be a positive finite number.")
    return normalized


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AsyncTaskManifestError(f"duplicate JSON field '{key}'.")
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise AsyncTaskManifestError(f"non-JSON numeric constant '{value}' is not allowed.")


__all__ = [
    "AsyncTaskAuthDefinition",
    "AsyncTaskCancelDefinition",
    "AsyncTaskExecutorDefinition",
    "AsyncTaskInputDefinition",
    "AsyncTaskKnowledgeDefinition",
    "AsyncTaskManifest",
    "AsyncTaskManifestError",
    "AsyncTaskManifestLoader",
    "AsyncTaskManifestRegistry",
    "AsyncTaskOutputDefinition",
    "AsyncTaskResourceDefinition",
    "AsyncTaskStatusDefinition",
    "AsyncTaskSubmitDefinition",
]
