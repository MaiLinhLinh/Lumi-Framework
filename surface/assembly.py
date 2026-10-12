"""Assembly of a semantic Surface Draft from usable runtime data only."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from final_framework.settings import Settings
from final_framework.task_manager import SurfacePlanTask, SurfaceRetrievalPlan
from final_framework.widgets import WidgetRegistry

from .contracts import SurfaceDraft


logger = logging.getLogger("lumi.surface_assembly")


class SurfaceAssemblyError(RuntimeError):
    """Assembly output or configuration violates the framework contract."""


@dataclass(frozen=True, slots=True)
class AssemblyValidationIssue:
    """A deterministic path/code error returned to the Assembly model."""

    path: str
    code: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code}


class AssemblyValidationError(SurfaceAssemblyError):
    """The model output can be repaired from a machine-readable feedback turn."""

    def __init__(self, issues: Sequence[AssemblyValidationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("Surface Assembly Agent output failed validation.")


AssemblyClientFactory = Callable[..., Any]
MAX_ASSEMBLY_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class CompleteSurface:
    """Assembly output before Runtime validates/materializes renderer state."""

    draft: SurfaceDraft

    def __post_init__(self) -> None:
        if not isinstance(self.draft, SurfaceDraft):
            raise SurfaceAssemblyError("complete surface requires a SurfaceDraft.")

    def to_dict(self) -> dict[str, Any]:
        return self.draft.to_dict()


ASSEMBLY_SYSTEM_PROMPT = """Bạn là Surface Assembly Agent của Lumi.

Input gồm surface_draft, widget_contracts và task_outputs. Runtime đã giới
hạn Draft và task_outputs cho đúng một slot_role. Mỗi task_output gồm:
- task: evidence_ref, task_type, target_slots và instruction;
- output_contract: JSON Schema mô tả ý nghĩa/cấu trúc output;
- output_data: kết quả thực tế của task.

Chỉ điền field props còn thiếu của block/children bằng output_data của
task có target_slots chứa slot_role của block. Dùng output_contract để hiểu
dữ liệu. Không dùng instruction hay kiến thức bên ngoài làm data.

Giữ nguyên widget_id, grid, slot_role, description, visibility, state, children
và mọi giá trị props đã có. Không thêm block hay child, không sửa layout.
Với props dạng mảng, giữ nguyên số phần tử và thứ tự của skeleton trong Draft;
chỉ điền field còn thiếu trong từng object rỗng. Không thêm hoặc bớt phần tử,
vì cardinality này đã được Draft Compiler dùng để cấp anchor ổn định.

Được diễn đạt lại cách viết của output_data để phù hợp widget,
nhưng phải giữ đầy đủ nội dung và ý nghĩa. Không được tóm tắt,
rút gọn, bỏ chi tiết, chọn lọc fact hoặc thêm fact. Tên riêng, ngày
tháng, số liệu và trích dẫn phải giữ chính xác. Nếu widget không thể
chứa đầy đủ data, không được tự cắt; hãy giữ field unresolved.

Media/resource ID chỉ được điền bằng ID xuất hiện trong output_data
của slot. Không tạo URL, source URI, asset ID, media ID, số liệu hay
factual content giả. Nếu output_data không đủ cho field, giữ field unresolved;
Final Compiler sẽ giữ placeholder hiện có. Không tạo warning hay nội dung
thay thế.

Chỉ trả JSON {"surface":{"blocks":[...]}} giữ nguyên cấu trúc Draft.
"""


ASSEMBLY_FEEDBACK_PROTOCOL = """
If you receive assembly_validation_feedback, repair only the reported paths
and return the complete scoped Surface JSON. Codes:
- invalid_json / invalid_schema: invalid JSON or Surface schema.
- must_match_draft: changed Draft structure or an existing prop value.
- no_output_for_slot: this slot has no usable task output.
- not_in_output_data: media_id is absent from this slot's output_data, or a
  new URL/source/asset reference was introduced. Use only scoped output_data.
