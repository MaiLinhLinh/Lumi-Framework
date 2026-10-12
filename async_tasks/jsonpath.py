"""Safe, read-only JSONPath subset used by Async Agent Task manifests."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class AsyncTaskJsonPathError(ValueError):
    """A manifest JSONPath is outside the framework's read-only subset."""


@dataclass(frozen=True, slots=True)
class JsonPathSegment:
    """One immutable navigation step in a compiled JSONPath."""

    kind: Literal["field", "index", "wildcard"]
    value: str | int | None


@dataclass(frozen=True, slots=True)
class CompiledJsonPath:
    """Startup-compiled representation with no executable expressions."""

    source: str
    segments: tuple[JsonPathSegment, ...]

    @property
    def is_plural(self) -> bool:
        return any(segment.kind == "wildcard" for segment in self.segments)


def compile_json_path(
    value: object,
    *,
    field: str,
    cardinality: Literal["single", "many"],
) -> CompiledJsonPath:
    """Parse the documented JSONPath subset and validate static cardinality."""

    if not isinstance(value, str) or not value:
        raise AsyncTaskJsonPathError(f"{field} must be a non-empty JSONPath string.")
    if not value.startswith("$"):
        raise AsyncTaskJsonPathError(f"{field} must start with '$'.")

    segments: list[JsonPathSegment] = []
    index = 1
    while index < len(value):
        character = value[index]
        if character == ".":
            if index + 1 < len(value) and value[index + 1] == ".":
                raise AsyncTaskJsonPathError(f"{field} does not allow recursive descent '..'.")
            match = _FIELD.match(value, index + 1)
            if match is None:
                raise AsyncTaskJsonPathError(f"{field} has an invalid field segment at offset {index}.")
            segments.append(JsonPathSegment("field", match.group(0)))
            index = match.end()
            continue
        if character == "[":
            close = value.find("]", index + 1)
            if close < 0:
                raise AsyncTaskJsonPathError(f"{field} has an unterminated bracket segment.")
            token = value[index + 1 : close]
            if token == "*":
                segments.append(JsonPathSegment("wildcard", None))
            elif token.isascii() and token.isdigit():
                segments.append(JsonPathSegment("index", int(token)))
            else:
                raise AsyncTaskJsonPathError(
                    f"{field} only allows numeric indexes or the wildcard '[*]'."
                )
            index = close + 1
            continue
        raise AsyncTaskJsonPathError(f"{field} has unsupported syntax at offset {index}.")

    wildcard_positions = [
        position for position, segment in enumerate(segments) if segment.kind == "wildcard"
    ]
    if wildcard_positions and wildcard_positions[-1] != len(segments) - 1:
        raise AsyncTaskJsonPathError(f"{field} only allows a terminal wildcard '[*]'.")
    if len(wildcard_positions) > 1:
        raise AsyncTaskJsonPathError(f"{field} may contain at most one wildcard.")
    if cardinality == "single" and wildcard_positions:
        raise AsyncTaskJsonPathError(f"{field} must resolve to a single value, not a wildcard set.")

    return CompiledJsonPath(value, tuple(segments))


def resolve_json_path(document: object, path: CompiledJsonPath) -> tuple[Any, ...]:
    """Evaluate a startup-compiled path without expressions or mutation."""

    if not isinstance(path, CompiledJsonPath):
        raise AsyncTaskJsonPathError("path must be a CompiledJsonPath.")
    values: tuple[Any, ...] = (document,)
    for segment in path.segments:
        resolved: list[Any] = []
        for value in values:
            if segment.kind == "field":
                if isinstance(value, Mapping) and segment.value in value:
                    resolved.append(value[segment.value])
                continue
            if segment.kind == "index":
                if (
                    isinstance(value, Sequence)
                    and not isinstance(value, (str, bytes, bytearray))
                    and isinstance(segment.value, int)
                    and segment.value < len(value)
                ):
                    resolved.append(value[segment.value])
                continue
            if segment.kind == "wildcard":
                if isinstance(value, Sequence) and not isinstance(
                    value, (str, bytes, bytearray)
                ):
                    resolved.extend(value)
                continue
            raise AsyncTaskJsonPathError(
                f"path '{path.source}' contains an unknown compiled segment."
            )
        values = tuple(resolved)
        if not values:
            break
    return values
