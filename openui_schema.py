"""Validate widget props using build artifacts generated from OpenUI/Zod."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .widgets import WidgetDefinition, WidgetPropDefinition, WidgetPropsError


def unbound_props(_props: Mapping[str, Any]) -> dict[str, Any]:
    raise WidgetPropsError("OpenUI widget must be loaded through ExtensionLoader after frontend build.")


def _nullable(schema: Mapping[str, Any]) -> bool:
    return schema.get("type") == "null" or "null" in (schema.get("type") or []) or any(
        _nullable(branch) for branch in schema.get("anyOf", [])
    )


def _normalize(value: Any, schema: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        trim = schema.get("lumi_trim") or any(branch.get("lumi_trim") for branch in schema.get("anyOf", []))
        return value.strip() if trim else value
    if isinstance(value, list):
        return [_normalize(item, schema.get("items", {})) for item in value]
    if isinstance(value, Mapping):
        fields = schema.get("properties", {})
        result = {key: _normalize(item, fields.get(key, {})) for key, item in value.items()
                  if not (item is None and key in fields and key not in schema.get("required", []) and _nullable(fields[key]))}
        for key, field in fields.items():
            if key not in result and "default" in field:
                result[key] = field["default"]
        return result
    return value


def _prop(name: str, schema: Mapping[str, Any], required: bool) -> WidgetPropDefinition:
    kind = schema.get("type", "string")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "string")
    if "anyOf" in schema and "type" not in schema:
        kind = next((item.get("type") for item in schema["anyOf"] if item.get("type") != "null"), "string")
    fields = schema.get("properties", {})
    children = tuple(_prop(key, field, key in schema.get("required", []))
                     for key, field in fields.items())
    return WidgetPropDefinition(
        name=name, value_type=kind, required=required,
        description=schema.get("description") or name,
        source=schema.get("source"),
        allowed_values=tuple(schema.get("enum", [])), minimum=schema.get("minimum"),
        template_value_kind=schema.get("template_value_kind", "structural" if schema.get("enum") else "binding"),
        child_props=children,
        exactly_one_of=tuple(schema.get("exactly_one_of", ())),
        additional_properties=schema.get("additionalProperties", True),
    )


def bind_openui_schema(definition: WidgetDefinition, *, widget_id: str,
                       renderer: Path, manifest: Path, framework: Path) -> WidgetDefinition:
    artifact = framework / "build" / "openui" / "widget-schemas.json"
    try:
        data = json.loads(artifact.read_text(encoding="utf-8"))
        schema, entry = data["schemas"][widget_id], data["entries"][widget_id]
    except (OSError, ValueError, KeyError) as exc:
        raise WidgetPropsError(f"Missing OpenUI schema for '{widget_id}'; run npm run build in frontend.") from exc
    for path, field in ((renderer, "source_hash"), (manifest, "manifest_hash")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get(field):
            raise WidgetPropsError(f"Stale OpenUI schema for '{widget_id}'; rebuild frontend.")
    for relative, expected in data.get("inputs", {}).items():
        path = (framework / relative).resolve()
        try:
            path.relative_to(framework.resolve())
        except ValueError as exc:
            raise WidgetPropsError("Invalid OpenUI schema input path.") from exc
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise WidgetPropsError("Stale OpenUI schema dependencies; rebuild frontend.")
    if entry.get("interaction_actions") != [item.action for item in definition.interactions]:
        raise WidgetPropsError(f"OpenUI action metadata for '{widget_id}' differs from its Python contract.")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    def validate(props: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize(props, schema)
        error = next(validator.iter_errors(normalized), None)
        if error:
            path = ".".join(str(part) for part in error.absolute_path)
            raise WidgetPropsError(f"{widget_id}.props{'.' + path if path else ''}: {error.message}")
        return normalized

    props = tuple(_prop(name, field, name in schema.get("required", []))
                  for name, field in schema.get("properties", {}).items())
    return replace(definition, validate_props=validate, props=props)