"""


class SurfaceAssemblyAgent:
    """Cerebras-backed transformation from available data to Draft props."""

    def __init__(
        self,
        settings: Settings,
        widget_registry: WidgetRegistry,
        *,
        client_factory: AssemblyClientFactory | None = None,
    ) -> None:
        if not isinstance(settings, Settings):
            raise SurfaceAssemblyError("settings must be a Settings instance.")
        if not isinstance(widget_registry, WidgetRegistry):
            raise SurfaceAssemblyError("widget_registry must be a WidgetRegistry.")
        self._settings = settings
        self._widget_registry = widget_registry
        self._client_factory = client_factory

    @property
    def model_name(self) -> str:
        return self._settings.cerebras_assembly_model.strip()

    async def assemble(
        self,
        *,
        draft: SurfaceDraft,
        run_id: str,
        surface_plan: SurfaceRetrievalPlan,
        task_outputs_by_ref: Mapping[str, Mapping[str, Any]],
    ) -> CompleteSurface:
        if not isinstance(draft, SurfaceDraft):
            raise SurfaceAssemblyError("assembly requires a SurfaceDraft.")
        if not isinstance(run_id, str) or not run_id.strip():
            raise SurfaceAssemblyError("run_id must be a non-empty string.")
        return await self._assemble_plan(draft, surface_plan, task_outputs_by_ref, run_id)

    async def _assemble_plan(
        self,
        draft: SurfaceDraft,
        plan: SurfaceRetrievalPlan,
        task_outputs_by_ref: Mapping[str, Mapping[str, Any]] | None,
        run_id: str,
    ) -> CompleteSurface:
        if not isinstance(plan, SurfaceRetrievalPlan) or draft.blocks != plan.blocks:
            raise SurfaceAssemblyError("Surface Draft must match the Surface Retrieval Plan.")
        if not isinstance(task_outputs_by_ref, Mapping):
            raise SurfaceAssemblyError("surface plan assembly requires task_outputs_by_ref.")
        declared_refs = {task.evidence_ref for task in plan.tasks}
        if set(task_outputs_by_ref) - declared_refs:
            raise SurfaceAssemblyError("task_outputs_by_ref contains references absent from the plan.")
        normalized: dict[str, dict[str, Any]] = {}
        for task in plan.tasks:
            value = task_outputs_by_ref.get(task.evidence_ref)
            if value is not None:
                normalized[task.evidence_ref] = _normalize_task_output(task, value)
        blocks = []
        for block in draft.blocks:
            tasks = tuple(task for task in plan.tasks if block.slot_role in task.target_slots)
            scoped_outputs = tuple(
                normalized[task.evidence_ref]
                for task in tasks
                if task.evidence_ref in normalized
            )
            scoped_draft = SurfaceDraft(blocks=(block,))
            if not scoped_outputs:
                blocks.append(block)
                continue
            payload = {
                "surface_draft": scoped_draft.to_dict(),
                "widget_contracts": self._widget_contracts(scoped_draft),
                "task_outputs": list(scoped_outputs),
            }
            logger.info(
                "[ASSEMBLY:SLOT] run=%s slot=%s refs=%s",
                run_id, block.slot_role,
                [output["task"]["evidence_ref"] for output in scoped_outputs],
            )
            complete = await self._assemble_payload(
                scoped_draft, scoped_outputs, payload, ASSEMBLY_SYSTEM_PROMPT, run_id,
            )
            blocks.append(complete.draft.blocks[0])
        return CompleteSurface(draft=SurfaceDraft(blocks=tuple(blocks)))

    async def _assemble_payload(
        self,
        draft: SurfaceDraft,
        task_outputs: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any],
        system_prompt: str,
        run_id: str,
    ) -> CompleteSurface:
        if not isinstance(run_id, str) or not run_id.strip():
            raise SurfaceAssemblyError("run_id must be a non-empty string.")
        if not self._settings.cerebras_api_key:
            raise SurfaceAssemblyError("CEREBRAS_API_KEY is not configured for Surface Assembly Agent.")
        if not self.model_name:
            raise SurfaceAssemblyError("CEREBRAS_ASSEMBLY_MODEL is not configured.")

        logger.info(
            "[ASSEMBLY:START] run=%s model=%s blocks=%s task_outputs=%s widgets=%s",
            run_id,
            self.model_name,
            len(draft.blocks),
            [
                {
                    "task_type": item["task"]["task_type"],
                    "keys": sorted(item["output_data"]),
                }
                for item in task_outputs
            ],
            sorted(payload["widget_contracts"]),
        )
        client = self._client()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt + ASSEMBLY_FEEDBACK_PROTOCOL},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        last_error: AssemblyValidationError | None = None
        for attempt in range(1, MAX_ASSEMBLY_ATTEMPTS + 1):
            try:
                completion = await client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                    reasoning_effort="low",
                )
            except Exception as exc:
                logger.exception("[ASSEMBLY:MODEL_FAILED] run=%s model=%s", run_id, self.model_name)
                raise SurfaceAssemblyError("Surface Assembly Agent model request failed.") from exc

            choices = getattr(completion, "choices", None) or ()
            content = getattr(getattr(choices[0], "message", None), "content", None) if choices else None
            logger.info("[ASSEMBLY:MODEL_OUTPUT] run=%s attempt=%s json=%s", run_id, attempt, content)
            try:
                if not isinstance(content, str) or not content.strip():
                    raise AssemblyValidationError((AssemblyValidationIssue("$", "invalid_json"),))
                complete = _parse_complete_surface(_parse_json_object(content))
                self._verify(draft, complete, task_outputs)
                _verify_no_new_external_references(draft, complete.draft)
            except AssemblyValidationError as exc:
                last_error = exc
                logger.warning(
                    "[ASSEMBLY:VALIDATION_FAILED] run=%s attempt=%s errors=%s",
                    run_id,
                    attempt,
                    [issue.to_dict() for issue in exc.issues],
                )
                if attempt == MAX_ASSEMBLY_ATTEMPTS:
                    break
                if isinstance(content, str) and content.strip():
                    messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": json.dumps({
                        "type": "assembly_validation_feedback",
                        "errors": [issue.to_dict() for issue in exc.issues],
                    }, ensure_ascii=False),
                })
                continue
            logger.info("[ASSEMBLY:READY] run=%s blocks=%s", run_id, len(complete.draft.blocks))
            return complete
        assert last_error is not None
        raise last_error

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(
                api_key=self._settings.cerebras_api_key,
                base_url="https://api.cerebras.ai/v1",
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - deployment dependency boundary.
            raise SurfaceAssemblyError("Surface Assembly Agent requires the openai package.") from exc
        return AsyncOpenAI(
            api_key=self._settings.cerebras_api_key,
            base_url="https://api.cerebras.ai/v1",
        )

    def _widget_contracts(self, draft: SurfaceDraft) -> dict[str, Any]:
        widget_ids = {
            widget_id
            for block in draft.blocks
            for widget_id in (block.widget_id, *(child.widget_id for child in block.children))
        }
        return {
            widget_id: self._widget_registry.get(widget_id).public_contract()
            for widget_id in sorted(widget_ids)
        }

    def _verify(
        self,
        original: SurfaceDraft,
        complete: CompleteSurface,
        task_outputs: Sequence[Mapping[str, Any]],
    ) -> None:
        issues: list[AssemblyValidationIssue] = []
        if len(original.blocks) != len(complete.draft.blocks):
            raise AssemblyValidationError((AssemblyValidationIssue("surface.blocks", "must_match_draft"),))
        has_output_data = bool(task_outputs)
        for block_index, (before, after) in enumerate(zip(original.blocks, complete.draft.blocks, strict=True)):
            block_path = f"surface.blocks[{block_index}]"
            if before.widget_id != after.widget_id:
                issues.append(AssemblyValidationIssue(f"{block_path}.widget_id", "must_match_draft"))
            if before.grid != after.grid:
                issues.append(AssemblyValidationIssue(f"{block_path}.grid", "must_match_draft"))
            if before.slot_role != after.slot_role:
                issues.append(AssemblyValidationIssue(f"{block_path}.slot_role", "must_match_draft"))
            if before.description != after.description:
                issues.append(AssemblyValidationIssue(f"{block_path}.description", "must_match_draft"))
            if before.initial_visibility != after.initial_visibility:
                issues.append(AssemblyValidationIssue(f"{block_path}.initial_visibility", "must_match_draft"))
            if before.initial_state != after.initial_state:
                issues.append(AssemblyValidationIssue(f"{block_path}.initial_state", "must_match_draft"))
            if tuple(child.widget_id for child in before.children) != tuple(child.widget_id for child in after.children):
                issues.append(AssemblyValidationIssue(f"{block_path}.children", "must_match_draft"))
            preserved_issues = _preserved_value_issues(before.props, after.props, f"{block_path}.props")
            for child_index, (before_child, after_child) in enumerate(zip(before.children, after.children, strict=True)):
                child_path = f"{block_path}.children[{child_index}].props"
                preserved_issues.extend(_preserved_value_issues(before_child.props, after_child.props, child_path))
            issues.extend(preserved_issues)
            changed = before.props != after.props or tuple(child.props for child in before.children) != tuple(
                child.props for child in after.children
            )
            if changed and not has_output_data and not preserved_issues:
                issues.append(AssemblyValidationIssue(f"{block_path}.props", "no_output_for_slot"))
        issues.extend(_media_id_issues(complete.draft, task_outputs))
        if issues:
            raise AssemblyValidationError(tuple(_unique_issues(issues)))


def _normalize_task_output(
    task: SurfacePlanTask,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the provider-neutral Assembly boundary for one declared task."""

    if not isinstance(value, Mapping) or set(value) != {
        "task", "output_contract", "output_data",
    }:
        raise SurfaceAssemblyError(
            "task output must contain exactly task, output_contract and output_data."
        )
    task_value = value.get("task")
    output_contract = value.get("output_contract")
    output_data = value.get("output_data")
    if not isinstance(task_value, Mapping) or dict(task_value) != task.to_dict():
        raise SurfaceAssemblyError("task output context must match its declared plan task.")
    if not isinstance(output_contract, Mapping):
        raise SurfaceAssemblyError("task output_contract must be an object.")
    if output_contract.get("type") != "object":
        raise SurfaceAssemblyError("task output_contract root type must be object.")
    if not isinstance(output_data, Mapping):
        raise SurfaceAssemblyError("task output_data must be an object.")
    if not output_data:
        raise SurfaceAssemblyError("task output_data must not be empty when provided.")
    return {
        "task": task.to_dict(),
        "output_contract": _json_value(output_contract),
        "output_data": _json_value(output_data),
    }


