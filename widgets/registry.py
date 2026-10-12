"""Widget contracts independent of any domain, panel renderer or LLM.

The registry deliberately owns widget props and visual anchor requests.  A plan
only names a widget and supplies its props; later the compiler asks the widget
for the anchors it is allowed to materialize.  Therefore the Plan Agent never
creates DOM targets or anchor identifiers directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping


class WidgetPropsError(ValueError):
    """Raised when block props do not meet a widget's public contract."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WidgetPropsError(f"{field_name} must be a positive integer.")
    return value


@dataclass(frozen=True, slots=True)
class WidgetAnchor:
    """A widget-local anchor request; the compiler assigns final anchor IDs.

    Effects are global capabilities owned by ``EffectRegistry``. An anchor
    names only a render target; it does not grant or deny individual effects.
    """

    key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _text(self.key, "widget anchor key"))


AnchorPolicy = Callable[[Mapping[str, Any]], tuple[WidgetAnchor, ...]]
PropsValidator = Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class WidgetPropDefinition:
    """A serializable public contract for one widget property."""

    name: str
    value_type: str
    required: bool
    description: str
    source: str | None = None
    allowed_values: tuple[str, ...] = ()
    minimum: int | None = None
    template_value_kind: str = "structural"
    child_props: tuple["WidgetPropDefinition", ...] = ()
    exactly_one_of: tuple[str, ...] = ()
    additional_properties: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "widget prop name"))
        object.__setattr__(self, "value_type", _text(self.value_type, "widget prop type"))
        object.__setattr__(self, "description", _text(self.description, "widget prop description"))
        if self.source is not None:
            object.__setattr__(self, "source", _text(self.source, "widget prop source"))
        values = tuple(_text(value, "widget prop allowed value") for value in self.allowed_values)
        if len(values) != len(set(values)):
            raise WidgetPropsError("widget prop allowed values must be unique.")
        object.__setattr__(self, "allowed_values", values)
        if self.minimum is not None and self.minimum < 0:
            raise WidgetPropsError("widget prop minimum must not be negative.")
        if self.template_value_kind not in {"structural", "binding"}:
            raise WidgetPropsError(
                "widget prop template_value_kind must be structural or binding."
            )
        children = tuple(self.child_props)
        if not all(isinstance(child, WidgetPropDefinition) for child in children):
            raise WidgetPropsError("widget prop child_props must contain WidgetPropDefinition values.")
        child_names = tuple(child.name for child in children)
        if len(child_names) != len(set(child_names)):
            raise WidgetPropsError("widget prop child names must be unique.")
        if children and self.value_type != "object":
            raise WidgetPropsError("widget prop child_props are only valid for an object prop.")
        exclusive = tuple(
            _text(value, "widget prop exactly_one_of field")
            for value in self.exactly_one_of
        )
        if len(exclusive) != len(set(exclusive)):
            raise WidgetPropsError("widget prop exactly_one_of fields must be unique.")
        if any(value not in child_names for value in exclusive):
            raise WidgetPropsError(
                "widget prop exactly_one_of fields must be declared child_props."
            )
        if not isinstance(self.additional_properties, bool):
            raise WidgetPropsError("widget prop additional_properties must be a boolean.")
        object.__setattr__(self, "child_props", children)
        object.__setattr__(self, "exactly_one_of", exclusive)

    def to_public_contract(self) -> dict[str, Any]:
        contract: dict[str, Any] = {
            "type": self.value_type,
            "required": self.required,
            "description": self.description,
            "template_value_kind": self.template_value_kind,
        }
        if self.source is not None:
            contract["source"] = self.source
        if self.allowed_values:
            contract["allowed_values"] = list(self.allowed_values)
        if self.minimum is not None:
            contract["minimum"] = self.minimum
        if self.child_props:
            contract["fields"] = {
                child.name: child.to_public_contract()
                for child in self.child_props
            }
            contract["additional_properties"] = self.additional_properties
        if self.exactly_one_of:
            contract["exactly_one_of"] = list(self.exactly_one_of)
        return contract


