"""Declarative reusable layout-template catalogs.

The catalog describes plans in natural language for the Plan Agent.  It never
decides suitability in Python: the agent chooses an id and the backend only
checks that the id exists before loading its stored trusted template contract.

Every catalog entry is a reusable layout template. Concrete presentation plans
exist only transiently after binding materialization.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from final_framework.catalogs.layout_templates import LayoutTemplate, LayoutTemplateError, TemplateSpec


class TemplateCatalogError(ValueError):
    """Raised when a reusable plan catalog or a stored plan is invalid."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TemplateCatalogError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _safe_relative_path(value: object, field_name: str) -> Path:
    path = Path(_text(value, field_name))
    if path.is_absolute() or ".." in path.parts:
        raise TemplateCatalogError(f"{field_name} must be a safe relative path.")
    return path


def _json_object(path: Path, field_name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateCatalogError(f"cannot load {field_name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise TemplateCatalogError(f"{field_name} root must be an object.")
    return value


@dataclass(frozen=True, slots=True)
class TemplateCatalogEntry:
    """One reusable template advertised to the Plan Agent without paths."""

    id: str
    description: str
    layout_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "template.id"))
        object.__setattr__(self, "description", _text(self.description, "template.description"))
        if not isinstance(self.layout_path, Path):
            raise TemplateCatalogError("template.layout_path must be a Path.")

    def for_plan_agent(self, *, semantic_spec: TemplateSpec) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "semantic_spec": semantic_spec.to_dict(),
        }

    def to_catalog_record(self) -> dict[str, str]:
        return {
            "id": self.id,
            "description": self.description,
            "layout_path": self.layout_path.as_posix(),
        }