def _json_value(value: Any) -> Any:
    """Copy frozen manifest JSON into a model-safe mutable JSON value."""

    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def _verify_no_new_external_references(original: SurfaceDraft, complete: SurfaceDraft) -> None:
    """Retrieved media must enter through trusted media_id, never raw sources."""

    def check(before: Any, after: Any, path: str) -> list[AssemblyValidationIssue]:
        issues = []
        if isinstance(after, Mapping):
            prior = before if isinstance(before, Mapping) else {}
            for key, value in after.items():
                child_path = f"{path}.{key}"
                if key in {"url", "src", "href", "source", "source_uri", "asset_id", "asset_ids"}:
                    if key not in prior or value != prior[key]:
                        issues.append(AssemblyValidationIssue(child_path, "not_in_output_data"))
                else:
                    issues.extend(check(prior.get(key), value, child_path))
        elif isinstance(after, list):
            prior = before if isinstance(before, list) else []
            for index, value in enumerate(after):
                issues.extend(check(prior[index] if index < len(prior) else None, value, f"{path}[{index}]"))
        return issues

    issues = []
    for index, (before, after) in enumerate(zip(original.blocks, complete.blocks, strict=True)):
        path = f"surface.blocks[{index}]"
        issues.extend(check(before.props, after.props, f"{path}.props"))
        for child_index, (before_child, after_child) in enumerate(zip(before.children, after.children, strict=True)):
            issues.extend(check(before_child.props, after_child.props, f"{path}.children[{child_index}].props"))
    if issues:
        raise AssemblyValidationError(tuple(issues))


