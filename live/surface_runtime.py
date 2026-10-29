"""OpenUI Lang document bindings and Lumi-owned runtime state per Live session."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from lumi_openui.catalogs.assets import AssetCatalog
from lumi_openui.extension_loader import EffectRegistry
from lumi_openui.panel import render_visual_stage_map
from lumi_openui.panel.contracts import ComponentNode
from lumi_openui.surface.contracts import CompiledSurface
from lumi_openui.widgets import WidgetRegistry

from .memory import SessionMemoryStore
from .session_protocol import LiveSessionState, can_transition
from lumi_openui.openui.contracts import ProgramSource


@dataclass(frozen=True, slots=True)
class SurfaceActionResult:
    response: dict[str, Any]
    document_update: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SurfaceInteractionResult:
    interaction: dict[str, Any]
    document_update: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SurfaceDeleteResult:
    response: dict[str, Any]


class SurfaceRuntime:
    """Keep one accepted OpenUI program and its state/anchor bindings per session."""

    _REPAIRABLE_DIAGNOSTICS = frozenset({
        "image_load_failed", "renderer_missing", "widget_render_failed", "state_apply_failed",
    })

    def __init__(
        self,
        *,
        widget_registry: WidgetRegistry,
        asset_catalog: AssetCatalog,
        effect_registry: EffectRegistry,
        asset_urls: Mapping[str, str],
        memory_store: SessionMemoryStore | None = None,
    ) -> None:
        if not isinstance(widget_registry, WidgetRegistry):
            raise TypeError("widget_registry must be a WidgetRegistry.")
        if not isinstance(asset_catalog, AssetCatalog):
            raise TypeError("asset_catalog must be an AssetCatalog.")
        if not isinstance(effect_registry, EffectRegistry):
            raise TypeError("effect_registry must be an EffectRegistry.")
        self._widget_registry = widget_registry
        self._asset_catalog = asset_catalog
        self._effect_registry = effect_registry
        self._asset_urls = dict(asset_urls)
        self._memory_store = memory_store or SessionMemoryStore()
        self._technical_states: dict[str, LiveSessionState] = {}
        self._active_surfaces: dict[str, CompiledSurface] = {}
        self._openui_programs: dict[str, tuple[str, ProgramSource]] = {}
        self._identity_limits: dict[str, tuple[int, int]] = {}

    def session_memory(self, session_id: str):
        return self._memory_store.get(session_id)

    def session_state(self, session_id: str) -> LiveSessionState:
        return self._technical_states.setdefault(session_id, LiveSessionState.IDLE)

    def transition_session(self, *, session_id: str, target: LiveSessionState) -> LiveSessionState:
        current = self.session_state(session_id)
        if current != target and not can_transition(current, target):
            raise RuntimeError(f"Invalid Live session transition: {current} -> {target}")
        self._technical_states[session_id] = target
        return target

    def reset_session_state(self, session_id: str) -> None:
        """Reset only the Live transport state; retain the visible panel."""

        self._technical_states[session_id] = LiveSessionState.IDLE

    def remember_turn(self, *, session_id: str, user_text: str, assistant_text: str) -> None:
        memory = self._memory_store.get(session_id)
        memory.append("user", user_text)
        memory.append("assistant", assistant_text)

    def install_surface(self, *, session_id: str, compiled: CompiledSurface) -> None:
        if not isinstance(compiled, CompiledSurface):
            raise TypeError("compiled must be a CompiledSurface.")
        active = self._active_surfaces.get(session_id)
        if (
            active is not None
            and compiled.surface_id == active.surface_id
            and compiled.revision <= active.revision
        ):
            raise ValueError("compiled surface revision must advance the active surface.")

        # A Task Manager Draft intentionally has no Stage Map.  The accepted
        # OpenUI tree is attached immediately after this identity/state commit.
        self._visual_effects()

        self._active_surfaces[session_id] = compiled
        if active is None or active.surface_id != compiled.surface_id:
            self._openui_programs.pop(session_id, None)
            self._identity_limits[session_id] = (0, 0)
        previous = self._identity_limits.get(session_id, (0, 0))
        self._identity_limits[session_id] = (
            max(previous[0], *(int(c.id) for c in compiled.components)),
            max([previous[1], *(int(a.anchor_id[1:]) for a in compiled.anchors)]))

    def prepare_edit(self, *, session_id: str, program: ProgramSource,
                     candidate: CompiledSurface) -> CompiledSurface:
        """Remap a checked candidate to stable Slot identities without committing."""
        active = self._active(session_id)
        accepted = self.accepted_openui_program(session_id)
        if accepted is None or program.base_source != accepted.source:
            raise ValueError('Edit base is stale; request a new patch against the accepted program.')
        old = dict(zip(accepted.slots, active.components, strict=True))
        component_counter, anchor_counter = self._identity_limits[session_id]
        components, anchors = [], []
        for slot, component in zip(program.slots, candidate.components, strict=True):
            allocated = tuple(a for a in candidate.anchors if a.component_id == component.id)
            previous = old.get(slot)
            if previous is not None:
                reserved = tuple(a for a in active.anchors if a.component_id == previous.id)
                if (previous.type != component.type
                    or tuple(a.anchor_key for a in reserved) != tuple(a.anchor_key for a in allocated)
                    or tuple(c.type for c in previous.children) != tuple(c.type for c in component.children)):
                    raise ValueError(f'{slot}: widget/anchor structure changed; remove the old Slot and add a new named Slot.')
                widget = self._widget_registry.get(component.type)
                changes = {definition.name: component.props[definition.initial_prop]
                    for definition in widget.state_fields
                    if definition.initial_prop in program.prop_updates.get(slot, ())
                    and definition.initial_prop in component.props}
                changes.update(program.state_updates.get(slot, {}))
                state = (widget.validate_state_changes(current_state=previous.state, changes=changes)
                         if changes else dict(previous.state))
                component = replace(component, id=previous.id, state=state)
                anchors.extend(reserved)
            else:
                component_counter += 1
                component = replace(component, id=str(component_counter))
                for anchor in allocated:
                    anchor_counter += 1
                    anchors.append(replace(anchor, component_id=component.id, anchor_id=f'a{anchor_counter}'))
            components.append(component)
        prepared = replace(candidate, surface_id=active.surface_id, revision=active.revision+1,
                           components=tuple(components), anchors=tuple(anchors))
        # Edits immediately produce a fresh Gemini context, so validate their
        # OpenUI-derived map before replacing accepted source or runtime state.
        self._stage_map(prepared, program=program)
        return prepared

    def accept_openui_program(self, *, session_id: str, surface_id: str, program: ProgramSource) -> None:
        """Retain source only after the validated draft became the active surface."""
        surface = self._active(session_id)
        if surface.surface_id != surface_id or not isinstance(program, ProgramSource) or len(program.slots) != len(surface.components):
            raise ValueError('OpenUI statement bindings must match the installed surface.')
        self._openui_programs[session_id] = (surface.surface_id, program)

    def accepted_openui_program(self, session_id: str) -> ProgramSource | None:
        value = self._openui_programs.get(session_id)
        active = self._active_surfaces.get(session_id)
        return value[1] if value and active and value[0] == active.surface_id else None

    def install_completion(self, *, session_id: str, compiled: CompiledSurface,
                           program: ProgramSource, base_source: str) -> tuple[dict[str, Any], CompiledSurface]:
        """Commit filled source and panel together, retaining interaction state.

        The caller rebases validated content against current accepted source.
        Reject a source change during preflight; state-only revisions still apply.
        """
        active = self._active(session_id)
        accepted = self.accepted_openui_program(session_id)
        if accepted is None or accepted.source != base_source or active.surface_id != compiled.surface_id:
            raise ValueError('Completion no longer matches the active accepted program.')
        if not isinstance(program, ProgramSource) or program.slots != accepted.slots:
            raise ValueError('Completion changed statement bindings.')
        if (tuple((c.id, c.type) for c in active.components) != tuple((c.id, c.type) for c in compiled.components)
                or active.anchors != compiled.anchors):
            raise ValueError('Completion changed component or anchor identity.')
        if len(program.slots) != len(compiled.components):
            raise ValueError('Completion statement count does not match components.')
        updated = replace(compiled, revision=active.revision + 1, components=tuple(
            replace(component, state=active.component_map[component.id].state)
            for component in compiled.components))
        # Build the final map from the candidate OpenUI tree before committing
        # either source or runtime state.
        self._stage_map(updated, program=program)
        self.install_surface(session_id=session_id, compiled=updated)
        self._openui_programs[session_id] = (updated.surface_id, program)
        return self.active_openui_document(session_id), updated

    def active_openui_document(self, session_id: str) -> dict[str, Any]:
        """Return OpenUI Lang plus runtime metadata, never a component JSON tree."""
        surface = self._active(session_id)
        program = self.accepted_openui_program(session_id)
        if program is None:
            raise ValueError('no accepted OpenUI program for the active surface')
        slots = {}
        for statement_id, component in zip(program.slots, surface.components, strict=True):
            slots[statement_id] = {
                'component_id': component.id,
                'anchors': {
                    anchor.anchor_key: anchor.anchor_id
                    for anchor in surface.anchors if anchor.component_id == component.id
                },
                'state': dict(component.state),
            }
        media = _surface_media_resources(surface)
        return {
            'ui_type': 'openui_lang',
            'surface_id': surface.surface_id,
            'revision': surface.revision,
            'source': program.source,
            'is_streaming': False,
            'slots': slots,
            'resources': {'assets': dict(self._asset_urls), 'media': media},
        }

    def openui_stream_resources(self) -> dict[str, Any]:
        """Resources safe to resolve while an uncommitted create stream renders."""
        return {'assets': dict(self._asset_urls), 'media': {}}

    def active_surface_for_task(self, session_id: str) -> dict[str, Any] | None:
        surface = self._active_surfaces.get(session_id)
        if surface is None:
            return None
        return {**surface.to_dict(), "runtime_slots": self._runtime_slots(session_id, surface)}

    def _runtime_slots(self, session_id: str, surface: CompiledSurface) -> dict[str, Any]:
        program = self.accepted_openui_program(session_id)
        if program is None:
            return {}
        return {slot: {"component_id": component.id,
                       "anchor_ids": [a.anchor_id for a in surface.anchors if a.component_id == component.id],
                       "state": dict(component.state),
                       "state_fields": self._widget_registry.get(component.type).public_contract().get("state_fields", {})}
                for slot, component in zip(program.slots, surface.components, strict=True)}

    def active_surface_context(self, session_id: str) -> dict[str, Any] | None:
        surface = self._active_surfaces.get(session_id)
        if surface is None:
            return None
        return {
            "surface_id": surface.surface_id,
            "revision": surface.revision,
            "visual_stage_map": self._stage_map(
                surface, program=self.accepted_openui_program(session_id)),
            "visual_effects": self._visual_effects(),
            "runtime_slots": self._runtime_slots(session_id, surface),
        }

    def present_visual(self, *, session_id: str, anchor_id: str, effect_id: str) -> dict[str, Any]:
        surface = self._active(session_id)
        anchor = surface.anchor_map.get(anchor_id)
        if anchor is None:
            raise ValueError("unknown anchor_id for the active panel")
        try:
            self._effect_registry.get(effect_id)
        except ValueError as exc:
            raise ValueError("unknown effect_id") from exc
        return {
            "anchor_id": anchor.anchor_id,
            "effect_id": effect_id,
            "effect": effect_id,
            "panel_id": surface.surface_id,
            "panel_revision": surface.revision,
        }

    def resolve_panel_interaction(
        self, *, session_id: str, surface_id: str, revision: object, anchor_id: str, action: str
    ) -> dict[str, Any]:
        surface = self._active(session_id)
        if surface_id != surface.surface_id:
            raise ValueError("surface_id does not match the active panel")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("revision must be a positive integer")
        if revision != surface.revision:
            raise ValueError("revision does not match the active surface")
        anchor = surface.anchor_map.get(anchor_id)
        if anchor is None:
            raise ValueError("unknown anchor_id for the active panel")
        component = surface.component_map.get(anchor.component_id)
        if component is None:
            raise ValueError("anchor component is unavailable")
        widget = self._widget_registry.get(component.type)
        if not widget.allows_interaction(action):
            raise ValueError("action is not allowed for this anchor")
        return {
            "event": "surface_interaction",
            "surface_id": surface.surface_id,
            "revision": surface.revision,
            "anchor_id": anchor.anchor_id,
            "widget_id": component.type,
            "action": action,
            "content": [child.to_dict() for child in component.children],
        }

    def apply_panel_interaction(
        self, *, session_id: str, surface_id: str, revision: object, anchor_id: str, action: str
    ) -> SurfaceInteractionResult:
        interaction = self.resolve_panel_interaction(
            session_id=session_id,
            surface_id=surface_id,
            revision=revision,
            anchor_id=anchor_id,
            action=action,
        )
        surface = self._active(session_id)
        anchor = surface.anchor_map[anchor_id]
        component = surface.component_map[anchor.component_id]
        widget = self._widget_registry.get(component.type)
        changes = widget.interaction_state_changes(action=action, current_state=component.state)
        if not changes:
            return SurfaceInteractionResult(interaction=interaction)
        mutation = self.update_surface_state(
            session_id=session_id,
            surface_id=surface.surface_id,
            base_revision=surface.revision,
            updates=[{"anchor_id": anchor.anchor_id, "changes": changes}],
        )
        return SurfaceInteractionResult(
            interaction={
                **interaction,
                "revision": mutation.response["revision"],
                "visual_stage_map": mutation.response["visual_stage_map"],
                "visual_effects": mutation.response["visual_effects"],
            },
            document_update=mutation.document_update,
        )

    def update_surface_state(
        self, *, session_id: str, surface_id: str, base_revision: int, updates: list[dict[str, Any]]
    ) -> SurfaceActionResult:
        surface = self._active(session_id)
        if surface_id != surface.surface_id:
            raise ValueError("surface_id does not match the active panel")
        if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
            raise ValueError("base_revision must be a positive integer")
        if base_revision != surface.revision:
            raise ValueError("base_revision does not match the active panel revision")
        if not isinstance(updates, list) or not updates:
            raise ValueError("updates must contain at least one update")

        replacements: dict[str, dict[str, Any]] = {}
        updated_anchor_ids: list[str] = []
        for update in updates:
            if not isinstance(update, Mapping):
                raise ValueError("each update must be an object")
            anchor_id, changes = update.get("anchor_id"), update.get("changes")
            if not isinstance(anchor_id, str) or not anchor_id:
                raise ValueError("update.anchor_id must be a non-empty string")
            if not isinstance(changes, Mapping) or not changes:
                raise ValueError("update.changes must be a non-empty object")
            anchor = surface.anchor_map.get(anchor_id)
            if anchor is None:
                raise ValueError("unknown anchor_id for the active panel")
            if anchor.component_id in replacements:
                raise ValueError("updates must not target the same component more than once")
            component = surface.component_map[anchor.component_id]
            widget = self._widget_registry.get(component.type)
            replacements[component.id] = widget.validate_state_changes(
                current_state=component.state, changes=changes
            )
            updated_anchor_ids.append(anchor_id)

        updated = replace(
            surface,
            revision=surface.revision + int(any(
                state != surface.component_map[component_id].state
                for component_id, state in replacements.items())),
            components=tuple(
                replace(component, state=replacements.get(component.id, component.state))
                for component in surface.components
            ),
        )
        # Prepare the map before committing so a map failure remains atomic.
        stage_map = self._stage_map(
            updated, program=self.accepted_openui_program(session_id))
        self._active_surfaces[session_id] = updated
        document_update = self.active_openui_document(session_id)
        return SurfaceActionResult(
            response={
                "status": "completed",
                "changed": updated.revision != surface.revision,
                "updated": [{"anchor_id": anchor_id,
                             "state": dict(updated.component_map[surface.anchor_map[anchor_id].component_id].state)}
                            for anchor_id in updated_anchor_ids],
                "updated_anchor_ids": updated_anchor_ids,
                "panel_id": updated.surface_id,
                "surface_id": updated.surface_id,
                "revision": updated.revision,
                "visual_stage_map": stage_map,
                "visual_effects": self._visual_effects(),
                "runtime_slots": self._runtime_slots(session_id, updated),
            },
            document_update=document_update,
        )

    def delete_surface(self, *, session_id: str, surface_id: str, base_revision: int) -> SurfaceDeleteResult:
        surface = self._active(session_id)
        if surface_id != surface.surface_id or base_revision != surface.revision:
            raise ValueError("surface_id or revision does not match the active panel")
        del self._active_surfaces[session_id]
        self._openui_programs.pop(session_id, None)
        return SurfaceDeleteResult(response={
            "status": "completed",
            "surface_id": surface_id,
            "revision": surface.revision + 1,
            "visual_stage_map": "VISUAL STAGE MAP — KHÔNG CÓ PANEL ĐANG MỞ.",
            "visual_effects": [],
        })

    def report_runtime_diagnostic(self, *, session_id: str, diagnostic: Mapping[str, Any]) -> dict[str, Any]:
        """Validate/report browser diagnostics; Gemini decides any replacement plan."""

        surface = self._active(session_id)
        if diagnostic.get("surface_id") != surface.surface_id or diagnostic.get("revision") != surface.revision:
            return {"status": "ignored", "detail": "surface_id or revision is not active"}
        if diagnostic.get("error_type") not in self._REPAIRABLE_DIAGNOSTICS:
            return {"status": "ignored", "detail": "diagnostic error_type is not repairable"}
        return {"status": "reported", "detail": "diagnostic was forwarded to the runtime"}

    def confirm_rendered(self, *, session_id: str, surface_id: str, revision: object) -> dict[str, Any]:
        surface = self._active(session_id)
        if surface.surface_id != surface_id or surface.revision != revision:
            return {"status": "ignored", "detail": "surface_id or revision is not active"}
        return {"status": "confirmed"}

    def _active(self, session_id: str) -> CompiledSurface:
        try:
            return self._active_surfaces[session_id]
        except KeyError as exc:
            raise ValueError("no active panel") from exc

    def _stage_map(self, surface: CompiledSurface, *, program: ProgramSource | None) -> str:
        if program is None or not program.stage_slots:
            raise ValueError('accepted OpenUI program has no evaluated Stage tree')
        return render_visual_stage_map(
            program.stage_slots,
            runtime_components=surface.components,
            anchors=surface.anchors,
            widget_registry=self._widget_registry,
            asset_catalog=self._asset_catalog,
            media_resources=_surface_media_resources(surface),
        )

    def _visual_effects(self) -> list[dict[str, str]]:
        return [
            {
                "id": definition.effect_id,
                "description": definition.description,
                "usage_guidance": definition.usage_guidance,
            }
            for definition in self._effect_registry.definitions()
        ]


def _collect_media_resources(value: Any, target: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, Mapping):
        media_id = value.get('media_id')
        source = value.get('source')
        if isinstance(media_id, str) and isinstance(source, Mapping) and isinstance(source.get('url'), str):
            target[media_id] = {
                key: source[key]
                for key in ('url', 'caption', 'source_url', 'mime_type')
                if source.get(key) is not None
            }
        for nested in value.values():
            _collect_media_resources(nested, target)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _collect_media_resources(nested, target)


def _surface_media_resources(surface: CompiledSurface) -> dict[str, dict[str, Any]]:
    media: dict[str, dict[str, Any]] = {}
    for component in surface.components:
        _collect_media_resources(component.props, media)
        for child in component.children:
            _collect_media_resources(child.to_dict(), media)
    return media
