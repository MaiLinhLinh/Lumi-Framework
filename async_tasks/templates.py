"""Startup validation for manifest input and endpoint URL templates."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlsplit


INPUT_TEMPLATE_VARIABLES = frozenset(
    {
        "task.instruction",
        "task.task_id",
        "task.task_type",
        "task.evidence_ref",
        "run.run_id",
    }
)
JOB_ID_PLACEHOLDER = "{{job_id}}"
_EXACT_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.]*)\}\}")


class AsyncTaskTemplateError(ValueError):
    """A manifest template could execute or interpolate unsupported input."""


def validate_input_template(value: object, *, field: str = "input.payload") -> None:
    """Allow placeholders only as complete JSON string values."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise AsyncTaskTemplateError(f"{field} object keys must be strings.")
            if "{{" in key or "}}" in key:
                raise AsyncTaskTemplateError(f"{field} does not allow placeholders in object keys.")
            validate_input_template(child, field=f"{field}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            validate_input_template(child, field=f"{field}[{index}]")
        return
    if not isinstance(value, str):
        return

    has_template_marker = "{{" in value or "}}" in value
    match = _EXACT_PLACEHOLDER.fullmatch(value)
    if not has_template_marker:
        return
    if match is None:
        raise AsyncTaskTemplateError(
            f"{field} placeholders must occupy the complete JSON string value."
        )
    variable = match.group(1)
    if variable not in INPUT_TEMPLATE_VARIABLES:
        raise AsyncTaskTemplateError(f"{field} uses unsupported placeholder '{{{{{variable}}}}}'.")


def validate_endpoint_url_template(
    value: object,
    *,
    field: str,
    require_job_id: bool,
) -> str:
    """Validate a static HTTPS origin and job-id placement in path/query values."""

    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AsyncTaskTemplateError(f"{field} must be a non-empty URL without outer whitespace.")
    unsupported = re.findall(r"\{\{[^{}]*\}\}", value.replace(JOB_ID_PLACEHOLDER, ""))
    if unsupported or "{{" in value.replace(JOB_ID_PLACEHOLDER, "") or "}}" in value.replace(
        JOB_ID_PLACEHOLDER, ""
    ):
        raise AsyncTaskTemplateError(f"{field} only allows the placeholder '{JOB_ID_PLACEHOLDER}'.")

    placeholder_count = value.count(JOB_ID_PLACEHOLDER)
    if require_job_id and placeholder_count == 0:
        raise AsyncTaskTemplateError(f"{field} must contain '{JOB_ID_PLACEHOLDER}'.")
    if not require_job_id and placeholder_count:
        raise AsyncTaskTemplateError(f"{field} cannot contain '{JOB_ID_PLACEHOLDER}'.")

    marker = "lumi-job-id"
    parsed = urlsplit(value.replace(JOB_ID_PLACEHOLDER, marker))
    if parsed.scheme != "https":
        raise AsyncTaskTemplateError(f"{field} must use HTTPS.")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise AsyncTaskTemplateError(f"{field} must have a static hostname and no user info.")
    try:
        parsed.port
    except ValueError as exc:
        raise AsyncTaskTemplateError(f"{field} has an invalid port.") from exc
    if marker in parsed.scheme or marker in parsed.netloc or marker in parsed.fragment:
        raise AsyncTaskTemplateError(
            f"{field} may place '{JOB_ID_PLACEHOLDER}' only in the path or a query value."
        )
    for pair in parsed.query.split("&") if parsed.query else ():
        if marker not in pair:
            continue
        key, separator, query_value = pair.partition("=")
        if not separator or marker in key or marker not in query_value:
            raise AsyncTaskTemplateError(
                f"{field} may place '{JOB_ID_PLACEHOLDER}' only in a query value."
            )
    return value


def render_input_template(
    value: object,
    variables: Mapping[str, Any],
    *,
    field: str = "input.payload",
) -> Any:
    """Render validated full-value placeholders into a fresh JSON-compatible tree."""

    if isinstance(value, Mapping):
        return {
            key: render_input_template(child, variables, field=f"{field}.{key}")
            for key, child in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            render_input_template(child, variables, field=f"{field}[{index}]")
            for index, child in enumerate(value)
        ]
    if not isinstance(value, str):
        return value
    match = _EXACT_PLACEHOLDER.fullmatch(value)
    if match is None:
        if "{{" in value or "}}" in value:
            raise AsyncTaskTemplateError(
                f"{field} placeholders must occupy the complete JSON string value."
            )
        return value
    variable = match.group(1)
    if variable not in INPUT_TEMPLATE_VARIABLES:
        raise AsyncTaskTemplateError(
            f"{field} uses unsupported placeholder '{{{{{variable}}}}}'."
        )
    if variable not in variables:
        raise AsyncTaskTemplateError(f"{field} has no runtime value for '{{{{{variable}}}}}'.")
    return variables[variable]


def render_job_url(template: str, job_id: object, *, max_chars: int) -> str:
    """Insert a length-bounded, percent-encoded remote job ID into a validated URL."""

    if not isinstance(job_id, str) or not job_id.strip():
        raise AsyncTaskTemplateError("job_id must be a non-empty string.")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise AsyncTaskTemplateError("max_job_id_chars must be a positive integer.")
    if len(job_id) > max_chars:
        raise AsyncTaskTemplateError(
            f"job_id exceeds the configured limit of {max_chars} characters."
        )
    return template.replace(JOB_ID_PLACEHOLDER, quote(job_id, safe=""))
