"""Domainless Surface Draft and compiled-surface contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from lumi_openui.panel.contracts import AnchorBinding, ComponentNode, PlanBlock


class SurfaceDraftError(ValueError):
    """A semantic Surface Draft is not structurally valid."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SurfaceDraftError(f"{field_name} must be a non-empty string.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class SurfaceDraft:
    """Validated semantic view of the Task Manager's OpenUI Slot tree."""

    blocks: tuple[PlanBlock, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, tuple) or not self.blocks:
            raise SurfaceDraftError("surface.blocks must contain at least one block.")
        if not all(isinstance(block, PlanBlock) for block in self.blocks):
            raise SurfaceDraftError("surface.blocks must contain PlanBlock values.")

    @classmethod
    def from_dict(cls, value: object) -> "SurfaceDraft":
        if not isinstance(value, Mapping) or set(value) != {"surface"}:
            raise SurfaceDraftError("Surface Draft must contain exactly the surface field.")
        surface = value.get("surface")
        if not isinstance(surface, Mapping) or set(surface) != {"blocks"}:
            raise SurfaceDraftError("surface must contain exactly the blocks field.")
        blocks = surface.get("blocks")
        if not isinstance(blocks, list):
            raise SurfaceDraftError("surface.blocks must be an array.")
        try:
            return cls(blocks=tuple(PlanBlock.from_dict(item) for item in blocks))
        except Exception as exc:
            raise SurfaceDraftError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {"surface": {"blocks": [block.to_dict() for block in self.blocks]}}


@dataclass(frozen=True, slots=True)
class CompiledSurface:
    """Lumi-owned runtime identity/state derived from accepted OpenUI Lang.

    Browser content is never serialized from this value; the native Renderer
    consumes ``ProgramSource.source`` directly.
    """

    surface_id: str
    revision: int
    components: tuple[ComponentNode, ...]
    anchors: tuple[AnchorBinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _text(self.surface_id, "surface_id"))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise SurfaceDraftError("revision must be a positive integer.")
        if not isinstance(self.components, tuple) or not self.components:
            raise SurfaceDraftError("components must contain ComponentNode values.")
        if not all(isinstance(component, ComponentNode) for component in self.components):
            raise SurfaceDraftError("components must contain ComponentNode values.")
        if not isinstance(self.anchors, tuple) or not all(isinstance(anchor, AnchorBinding) for anchor in self.anchors):
            raise SurfaceDraftError("anchors must contain AnchorBinding values.")
        component_ids = tuple(component.id for component in self.components)
        if len(component_ids) != len(set(component_ids)):
            raise SurfaceDraftError("components must have unique ids.")
        if any(anchor.component_id not in component_ids for anchor in self.anchors):
            raise SurfaceDraftError("anchor references an unknown component.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "revision": self.revision,
            "components": [component.to_dict() for component in self.components],
            "anchors": [
                {
                    "anchor_id": anchor.anchor_id,
                    "component_id": anchor.component_id,
                    "anchor_key": anchor.anchor_key,
                }
                for anchor in self.anchors
            ],
        }

    @property
    def component_map(self) -> dict[str, ComponentNode]:
        return {component.id: component for component in self.components}

    @property
    def anchor_map(self) -> dict[str, AnchorBinding]:
        return {anchor.anchor_id: anchor for anchor in self.anchors}


@dataclass(frozen=True, slots=True)
class AnchorManifest:
    """Immutable component/anchor identity allocated by the Draft compiler."""

    surface_id: str
    component_types: tuple[tuple[str, str], ...]
    anchors: tuple[AnchorBinding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _text(self.surface_id, "surface_id"))
        if not isinstance(self.component_types, tuple) or not self.component_types:
            raise SurfaceDraftError("anchor manifest components must not be empty.")
        normalized: list[tuple[str, str]] = []
        for item in self.component_types:
            if not isinstance(item, tuple) or len(item) != 2:
                raise SurfaceDraftError("anchor manifest component must contain id and widget_id.")
            normalized.append((_text(item[0], "component_id"), _text(item[1], "widget_id")))
        component_ids = tuple(item[0] for item in normalized)
        if len(component_ids) != len(set(component_ids)):
            raise SurfaceDraftError("anchor manifest component ids must be unique.")
        object.__setattr__(self, "component_types", tuple(normalized))
        if not isinstance(self.anchors, tuple) or not all(
            isinstance(anchor, AnchorBinding) for anchor in self.anchors
        ):
            raise SurfaceDraftError("anchor manifest anchors must contain AnchorBinding values.")
        if any(anchor.component_id not in component_ids for anchor in self.anchors):
            raise SurfaceDraftError("anchor manifest references an unknown component.")
        anchor_ids = tuple(anchor.anchor_id for anchor in self.anchors)
        if len(anchor_ids) != len(set(anchor_ids)):
            raise SurfaceDraftError("anchor manifest anchor ids must be unique.")
        anchor_keys = tuple((anchor.component_id, anchor.anchor_key) for anchor in self.anchors)
        if len(anchor_keys) != len(set(anchor_keys)):
            raise SurfaceDraftError("anchor manifest component anchor keys must be unique.")

    @classmethod
    def from_compiled(cls, compiled: CompiledSurface) -> "AnchorManifest":
        if not isinstance(compiled, CompiledSurface):
            raise SurfaceDraftError("compiled must be a CompiledSurface.")
        return cls(
            surface_id=compiled.surface_id,
            component_types=tuple((item.id, item.type) for item in compiled.components),
            anchors=compiled.anchors,
        )

    @classmethod
    def from_dict(cls, value: object) -> "AnchorManifest":
        if not isinstance(value, Mapping) or set(value) != {"surface_id", "components", "anchors"}:
            raise SurfaceDraftError(
                "anchor manifest must contain exactly surface_id, components and anchors."
            )
        components = value.get("components")
        anchors = value.get("anchors")
        if not isinstance(components, list) or not isinstance(anchors, list):
            raise SurfaceDraftError("anchor manifest components and anchors must be arrays.")
        component_types: list[tuple[str, str]] = []
        for item in components:
            if not isinstance(item, Mapping) or set(item) != {"component_id", "widget_id"}:
                raise SurfaceDraftError(
                    "anchor manifest component must contain exactly component_id and widget_id."
                )
            component_types.append((item.get("component_id"), item.get("widget_id")))
        bindings: list[AnchorBinding] = []
        for item in anchors:
            if not isinstance(item, Mapping) or set(item) != {
                "anchor_id", "component_id", "anchor_key"
            }:
                raise SurfaceDraftError(
                    "anchor manifest anchor must contain exactly anchor_id, component_id and anchor_key."
                )
            bindings.append(AnchorBinding(
                anchor_id=item.get("anchor_id"),
                component_id=item.get("component_id"),
                anchor_key=item.get("anchor_key"),
            ))
        return cls(
            surface_id=value.get("surface_id"),
            component_types=tuple(component_types),
            anchors=tuple(bindings),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "components": [
                {"component_id": component_id, "widget_id": widget_id}
                for component_id, widget_id in self.component_types
            ],
            "anchors": [
                {
                    "anchor_id": anchor.anchor_id,
                    "component_id": anchor.component_id,
                    "anchor_key": anchor.anchor_key,
                }
                for anchor in self.anchors
            ],
        }

    def anchors_for_component(self, component_id: str) -> tuple[AnchorBinding, ...]:
        return tuple(anchor for anchor in self.anchors if anchor.component_id == component_id)
