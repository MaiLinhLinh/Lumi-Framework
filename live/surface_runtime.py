"""Domainless runtime state for panels created by the Task Manager pipeline.

This module deliberately owns only the copied panel interaction contract.  It
does not know about domains, Plan Agent, retrieval, or Task Manager planning.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from final_framework.catalogs.assets import AssetCatalog
from final_framework.extension_loader import EffectRegistry
from final_framework.panel import render_visual_stage_map, surface_document_client_payload
from final_framework.panel.contracts import ComponentNode
from final_framework.surface.contracts import CompiledSurface
from final_framework.widgets import WidgetRegistry

from .memory import SessionMemoryStore
from .session_protocol import LiveSessionState, can_transition


@dataclass(frozen=True, slots=True)
class PanelActionResult:
    response: dict[str, Any]
    panel_update: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PanelInteractionResult:
    interaction: dict[str, Any]
    panel_update: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SurfaceDeleteResult:
    response: dict[str, Any]


class SurfaceRuntime:
    """Keep one compiled, domainless surface per Live session."""

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

    def install_surface(self, *, session_id: str, compiled: CompiledSurface) -> dict[str, Any]:
        if not isinstance(compiled, CompiledSurface):
            raise TypeError("compiled must be a CompiledSurface.")
        active = self._active_surfaces.get(session_id)
        if (
            active is not None
            and compiled.surface_id == active.surface_id
            and compiled.revision <= active.revision
        ):
            raise ValueError("compiled surface revision must advance the active surface.")

        # Materialize every representation consumed immediately after install
        # before committing the new active surface.  A Stage Map or browser
        # payload failure must leave Runtime and Browser pointing at the same
        # previously installed surface.
        browser_payload = surface_document_client_payload(
            compiled, asset_urls=self._asset_urls
        )
        self._stage_map(compiled)
        self._visual_effects()

        self._active_surfaces[session_id] = compiled
        return browser_payload

    def active_surface_for_task(self, session_id: str) -> dict[str, Any] | None:
        surface = self._active_surfaces.get(session_id)
        return surface.to_dict() if surface is not None else None

    def active_surface_context(self, session_id: str) -> dict[str, Any] | None:
        surface = self._active_surfaces.get(session_id)
        if surface is None:
            return None
        return {
            "surface_id": surface.surface_id,
            "revision": surface.revision,
            "visual_stage_map": self._stage_map(surface),
            "visual_effects": self._visual_effects(),
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
    ) -> PanelInteractionResult:
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
            return PanelInteractionResult(interaction=interaction)
        mutation = self.update_surface_state(
            session_id=session_id,
            surface_id=surface.surface_id,
            base_revision=surface.revision,
            updates=[{"anchor_id": anchor.anchor_id, "changes": changes}],
        )
        return PanelInteractionResult(
            interaction={
                **interaction,
                "revision": mutation.response["revision"],
                "visual_stage_map": mutation.response["visual_stage_map"],
                "visual_effects": mutation.response["visual_effects"],
            },
            panel_update=mutation.panel_update,
        )

    def update_surface_state(
        self, *, session_id: str, surface_id: str, base_revision: int, updates: list[dict[str, Any]]
    ) -> PanelActionResult:
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
            revision=surface.revision + 1,
            components=tuple(
                replace(component, state=replacements.get(component.id, component.state))
                for component in surface.components
            ),
        )
        self._active_surfaces[session_id] = updated
        return PanelActionResult(
            response={
                "status": "completed",
                "updated_anchor_ids": updated_anchor_ids,
                "panel_id": updated.surface_id,
                "surface_id": updated.surface_id,
                "revision": updated.revision,
                "visual_stage_map": self._stage_map(updated),
                "visual_effects": self._visual_effects(),
            },
            panel_update=surface_document_client_payload(updated, asset_urls=self._asset_urls),
        )

    def delete_surface(self, *, session_id: str, surface_id: str, base_revision: int) -> SurfaceDeleteResult:
        surface = self._active(session_id)
        if surface_id != surface.surface_id or base_revision != surface.revision:
            raise ValueError("surface_id or revision does not match the active panel")
        del self._active_surfaces[session_id]
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

    def _stage_map(self, surface: CompiledSurface) -> str:
        return render_visual_stage_map(
            surface,
            widget_registry=self._widget_registry,
            asset_catalog=self._asset_catalog,
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