def _parse_complete_surface(raw: Mapping[str, Any]) -> CompleteSurface:
    if set(raw) != {"surface"}:
        raise AssemblyValidationError((AssemblyValidationIssue("$", "invalid_schema"),))
    try:
        draft = SurfaceDraft.from_dict({"surface": raw.get("surface")})
    except Exception as exc:
        raise AssemblyValidationError((AssemblyValidationIssue("surface", "invalid_schema"),)) from exc
    return CompleteSurface(draft=draft)


def _preserved_value_issues(before: Any, after: Any, path: str) -> list[AssemblyValidationIssue]:
    """Existing Draft values are immutable; empty containers may be completed."""

    if isinstance(before, Mapping):
        if not isinstance(after, Mapping):
            return [AssemblyValidationIssue(path, "must_match_draft")]
        issues: list[AssemblyValidationIssue] = []
        for key, value in before.items():
            if key not in after:
                issues.append(AssemblyValidationIssue(f"{path}.{key}", "must_match_draft"))
            else:
                issues.extend(_preserved_value_issues(value, after[key], f"{path}.{key}"))
        return issues
    if isinstance(before, list):
        if not isinstance(after, list) or len(after) != len(before):
            return [AssemblyValidationIssue(path, "must_match_draft")]
        return [
            issue
            for index, value in enumerate(before)
            for issue in _preserved_value_issues(value, after[index], f"{path}[{index}]")
        ]
    return [] if before == after else [AssemblyValidationIssue(path, "must_match_draft")]


