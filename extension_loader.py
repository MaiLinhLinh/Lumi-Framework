"""Load and validate local widget/effect extension packages at application startup."""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .widgets import WidgetDefinition, WidgetRegistry


_EXTENSION_ID = re.compile(r"^[a-z]+(?:_[a-z]+)*$")


class ExtensionManifestError(ValueError):
    """Raised when an extension package cannot safely enter the application."""


def _browser_url(relative_path: str, url_prefix: str | None) -> str:
    if url_prefix is None:
        return relative_path
    return f"{url_prefix.rstrip('/')}/{relative_path.lstrip('/')}"


def _extension_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _EXTENSION_ID.fullmatch(value):
        raise ExtensionManifestError(
            f"{field} must match ^[a-z]+(?:_[a-z]+)*$."
        )
    return value


def validate_extension_id(value: object) -> str:
    """Validate a package ID for author-facing tooling."""

    return _extension_id(value, "extension id")


def _safe_relative_path(value: object, *, field: str, package_root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ExtensionManifestError(f"{field} must be a non-empty relative path.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ExtensionManifestError(f"{field} must stay inside its extension package.")
    resolved = (package_root / relative).resolve()
    try:
        resolved.relative_to(package_root)
    except ValueError as exc:
        raise ExtensionManifestError(f"{field} resolves outside its extension package.") from exc
    if not resolved.is_file():
        raise ExtensionManifestError(f"{field} does not name an existing file.")
    return resolved


def _validate_stylesheet(path: Path, *, field: str) -> None:
    """Reject a missing, non-CSS, unreadable or empty package stylesheet."""

    if path.suffix.lower() != ".css":
        raise ExtensionManifestError(f"{field} must name a .css stylesheet.")
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtensionManifestError(f"cannot read stylesheet '{path}'.") from exc
    if not source.strip():
        raise ExtensionManifestError(f"stylesheet '{path.name}' must not be empty.")


def _require_named_export(path: Path, export_name: str) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtensionManifestError(f"cannot read JavaScript entry point '{path}'.") from exc
    pattern = rf"\bexport\s+function\s+{re.escape(export_name)}\b"
    if not re.search(pattern, source):
        raise ExtensionManifestError(
            f"'{path.name}' must declare named export function {export_name}()."
        )


def _renderer_interaction_actions(path: Path) -> tuple[str, ...]:
    """Read the renderer's declarative action list without executing browser code."""

    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtensionManifestError(f"cannot read JavaScript entry point '{path}'.") from exc
    match = re.search(
        r"\bexport\s+const\s+interactionActions\s*=\s*(\[[\s\S]*?\])\s*;",
        source,
    )
    if match is None:
        raise ExtensionManifestError(
            f"'{path.name}' must declare export const interactionActions = [...]."
        )
    try:
        actions = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ExtensionManifestError(
            f"'{path.name}' interactionActions must be a JSON array of action strings."
        ) from exc
    if not isinstance(actions, list) or not all(isinstance(action, str) and action.strip() for action in actions):
        raise ExtensionManifestError(
            f"'{path.name}' interactionActions must be a JSON array of non-empty strings."
        )
    normalized = tuple(action.strip() for action in actions)
    if len(normalized) != len(set(normalized)):
        raise ExtensionManifestError(f"'{path.name}' interactionActions must not repeat an action.")
    return normalized


def _renderer_emitted_actions(path: Path) -> tuple[str, ...]:
    """Read literal actions passed to the standard browser interaction API."""

    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtensionManifestError(f"cannot read JavaScript entry point '{path}'.") from exc
    matches = re.findall(
        r"\bemitInteraction\s*\(\s*\{[^}]*?\baction\s*:\s*(['\"])([^'\"]+)\1",
        source,
    )
    return tuple(dict.fromkeys(action.strip() for _, action in matches if action.strip()))


def _manifest(
    package_root: Path, *, required_fields: set[str], optional_fields: set[str] = frozenset()
) -> Mapping[str, Any]:
    path = package_root / "manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtensionManifestError(f"cannot load extension manifest '{path}': {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ExtensionManifestError("extension manifest root must be an object.")
    actual_fields = set(raw)
    unsupported = actual_fields - required_fields - optional_fields
    missing = required_fields - actual_fields
    if missing or unsupported:
        raise ExtensionManifestError(
            f"extension manifest is missing {sorted(missing)} or has unsupported "
            f"fields {sorted(unsupported)}."
        )
    return raw


@dataclass(frozen=True, slots=True)
class EffectExtension:
    """Validated effect metadata available to Runtime and Browser catalog."""

    effect_id: str
    handler_path: Path
    styles_path: Path | None
    description: str
    usage_guidance: str

    def browser_catalog_entry(
        self,
        *,
        extensions_root: Path,
        url_prefix: str | None = None,
    ) -> dict[str, str]:
        handler = self.handler_path.relative_to(extensions_root).as_posix()
        entry = {
            "id": self.effect_id,
            "handler": _browser_url(handler, url_prefix),
            "description": self.description,
            "usage_guidance": self.usage_guidance,
        }
        if self.styles_path is not None:
            styles = self.styles_path.relative_to(extensions_root).as_posix()
            entry["styles"] = _browser_url(styles, url_prefix)
        return entry


class EffectRegistry:
    """Validated effect metadata; Runtime integration is introduced in EF3."""

    def __init__(self, effects: tuple[EffectExtension, ...] = ()) -> None:
        self._effects: dict[str, EffectExtension] = {}
        for effect in effects:
            if effect.effect_id in self._effects:
                raise ValueError(f"effect '{effect.effect_id}' is already registered.")
            self._effects[effect.effect_id] = effect

    def effect_ids(self) -> tuple[str, ...]:
        return tuple(self._effects)

    def get(self, effect_id: str) -> EffectExtension:
        try:
            return self._effects[effect_id]
        except KeyError as exc:
            raise ExtensionManifestError(f"unknown effect_id '{effect_id}'.") from exc

    def definitions(self) -> tuple[EffectExtension, ...]:
        return tuple(self._effects.values())

    def public_catalog(
        self,
        *,
        extensions_root: Path,
        url_prefix: str | None = None,
    ) -> list[dict[str, str]]:
        return [
            effect.browser_catalog_entry(extensions_root=extensions_root, url_prefix=url_prefix)
            for effect in self._effects.values()
        ]


@dataclass(frozen=True, slots=True)
class LoadedWidgetPackage:
    """A bound backend definition plus Browser assets for one widget package."""

    widget_id: str
    definition: WidgetDefinition
    renderer_path: Path
    styles_path: Path
    interaction_actions: tuple[str, ...]

    def browser_catalog_entry(
        self,
        *,
        extensions_root: Path,
        url_prefix: str | None = None,
    ) -> dict[str, Any]:
        renderer = self.renderer_path.relative_to(extensions_root).as_posix()
        styles = self.styles_path.relative_to(extensions_root).as_posix()
        return {
            "id": self.widget_id,
            "renderer": _browser_url(renderer, url_prefix),
            "styles": _browser_url(styles, url_prefix),
            "interaction_actions": list(self.interaction_actions),
        }


@dataclass(frozen=True, slots=True)
class LoadedExtensions:
    """The startup-validated extension set; EF3 will consume its registries/catalog."""

    root: Path
    widgets: tuple[LoadedWidgetPackage, ...]
    widget_registry: WidgetRegistry
    effect_registry: EffectRegistry

    def browser_catalog(self, *, url_prefix: str | None = None) -> dict[str, list[dict[str, Any]]]:
        return {
            "widgets": [
                widget.browser_catalog_entry(extensions_root=self.root, url_prefix=url_prefix)
                for widget in self.widgets
            ],
            "effects": self.effect_registry.public_catalog(
                extensions_root=self.root,
                url_prefix=url_prefix,
            ),
        }

    def browser_asset(self, relative_path: str) -> Path:
        """Return only an entry point Loader has validated for Browser use."""

        normalized = relative_path.replace("\\", "/").lstrip("/")
        for widget in self.widgets:
            for path in (widget.renderer_path, widget.styles_path):
                if path.relative_to(self.root).as_posix() == normalized:
                    return path
        for effect in self.effect_registry.definitions():
            for path in (effect.handler_path, effect.styles_path):
                if path is not None and path.relative_to(self.root).as_posix() == normalized:
                    return path
        raise ExtensionManifestError(f"extension browser asset '{relative_path}' is not in the catalog.")


class ExtensionLoader:
    """Validate local extension packages without changing current core registries."""

    def __init__(self, extensions_root: Path) -> None:
        self._root = extensions_root.resolve()
        self._widgets_root = self._root / "widgets"
        self._effects_root = self._root / "effects"

    def load(self) -> LoadedExtensions:
        self._widgets_root.mkdir(parents=True, exist_ok=True)
        self._effects_root.mkdir(parents=True, exist_ok=True)
        widgets = tuple(self._load_widget(path) for path in self._package_paths(self._widgets_root))
        effects = tuple(self._load_effect(path) for path in self._package_paths(self._effects_root))
        self._reject_duplicate_ids(widgets, label="widget")
        self._reject_duplicate_ids(effects, label="effect")
        return LoadedExtensions(
            root=self._root,
            widgets=widgets,
            widget_registry=WidgetRegistry(tuple(widget.definition for widget in widgets)),
            effect_registry=EffectRegistry(effects),
        )

    @staticmethod
    def _package_paths(root: Path) -> tuple[Path, ...]:
        invalid = sorted(path.name for path in root.iterdir() if not path.is_dir())
        if invalid:
            raise ExtensionManifestError(
                f"extension root '{root}' may contain package directories only: {invalid}."
            )
        return tuple(sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name))

    def _load_widget(self, package_root: Path) -> LoadedWidgetPackage:
        manifest = _manifest(
            package_root,
            required_fields={"id", "contract", "renderer", "styles"},
        )
        widget_id = self._validated_package_id(manifest, package_root, label="widget")
        contract_path = _safe_relative_path(
            manifest["contract"], field="manifest.contract", package_root=package_root
        )
        renderer_path = _safe_relative_path(
            manifest["renderer"], field="manifest.renderer", package_root=package_root
        )
        _require_named_export(renderer_path, "render")
        renderer_actions = _renderer_interaction_actions(renderer_path)
        emitted_actions = _renderer_emitted_actions(renderer_path)
        styles_path = _safe_relative_path(
            manifest["styles"], field="manifest.styles", package_root=package_root
        )
        _validate_stylesheet(styles_path, field="manifest.styles")
        definition = self._load_contract(contract_path, widget_id).bind_id(widget_id)
        contract_actions = tuple(interaction.action for interaction in definition.interactions)
        if renderer_actions != contract_actions:
            raise ExtensionManifestError(
                f"widget '{widget_id}' renderer interactionActions {list(renderer_actions)} "
                f"must exactly match backend contract actions {list(contract_actions)}."
            )
        if emitted_actions != renderer_actions:
            raise ExtensionManifestError(
                f"widget '{widget_id}' renderer emits actions {list(emitted_actions)} "
                f"but declares {list(renderer_actions)}."
            )
        self._validate_runtime_actions(definition)
        return LoadedWidgetPackage(
            widget_id, definition, renderer_path, styles_path, renderer_actions
        )

    def _load_effect(self, package_root: Path) -> EffectExtension:
        manifest = _manifest(
            package_root,
            required_fields={"id", "handler", "description", "usage_guidance"},
            optional_fields={"styles"},
        )
        effect_id = self._validated_package_id(manifest, package_root, label="effect")
        handler_path = _safe_relative_path(
            manifest["handler"], field="manifest.handler", package_root=package_root
        )
        _require_named_export(handler_path, "run")
        styles_path = (
            _safe_relative_path(manifest["styles"], field="manifest.styles", package_root=package_root)
            if "styles" in manifest
            else None
        )
        if styles_path is not None:
            _validate_stylesheet(styles_path, field="manifest.styles")
        description = self._required_text(manifest["description"], "manifest.description")
        usage_guidance = self._required_text(manifest["usage_guidance"], "manifest.usage_guidance")
        return EffectExtension(effect_id, handler_path, styles_path, description, usage_guidance)

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ExtensionManifestError(f"{field} must be a non-empty string.")
        return value.strip()

    @staticmethod
    def _validated_package_id(manifest: Mapping[str, Any], package_root: Path, *, label: str) -> str:
        extension_id = _extension_id(manifest["id"], f"{label} manifest.id")
        directory_id = _extension_id(package_root.name, f"{label} package directory")
        if extension_id != directory_id:
            raise ExtensionManifestError(
                f"{label} manifest.id '{extension_id}' must match package directory '{directory_id}'."
            )
        return extension_id

    @staticmethod
    def _load_contract(contract_path: Path, widget_id: str) -> WidgetDefinition:
        module_name = f"lumi_extension_widget_{widget_id}"
        spec = importlib.util.spec_from_file_location(module_name, contract_path)
        if spec is None or spec.loader is None:
            raise ExtensionManifestError(f"cannot import widget contract '{contract_path}'.")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise ExtensionManifestError(f"cannot execute widget contract '{widget_id}': {exc}") from exc
        definition = getattr(module, "WIDGET_EXTENSION", None)
        if not isinstance(definition, WidgetDefinition):
            raise ExtensionManifestError(
                f"widget contract '{widget_id}' must export WIDGET_EXTENSION as WidgetDefinition."
            )
        if definition.widget_id is not None:
            raise ExtensionManifestError(
                f"widget contract '{widget_id}' must not declare widget_id; manifest.json owns it."
            )
        return definition

    @staticmethod
    def _reject_duplicate_ids(items: tuple[Any, ...], *, label: str) -> None:
        values = [item.widget_id if label == "widget" else item.effect_id for item in items]
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ExtensionManifestError(f"duplicate {label} ids: {duplicates}.")

    @staticmethod
    def _validate_runtime_actions(definition: WidgetDefinition) -> None:
        """Prove every declared action can enter the generic Runtime state path."""

        for interaction in definition.interactions:
            try:
                definition.interaction_state_changes(
                    action=interaction.action,
                    current_state=definition.default_state,
                )
            except ValueError as exc:
                raise ExtensionManifestError(
                    f"widget '{definition.widget_id}' action '{interaction.action}' "
                    f"cannot be applied by the Runtime: {exc}"
                ) from exc