@dataclass(frozen=True, slots=True)
class WidgetStateDefinition:
    """One runtime-state field a widget explicitly permits Runtime to change."""

    name: str
    value_type: str
    default_value: Any
    allowed_values: tuple[str, ...] = ()
    transitions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "widget state name"))
        object.__setattr__(self, "value_type", _text(self.value_type, "widget state type"))
        values = tuple(_text(value, "widget state allowed value") for value in self.allowed_values)
        if len(values) != len(set(values)):
            raise WidgetPropsError("widget state allowed values must be unique.")
        object.__setattr__(self, "allowed_values", values)
        normalized_transitions: dict[str, tuple[str, ...]] = {}
        for source, targets in self.transitions.items():
            source_value = _text(source, "widget state transition source")
            if not isinstance(targets, tuple):
                raise WidgetPropsError("widget state transition targets must be a tuple.")
            normalized_transitions[source_value] = tuple(
                _text(target, "widget state transition target") for target in targets
            )
        object.__setattr__(self, "transitions", normalized_transitions)
        # Validate the default through the same public type/value contract. A
        # transition is about a later mutation, not initial construction.
        self.validate_value(self.default_value)

    def validate_value(self, value: Any) -> Any:
        """Validate one state value without evaluating a state transition."""

        if self.value_type == "string":
            if not isinstance(value, str):
                raise WidgetPropsError(f"state.{self.name} must be a string.")
            if self.allowed_values and value not in self.allowed_values:
                raise WidgetPropsError(
                    f"state.{self.name} must be one of {list(self.allowed_values)}."
                )
        elif self.value_type == "boolean":
            if not isinstance(value, bool):
                raise WidgetPropsError(f"state.{self.name} must be a boolean.")
        else:  # A future widget supplies a supported type in the same registry change.
            raise WidgetPropsError(f"unsupported state type '{self.value_type}'.")
        return value

    def validate_change(self, *, current_value: Any, next_value: Any) -> Any:
        self.validate_value(next_value)
        allowed_targets = self.transitions.get(str(current_value))
        if allowed_targets is not None and next_value not in allowed_targets:
            raise WidgetPropsError(
                f"state.{self.name} cannot transition from '{current_value}' to '{next_value}'."
            )
        return next_value


@dataclass(frozen=True, slots=True)
class WidgetInteractionDefinition:
    """One browser action the Runtime may accept for a widget type.

    The action is a capability, not an instruction to mutate state. Runtime
    will apply the widget's transition policy in SD7; it must never trust an
    arbitrary state value sent by the browser.
    """

    action: str
    description: str
    state_rule: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _text(self.action, "widget interaction action"))
        object.__setattr__(self, "description", _text(self.description, "widget interaction description"))
        if not isinstance(self.state_rule, Mapping):
            raise WidgetPropsError("widget interaction state_rule must be an object.")
        normalized: dict[str, dict[str, str]] = {}
        for field_name, rule in self.state_rule.items():
            name = _text(field_name, "widget interaction state_rule field")
            if not isinstance(rule, Mapping) or set(rule) != {"op"}:
                raise WidgetPropsError("each interaction state_rule must contain exactly 'op'.")
            operation = _text(rule.get("op"), "widget interaction state_rule op")
            if operation not in {"toggle"}:
                raise WidgetPropsError(f"unsupported interaction state_rule operation '{operation}'.")
            normalized[name] = {"op": operation}
        object.__setattr__(self, "state_rule", normalized)

    def state_changes_for(self, current_state: Mapping[str, Any]) -> dict[str, Any]:
        """Apply this interaction's declarative, widget-owned state rule.

        The Runtime invokes this generic interpreter after it has resolved a
        trusted component.  It never branches on a widget ID; a future action
        may add another declared operation here only when a real widget needs
        it.
        """

        if not isinstance(current_state, Mapping):
            raise WidgetPropsError("widget interaction current_state must be an object.")
        changes: dict[str, Any] = {}
        for field_name, rule in self.state_rule.items():
            value = current_state.get(field_name)
            if rule["op"] == "toggle":
                if not isinstance(value, bool):
                    raise WidgetPropsError(
                        f"state.{field_name} must be a boolean to use interaction operation 'toggle'."
                    )
                changes[field_name] = not value
        return changes