def _media_id_issues(
    complete: SurfaceDraft,
    task_outputs: Sequence[Mapping[str, Any]],
) -> list[AssemblyValidationIssue]:
    allowed_ids = {
        resource_id
        for task_output in task_outputs
        for key in ("media_id", "resource_id")
        for resource_id in _values_for_key(task_output["output_data"], key)
        if isinstance(resource_id, str)
    }
    issues: list[AssemblyValidationIssue] = []
    for block_index, block in enumerate(complete.blocks):
        for path, media_id in _media_id_paths(block.props, f"surface.blocks[{block_index}].props"):
            if media_id not in allowed_ids:
                issues.append(AssemblyValidationIssue(path, "not_in_output_data"))
        for child_index, child in enumerate(block.children):
            for path, media_id in _media_id_paths(
                child.props, f"surface.blocks[{block_index}].children[{child_index}].props"
            ):
                if media_id not in allowed_ids:
                    issues.append(AssemblyValidationIssue(path, "not_in_output_data"))
    return issues


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


def _media_id_paths(value: object, path: str) -> tuple[tuple[str, str], ...]:
    if isinstance(value, Mapping):
        found: list[tuple[str, str]] = []
        for key, child in value.items():
            if key == "media_id" and isinstance(child, str):
                found.append((f"{path}.media_id", child))
            else:
                found.extend(_media_id_paths(child, f"{path}.{key}"))
        return tuple(found)
    if isinstance(value, list):
        return tuple(
            media_id
            for item_index, item in enumerate(value)
            for media_id in _media_id_paths(item, f"{path}[{item_index}]")
        )
    return ()


def _unique_issues(issues: Sequence[AssemblyValidationIssue]) -> tuple[AssemblyValidationIssue, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[AssemblyValidationIssue] = []
    for issue in issues:
        key = (issue.path, issue.code)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return tuple(result)


def _parse_json_object(content: str) -> Mapping[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AssemblyValidationError((AssemblyValidationIssue("$", "invalid_json"),)) from exc
    if not isinstance(value, Mapping):
        raise AssemblyValidationError((AssemblyValidationIssue("$", "invalid_schema"),))
    return value