@dataclass(frozen=True, slots=True)
class TemplateCatalog:
    """Trusted loader for layouts reusable by every experience."""

    resource_root: Path
    entries: tuple[TemplateCatalogEntry, ...] = ()
    catalog_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource_root, Path):
            raise TemplateCatalogError("template_catalog.resource_root must be a Path.")
        object.__setattr__(self, "resource_root", self.resource_root.resolve())
        if not isinstance(self.entries, tuple) or not all(isinstance(item, TemplateCatalogEntry) for item in self.entries):
            raise TemplateCatalogError("template_catalog.entries must contain TemplateCatalogEntry values.")
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise TemplateCatalogError("template catalog contains duplicate ids.")
        if self.catalog_path is not None and not isinstance(self.catalog_path, Path):
            raise TemplateCatalogError("template_catalog.catalog_path must be a Path.")

    def for_plan_agent(self) -> list[dict[str, Any]]:
        """Advertise semantic shape, never merely a visually similar title."""

        return [
            entry.for_plan_agent(semantic_spec=self.load_layout_template(entry.id).semantic_spec)
            for entry in self.entries
        ]

    def contains(self, template_id: str) -> bool:
        return any(entry.id == template_id for entry in self.entries)

    def next_generated_template_id(self) -> str:
        """Return the next short shared ID for an extracted layout."""

        numbers = [
            int(match.group(1))
            for entry in self.entries
            if (match := re.fullmatch(r"tm([1-9][0-9]*)", entry.id)) is not None
        ]
        return f"tm{max(numbers, default=0) + 1}"

    def entry(self, template_id: str) -> TemplateCatalogEntry:
        requested_id = _text(template_id, "template_id")
        entry = next((item for item in self.entries if item.id == requested_id), None)
        if entry is None:
            raise TemplateCatalogError(f"unknown template_id: {requested_id}.")
        return entry

    def load_layout_template(self, template_id: str) -> LayoutTemplate:
        """Load a reusable layout frame; its bindings are not materialized here."""

        entry = self.entry(template_id)
        data = _json_object(self._resolve_entry_path(entry.layout_path), f"layout template {entry.id}")
        try:
            template = LayoutTemplate.from_dict(data)
        except LayoutTemplateError as exc:
            raise TemplateCatalogError(str(exc)) from exc
        if template.template_id != entry.id:
            raise TemplateCatalogError("stored layout template id must match the catalog id.")
        return template

    def save_layout_template(self, template: LayoutTemplate) -> "TemplateCatalog":
        """Persist a new shared layout template without overwriting an entry."""

        if self.contains(template.template_id):
            raise TemplateCatalogError(f"template_id '{template.template_id}' already exists.")
        if self.catalog_path is None:
            raise TemplateCatalogError("cannot persist a layout template without a catalog path.")

        relative_layout_path = Path("templates") / "layouts" / f"{template.template_id}.layout.json"
        layout_path = self._resolve_entry_path(relative_layout_path)
        if layout_path.exists():
            raise TemplateCatalogError("layout template file already exists.")
        layout_path.parent.mkdir(parents=True, exist_ok=True)
        layout_path.write_text(
            json.dumps(template.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        new_entry = TemplateCatalogEntry(
            id=template.template_id,
            description=template.description,
            layout_path=relative_layout_path,
        )
        new_entries = (*self.entries, new_entry)
        catalog_data = {
            "templates": [entry.to_catalog_record() for entry in new_entries],
        }
        self.catalog_path.write_text(
            json.dumps(catalog_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return TemplateCatalog(
            resource_root=self.resource_root,
            entries=new_entries,
            catalog_path=self.catalog_path,
        )

    def delete_layout_template(self, template_id: str) -> "TemplateCatalog":
        """Delete one stored layout and its catalog record as one operation.

        The associated file is always resolved inside the shared resource root before it is
        deleted.  This keeps deletion shared and prevents
        stale catalog entries from surviving after a layout is removed.
        """

        entry = self.entry(template_id)
        if self.catalog_path is None:
            raise TemplateCatalogError("cannot delete a layout template without a catalog path.")

        layout_path = self._resolve_entry_path(entry.layout_path)
        if layout_path.exists():
            if not layout_path.is_file():
                raise TemplateCatalogError("layout template path must point to a file.")
            layout_path.unlink()

        new_entries = tuple(item for item in self.entries if item.id != entry.id)
        catalog_data = {
            "templates": [item.to_catalog_record() for item in new_entries],
        }
        self.catalog_path.write_text(
            json.dumps(catalog_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return TemplateCatalog(
            resource_root=self.resource_root,
            entries=new_entries,
            catalog_path=self.catalog_path,
        )

    def _resolve_entry_path(self, relative_path: Path) -> Path:
        path = (self.resource_root / relative_path).resolve()
        try:
            path.relative_to(self.resource_root)
        except ValueError as exc:
            raise TemplateCatalogError("template path resolves outside the shared resource root.") from exc
        return path


def load_template_catalog(
    *,
    catalog_path: Path,
    resource_root: Path,
) -> TemplateCatalog:
    """Load a catalog whose paths are always constrained to the shared resource root."""

    root = resource_root.resolve()
    resolved_catalog = catalog_path.resolve()
    try:
        resolved_catalog.relative_to(root)
    except ValueError as exc:
        raise TemplateCatalogError("template catalog resolves outside the shared resource root.") from exc
    raw = _json_object(resolved_catalog, "template catalog")
    items = raw.get("templates", [])
    if not isinstance(items, list):
        raise TemplateCatalogError("template_catalog.templates must be an array.")
    entries: list[TemplateCatalogEntry] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise TemplateCatalogError(f"template_catalog.templates[{index}] must be an object.")
        entries.append(TemplateCatalogEntry(
            id=item.get("id"),
            description=item.get("description"),
            layout_path=_safe_relative_path(
                item.get("layout_path"), f"template_catalog.templates[{index}].layout_path"
            ),
        ))
    return TemplateCatalog(
        resource_root=root,
        entries=tuple(entries),
        catalog_path=resolved_catalog,
    )