@dataclass(frozen=True, slots=True)
class WidgetAssetReferenceDefinition:
    """One asset path a widget renders and Compiler must validate."""

    path: str
    allowed_kinds: tuple[str, ...] = ("image",)
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "widget asset reference path"))
        kinds = tuple(_text(kind, "widget asset reference allowed kind") for kind in self.allowed_kinds)
        if not kinds or len(kinds) != len(set(kinds)):
            raise WidgetPropsError("widget asset reference allowed_kinds must be non-empty and unique.")
        object.__setattr__(self, "allowed_kinds", kinds)
        if not isinstance(self.required, bool):
            raise WidgetPropsError("widget asset reference required must be a boolean.")


@dataclass(frozen=True, slots=True)
class StageMapTextSource:
    """One text value genuinely rendered by a state-dependent map view."""

    content_label: str | None
    text_source: str
    quote_text: bool = False

    def __post_init__(self) -> None:
        if self.content_label is not None:
            object.__setattr__(self, "content_label", _text(self.content_label, "stage map text content_label"))
        object.__setattr__(self, "text_source", _text(self.text_source, "stage map text source"))
        if not isinstance(self.quote_text, bool):
            raise WidgetPropsError("stage map text quote_text must be a boolean.")


@dataclass(frozen=True, slots=True)
class StageMapCollectionTextSource:
    """One visible text field relative to each object in a collection prop."""

    content_label: str | None
    text_source: str
    quote_text: bool = False

    def __post_init__(self) -> None:
        if self.content_label is not None:
            object.__setattr__(self, "content_label", _text(self.content_label, "stage map collection content_label"))
        object.__setattr__(self, "text_source", _text(self.text_source, "stage map collection text_source"))
        if not isinstance(self.quote_text, bool):
            raise WidgetPropsError("stage map collection quote_text must be a boolean.")


@dataclass(frozen=True, slots=True)
class StageMapView:
    """A policy view selected solely from one declared component state value."""

    state_field: str
    state_value: bool
    policy: "StageMapPolicy"

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_field", _text(self.state_field, "stage map view state_field"))
        if not isinstance(self.state_value, bool):
            raise WidgetPropsError("stage map view state_value must be a boolean.")
        if not isinstance(self.policy, StageMapPolicy):
            raise WidgetPropsError("stage map view policy must be a StageMapPolicy.")


@dataclass(frozen=True, slots=True)
class StageMapPolicy:
    """Structured, renderer-facing description of visible widget semantics.

    It deliberately names data sources rather than generating prose. SD6's
    generic stage-map renderer will resolve these paths and use only fields the
    corresponding browser widget actually renders.
    """

    kind: str
    content_label: str | None = None
    quote_text: bool = False
    anchor_key: str | None = None
    text_source: str | None = None
    asset_source: str | None = None
    asset_text_source: str | None = None
    count_source: str | None = None
    item_anchor_prefix: str | None = None
    collection_source: str | None = None
    collection_anchor_prefix: str | None = None
    text_rendered: bool = True
    children_layout: str | None = None
    collection_layout: str | None = None
    items_layout: str | None = None
    text_sources: tuple[StageMapTextSource, ...] = ()
    collection_text_sources: tuple[StageMapCollectionTextSource, ...] = ()
    views: tuple[StageMapView, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _text(self.kind, "stage map kind"))
        for field_name in (
            "content_label", "anchor_key", "text_source", "asset_source", "asset_text_source",
            "count_source", "item_anchor_prefix", "collection_source", "collection_anchor_prefix",
            "children_layout", "collection_layout", "items_layout",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _text(value, f"stage map {field_name}"))
        allowed_layouts = {"horizontal", "vertical", "wrap"}
        for field_name in ("children_layout", "collection_layout", "items_layout"):
            value = getattr(self, field_name)
            if value is not None and value not in allowed_layouts:
                raise WidgetPropsError(
                    f"stage map {field_name} must be horizontal, vertical or wrap."
                )
        if not isinstance(self.text_rendered, bool):
            raise WidgetPropsError("stage map text_rendered must be a boolean.")
        if not isinstance(self.quote_text, bool):
            raise WidgetPropsError("stage map quote_text must be a boolean.")
        if not isinstance(self.text_sources, tuple) or not all(
            isinstance(item, StageMapTextSource) for item in self.text_sources
        ):
            raise WidgetPropsError("stage map text_sources must contain StageMapTextSource values.")
        if not isinstance(self.collection_text_sources, tuple) or not all(
            isinstance(item, StageMapCollectionTextSource) for item in self.collection_text_sources
        ):
            raise WidgetPropsError("stage map collection_text_sources must contain StageMapCollectionTextSource values.")
        if self.collection_source is None and self.collection_text_sources:
            raise WidgetPropsError("stage map collection_text_sources requires collection_source.")
        if self.collection_source is not None and self.collection_anchor_prefix is None:
            raise WidgetPropsError("stage map collection_source requires collection_anchor_prefix.")
        if not isinstance(self.views, tuple) or not all(isinstance(item, StageMapView) for item in self.views):
            raise WidgetPropsError("stage map views must contain StageMapView values.")
        view_keys = tuple((item.state_field, item.state_value) for item in self.views)
        if len(view_keys) != len(set(view_keys)):
            raise WidgetPropsError("stage map views must be unique per state value.")

    def to_public_contract(self) -> dict[str, Any]:
        contract: dict[str, Any] = {
            "kind": self.kind,
            "text_rendered": self.text_rendered,
            "quote_text": self.quote_text,
        }
        for field_name in (
            "content_label", "anchor_key", "text_source", "asset_source", "asset_text_source",
            "count_source", "item_anchor_prefix", "collection_source", "collection_anchor_prefix",
            "children_layout",
        ):
            value = getattr(self, field_name)
            if value is not None:
                contract[field_name] = value
        if self.text_sources:
            contract["text_sources"] = [
                {
                    "content_label": item.content_label,
                    "text_source": item.text_source,
                    "quote_text": item.quote_text,
                }
                for item in self.text_sources
            ]
        if self.collection_text_sources:
            contract["collection_text_sources"] = [
                {
                    "content_label": item.content_label,
                    "text_source": item.text_source,
                    "quote_text": item.quote_text,
                }
                for item in self.collection_text_sources
            ]
        if self.views:
            contract["views"] = [
                {"state_field": item.state_field, "state_value": item.state_value}
                for item in self.views
            ]
        return contract

    def for_state(self, state: Mapping[str, Any]) -> "StageMapPolicy":
        for view in self.views:
            if state.get(view.state_field) == view.state_value:
                return view.policy
        return self


