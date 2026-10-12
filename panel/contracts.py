"""Stable data contracts shared by templates, compilation and presentation.

These types deliberately know nothing about a widget renderer, database, or
Gemini API.  Task-graph and artifact contracts live in ``task_manager``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, TypeAlias


JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
_VISIBILITY_STATES = frozenset({"visible", "hidden"})


class ContractValidationError(ValueError):
    """Raised when an external JSON value cannot become a framework contract."""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{field_name} must be an object.")
    return value


@dataclass(frozen=True, slots=True)
class GridRect:
    """One-based grid coordinates. Canvas-boundary checks belong to CP5."""

    col: int
    row: int
    col_span: int
    row_span: int

    def __post_init__(self) -> None:
        for field_name in ("col", "row", "col_span", "row_span"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContractValidationError(f"grid.{field_name} must be a positive integer.")

    @classmethod
    def from_dict(cls, value: object) -> "GridRect":
        data = _mapping(value, "grid")
        return cls(
            col=data.get("col"),
            row=data.get("row"),
            col_span=data.get("col_span"),
            row_span=data.get("row_span"),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "col": self.col,
            "row": self.row,
            "col_span": self.col_span,
            "row_span": self.row_span,
        }


@dataclass(frozen=True, slots=True)
class ChoiceChild:
    """One widget rendered inside a choice block, without its own grid placement.

    Choice owns the outer grid cell.  Its renderer owns the initial vertical
    arrangement of these children, so a child is deliberately not a PlanBlock.
    """

    widget_id: str
    props: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "choice child.widget_id"))
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("choice child.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))

    @classmethod
    def from_dict(cls, value: object) -> "ChoiceChild":
        data = _mapping(value, "choice child")
        return cls(
            widget_id=data.get("widget_id"),
            props=_mapping(data.get("props", {}), "choice child.props"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"widget_id": self.widget_id, "props": dict(self.props)}


@dataclass(frozen=True, slots=True)
class PlanBlock:
    """A Plan Agent layout instruction without compiler-owned identifiers."""

    widget_id: str
    grid: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    initial_visibility: str = "visible"
    initial_state: Mapping[str, JsonValue] = field(default_factory=dict)
    children: tuple[ChoiceChild, ...] = ()
    description: str | None = None
    slot_role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "block.widget_id"))
        if not isinstance(self.grid, GridRect):
            raise ContractValidationError("block.grid must be a GridRect.")
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("block.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if self.initial_visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("block.initial_visibility must be 'visible' or 'hidden'.")
        if not isinstance(self.initial_state, Mapping):
            raise ContractValidationError("block.initial_state must be an object.")
        normalized_initial_state = dict(self.initial_state)
        declared_visibility = normalized_initial_state.get("visibility")
        if declared_visibility is not None and declared_visibility != self.initial_visibility:
            raise ContractValidationError(
                "block.initial_state.visibility must match block.initial_visibility when both are provided."
            )
        object.__setattr__(self, "initial_state", normalized_initial_state)
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ChoiceChild) for child in self.children
        ):
            raise ContractValidationError("block.children must contain ChoiceChild values.")
        if self.description is not None:
            object.__setattr__(self, "description", _required_text(self.description, "block.description"))
        if self.slot_role is not None:
            object.__setattr__(self, "slot_role", _required_text(self.slot_role, "block.slot_role"))

    @classmethod
    def from_dict(cls, value: object) -> "PlanBlock":
        data = _mapping(value, "block")
        props = data.get("props", {})
        children = data.get("children", [])
        initial_state = _mapping(data.get("initial_state", {}), "block.initial_state")
        if not isinstance(children, list):
            raise ContractValidationError("block.children must be an array.")
        initial_visibility = data.get("initial_visibility", initial_state.get("visibility", "visible"))
        return cls(
            widget_id=data.get("widget_id"),
            grid=GridRect.from_dict(data.get("grid")),
            props=_mapping(props, "block.props"),
            initial_visibility=initial_visibility,
            initial_state=initial_state,
            children=tuple(ChoiceChild.from_dict(item) for item in children),
            description=data.get("description"),
            slot_role=data.get("slot_role"),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "widget_id": self.widget_id,
            "grid": self.grid.to_dict(),
            "props": dict(self.props),
            "initial_visibility": self.initial_visibility,
        }
        if self.initial_state:
            data["initial_state"] = dict(self.initial_state)
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        if self.description is not None:
            data["description"] = self.description
        if self.slot_role is not None:
            data["slot_role"] = self.slot_role
        return data


@dataclass(frozen=True, slots=True)
class ComponentChild:
    """A widget rendered by a parent component, without a panel-grid layout.

    Children are deliberately generic rather than choice-specific.  The Widget
    Registry decides which component types may contain them in a later
    checkpoint.  This contract only preserves their safe, renderer-facing
    widget type and props.
    """

    type: str
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    children: tuple["ComponentChild", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _required_text(self.type, "component child.type"))
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("component child.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ComponentChild) for child in self.children
        ):
            raise ContractValidationError("component child.children must contain ComponentChild values.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type, "props": dict(self.props)}
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass(frozen=True, slots=True)
class AnchorBinding:
    """Compiler-owned visual target. Plan Agent never creates this directly."""

    anchor_id: str
    component_id: str
    anchor_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "anchor.anchor_id"))
        object.__setattr__(self, "component_id", _required_text(self.component_id, "anchor.component_id"))
        object.__setattr__(self, "anchor_key", _required_text(self.anchor_key, "anchor.anchor_key"))

@dataclass(frozen=True, slots=True)
class ComponentNode:
    """One component on a generated surface's top-level CSS Grid.

    The component's ``state`` is intentionally an open mapping.  SD2 will
    make Widget Registry responsible for which keys and transitions are valid;
    this contract enforces only the universal visible/hidden baseline.
    """

    id: str
    type: str
    layout: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    state: Mapping[str, JsonValue] = field(default_factory=lambda: {"visibility": "visible"})
    children: tuple[ComponentChild, ...] = ()
    draft_placeholder: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "component.id"))
        object.__setattr__(self, "type", _required_text(self.type, "component.type"))
        if not isinstance(self.layout, GridRect):
            raise ContractValidationError("component.layout must be a GridRect.")
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("component.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if not isinstance(self.state, Mapping):
            raise ContractValidationError("component.state must be an object.")
        normalized_state = dict(self.state)
        visibility = normalized_state.get("visibility")
        if visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("component.state.visibility must be 'visible' or 'hidden'.")
        object.__setattr__(self, "state", normalized_state)
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ComponentChild) for child in self.children
        ):
            raise ContractValidationError("component.children must contain ComponentChild values.")
        if self.draft_placeholder is not None:
            object.__setattr__(
                self,
                "draft_placeholder",
                _required_text(self.draft_placeholder, "component.draft_placeholder"),
            )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "layout": self.layout.to_dict(),
            "props": dict(self.props),
            "state": dict(self.state),
        }
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        if self.draft_placeholder is not None:
            data["draft_placeholder"] = self.draft_placeholder
        return data