@dataclass(frozen=True, slots=True)
class WidgetDefinition:
    """Registered widget behaviour shared by every enabled experience."""

    validate_props: PropsValidator
    anchor_policy: AnchorPolicy
    purpose: str
    props: tuple[WidgetPropDefinition, ...]
    # A partial Draft still needs to reserve the same visual targets that the
    # completed widget will expose. Most widgets have a fixed anchor policy and
    # can reuse ``anchor_policy`` directly. Dynamic widgets may provide a
    # structural policy that reads an incomplete props skeleton.
    draft_anchor_policy: AnchorPolicy | None = None
    # An extension author writes an unbound definition. ExtensionLoader binds
    # the sole ID from manifest.json before the definition enters a registry.
    widget_id: str | None = None
    state_fields: tuple[WidgetStateDefinition, ...] = ()
    allowed_child_widget_ids: tuple[str, ...] = ()
    interactions: tuple[WidgetInteractionDefinition, ...] = ()
    stage_map_policy: StageMapPolicy | None = None
    asset_references: tuple[WidgetAssetReferenceDefinition, ...] = ()

    def __post_init__(self) -> None:
        if self.widget_id is not None:
            object.__setattr__(self, "widget_id", _text(self.widget_id, "widget_id"))
        object.__setattr__(self, "purpose", _text(self.purpose, "widget purpose"))
        names = tuple(prop.name for prop in self.props)
        if len(names) != len(set(names)):
            raise WidgetPropsError("widget prop names must be unique.")
        state_names = tuple(state.name for state in self.state_fields)
        if len(state_names) != len(set(state_names)):
            raise WidgetPropsError("widget state field names must be unique.")
        children = tuple(_text(value, "widget child widget_id") for value in self.allowed_child_widget_ids)
        if len(children) != len(set(children)):
            raise WidgetPropsError("widget child widget_ids must be unique.")
        object.__setattr__(self, "allowed_child_widget_ids", children)
        if not isinstance(self.interactions, tuple) or not all(
            isinstance(item, WidgetInteractionDefinition) for item in self.interactions
        ):
            raise WidgetPropsError("widget interactions must contain WidgetInteractionDefinition values.")
        actions = tuple(item.action for item in self.interactions)
        if len(actions) != len(set(actions)):
            raise WidgetPropsError("widget interaction actions must be unique.")
        if self.stage_map_policy is not None and not isinstance(self.stage_map_policy, StageMapPolicy):
            raise WidgetPropsError("widget stage_map_policy must be a StageMapPolicy or None.")
        if not isinstance(self.asset_references, tuple) or not all(
            isinstance(item, WidgetAssetReferenceDefinition) for item in self.asset_references
        ):
            raise WidgetPropsError("widget asset_references must contain WidgetAssetReferenceDefinition values.")
        reference_paths = tuple(item.path for item in self.asset_references)
        if len(reference_paths) != len(set(reference_paths)):
            raise WidgetPropsError("widget asset reference paths must be unique.")

    def bind_id(self, widget_id: str) -> "WidgetDefinition":
        """Return the registered form of an extension-owned definition.

        The ID belongs to manifest.json. A definition can be bound once by
        ExtensionLoader, but cannot be rebound to a different ID.
        """

        normalized = _text(widget_id, "widget_id")
        if self.widget_id is not None:
            if self.widget_id != normalized:
                raise WidgetPropsError(
                    f"widget definition is already bound to '{self.widget_id}'."
                )
            return self
        return replace(self, widget_id=normalized)

    def validate(self, props: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(props, Mapping):
            raise WidgetPropsError(f"{self.widget_id}.props must be an object.")
        return self.validate_props(props)

    def anchors_for(self, props: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
        return self.anchor_policy(self.validate(props))

    def anchors_for_draft(self, props: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
        """Return anchors reserved by an incomplete Draft component.

        This deliberately does not validate complete widget props. The Draft
        compiler has already established that the component is incomplete;
        this policy only reads structural information such as collection
        cardinality. Invalid/missing structural data remains a compilation
        error instead of silently changing anchor identity later.
        """

        if not isinstance(props, Mapping):
            raise WidgetPropsError(f"{self.widget_id}.props must be an object.")
        policy = self.draft_anchor_policy or self.anchor_policy
        anchors = policy(props)
        if not isinstance(anchors, tuple) or not all(
            isinstance(anchor, WidgetAnchor) for anchor in anchors
        ):
            raise WidgetPropsError(
                f"{self.widget_id} draft anchor policy must return WidgetAnchor values."
            )
        keys = tuple(anchor.key for anchor in anchors)
        if len(keys) != len(set(keys)):
            raise WidgetPropsError(f"{self.widget_id} draft anchor keys must be unique.")
        return anchors

    def public_props_contract(self) -> dict[str, dict[str, Any]]:
        return {prop.name: prop.to_public_contract() for prop in self.props}

    def public_contract(self) -> dict[str, Any]:
        """Detailed contract returned after Plan Agent discovers a widget."""

        contract: dict[str, Any] = {"props": self.public_props_contract()}
        if self.state_fields:
            contract["state_fields"] = {
                state.name: {
                    "type": state.value_type,
                    "default": state.default_value,
                    "allowed_values": list(state.allowed_values),
                    "transitions": {
                        source: list(targets) for source, targets in state.transitions.items()
                    },
                }
                for state in self.state_fields
            }
        if self.allowed_child_widget_ids:
            contract["allowed_child_widget_ids"] = list(self.allowed_child_widget_ids)
        if self.interactions:
            contract["interactions"] = [
                {
                    "action": item.action,
                    "description": item.description,
                    **({"state_rule": item.state_rule} if item.state_rule else {}),
                }
                for item in self.interactions
            ]
        if self.stage_map_policy is not None:
            contract["stage_map_policy"] = self.stage_map_policy.to_public_contract()
        if self.asset_references:
            contract["asset_references"] = [
                {"path": item.path, "allowed_kinds": list(item.allowed_kinds)}
                for item in self.asset_references
            ]
        return contract

    @property
    def default_state(self) -> dict[str, Any]:
        """The only state a new component receives before an interaction."""

        return {definition.name: definition.default_value for definition in self.state_fields}

    @property
    def interaction_event(self) -> str | None:
        """Compatibility view for pre-SD7 callers that support one action."""

        return self.interactions[0].action if len(self.interactions) == 1 else None

    def allows_interaction(self, action: str) -> bool:
        return any(item.action == action for item in self.interactions)

    def interaction_state_changes(
        self, *, action: str, current_state: Mapping[str, Any]
    ) -> dict[str, Any]:
        for interaction in self.interactions:
            if interaction.action == action:
                changes = interaction.state_changes_for(current_state)
                if not changes:
                    return {}
                self.validate_state_changes(current_state=current_state, changes=changes)
                return changes
        raise WidgetPropsError(f"{self.widget_id} does not allow interaction action '{action}'.")

    def materialize_initial_state(self, initial_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Merge a plan's initial state with registry defaults without transitions."""

        if initial_state is None:
            initial_state = {}
        if not isinstance(initial_state, Mapping):
            raise WidgetPropsError("initial state must be an object.")
        definitions = {field.name: field for field in self.state_fields}
        unsupported = set(initial_state) - set(definitions)
        if unsupported:
            raise WidgetPropsError(
                f"{self.widget_id} does not allow initial state fields: {sorted(unsupported)}."
            )
        state = self.default_state
        for field_name, value in initial_state.items():
            state[field_name] = definitions[field_name].validate_value(value)
        return state

    def validate_child_widget_ids(self, child_widget_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Validate a parent widget's declared child widget types.

        Child props are validated by the referenced child WidgetDefinition. The
        compiler will compose both checks in SD3; keeping this policy here
        prevents containers from accepting arbitrary nested widget types.
        """

        if not isinstance(child_widget_ids, tuple) or not all(isinstance(item, str) for item in child_widget_ids):
            raise WidgetPropsError("child widget ids must be a tuple of strings.")
        normalized = tuple(_text(item, "child widget_id") for item in child_widget_ids)
        if not normalized:
            return ()
        if not self.allowed_child_widget_ids:
            raise WidgetPropsError(f"{self.widget_id} does not allow child widgets.")
        unsupported = set(normalized) - set(self.allowed_child_widget_ids)
        if unsupported:
            raise WidgetPropsError(
                f"{self.widget_id} does not allow child widget ids: {sorted(unsupported)}."
            )
        return normalized

    def validate_state_changes(
        self,
        *,
        current_state: Mapping[str, Any],
        changes: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(current_state, Mapping) or not isinstance(changes, Mapping) or not changes:
            raise WidgetPropsError("state changes must be a non-empty object.")
        definitions = {field.name: field for field in self.state_fields}
        unsupported = set(changes) - set(definitions)
        if unsupported:
            raise WidgetPropsError(
                f"{self.widget_id} does not allow state fields: {sorted(unsupported)}."
            )
        updated = dict(current_state)
        for field_name, next_value in changes.items():
            definition = definitions[field_name]
            updated[field_name] = definition.validate_change(
                current_value=updated.get(field_name), next_value=next_value
            )
        return updated


class WidgetRegistry:
    """Explicit registry loaded from extensions; core does not branch by category."""

    def __init__(self, definitions: tuple[WidgetDefinition, ...] = ()) -> None:
        self._definitions: dict[str, WidgetDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: WidgetDefinition) -> None:
        if not isinstance(definition, WidgetDefinition):
            raise TypeError("widget registry accepts WidgetDefinition values only.")
        if definition.widget_id is None:
            raise ValueError("widget registry accepts only definitions bound to a manifest id.")
        if definition.widget_id in self._definitions:
            raise ValueError(f"widget '{definition.widget_id}' is already registered.")
        self._definitions[definition.widget_id] = definition

    def get(self, widget_id: str) -> WidgetDefinition:
        try:
            return self._definitions[widget_id]
        except KeyError as error:
            raise WidgetPropsError(f"unknown widget_id '{widget_id}'.") from error

    def widget_ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def definitions(self) -> tuple[WidgetDefinition, ...]:
        """Expose registered contracts for startup-time registry composition only."""

        return tuple(self._definitions.values())

    def widget_index(self) -> tuple[dict[str, Any], ...]:
        """Return the short discovery catalog safe for the Plan Agent's first turn."""

        index: list[dict[str, Any]] = []
        for definition in self._definitions.values():
            item: dict[str, Any] = {"id": definition.widget_id, "purpose": definition.purpose}
            if definition.allowed_child_widget_ids:
                item["allows_children"] = True
            if definition.interactions:
                item["interaction_actions"] = [item.action for item in definition.interactions]
            index.append(item)
        return tuple(index)
