"""Author-facing scaffold and validation commands for local extensions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .extension_loader import ExtensionLoader, ExtensionManifestError, validate_extension_id


_PROJECT_ROOT = Path(__file__).resolve().parent

_WIDGET_MANIFEST = '''{{
  "id": "{extension_id}",
  "contract": "contract.py",
  "renderer": "component.tsx",
  "styles": "styles.css"
}}
'''
_WIDGET_CONTRACT = '''"""Backend contract for the {extension_id} widget extension."""

from lumi_openui.openui_schema import unbound_props
from lumi_openui.widgets import StageMapPolicy, WidgetDefinition, WidgetStateDefinition

_VISIBILITY = WidgetStateDefinition(
    name="visibility", value_type="string", default_value="visible",
    allowed_values=("visible", "hidden"),
    transitions={{"visible": ("hidden",), "hidden": ("visible",)}},
)

# manifest.json owns the ID. Keep this definition unbound.
WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props,
    anchor_policy=lambda _props: (),
    purpose="TODO: describe the learning or interaction purpose.",
    props=(),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="{extension_id}", text_rendered=False),
)
'''
_WIDGET_COMPONENT = '''/** @jsxRuntime classic */
/** @jsx React.createElement */
import {{ React, defineComponent, z }} from "../../../frontend/src/runtime/openui";
import {{ useWidgetRoot }} from "../../../frontend/src/runtime/widget";

export const interactionActions = [];

export const widget = defineComponent({{
  name: "{component_name}",
  description: "TODO: describe the learning or interaction purpose.",
  props: z.strictObject({{}}),
  component: () => <section {{...useWidgetRoot(undefined, "lumi-widget lumi-widget-{extension_id}")}}>
    TODO: implement {extension_id}
  </section>,
}});
'''
_WIDGET_STYLES = '''.lumi-widget-{extension_id} {{
  width: 100%; height: 100%; min-width: 0; min-height: 0;
  display: grid; place-items: center; padding: 12px; overflow: hidden;
  border: 2px dashed var(--panel-accent); border-radius: var(--panel-radius);
}}
'''
_WIDGET_README = '''# {extension_id}

1. Khai báo props, state, anchor và Stage Map trong `contract.py`.
2. Khai báo component OpenUI bằng `defineComponent()` trong `component.tsx`.
3. Chỉ phát interaction đã khai báo trong `interactionActions` và backend contract.
4. Giữ CSS cục bộ trong `styles.css`.
5. Chạy `npm run build` trong `frontend` để sinh schema/spec, sau đó chạy `python -m lumi_openui.extension_tools validate`.
'''
_EFFECT_MANIFEST = '''{{
  "id": "{extension_id}",
  "handler": "effect.tsx",
  "styles": "styles.css",
  "description": "TODO: describe the visible semantic result.",
  "usage_guidance": "TODO: tell Gemini Live when this effect is appropriate."
}}
'''
_EFFECT_COMPONENT = '''/** @jsxRuntime classic */
/** @jsx React.createElement */
import {{ React, defineEffect }} from "../../../frontend/src/presentation/effect";

export const effect = defineEffect({{
  component: ({{ context: {{ rect }} }}) => <rect
    className="lumi-overlay-{extension_id}"
    x={{Math.max(0, rect.x - 4)}} y={{Math.max(0, rect.y - 4)}}
    width={{rect.width + 8}} height={{rect.height + 8}} rx="10" ry="10"
  />,
}});
'''
_EFFECT_STYLES = '''.lumi-overlay-{extension_id} {{
  fill: none;
  stroke: var(--panel-accent);
  stroke-width: 3;
  pointer-events: none;
}}
'''
_EFFECT_README = '''# {extension_id}

Khai báo effect React bằng `defineEffect()` trong `effect.tsx`. Nếu component tạo
class, timer hoặc tài nguyên trên target thì phải cleanup trong effect hook.
`description` và `usage_guidance` trong manifest là ngữ nghĩa Gemini Live nhận
được khi effect được phép dùng.
'''


def _write_new(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _template(source: str, extension_id: str) -> str:
    """Insert scaffold identifiers without treating JS/Python braces as format fields."""

    component_name = "".join(part.capitalize() for part in extension_id.split("_"))
    return (source.replace("{extension_id}", extension_id)
            .replace("{component_name}", component_name)
            .replace("{{", "{").replace("}}", "}"))


def _create_widget(root: Path, extension_id: str) -> Path:
    package = root / "widgets" / extension_id
    if package.exists():
        raise FileExistsError(f"widget package already exists: {package}")
    package.mkdir(parents=True)
    _write_new(package / "manifest.json", _template(_WIDGET_MANIFEST, extension_id))
    _write_new(package / "contract.py", _template(_WIDGET_CONTRACT, extension_id))
    _write_new(package / "component.tsx", _template(_WIDGET_COMPONENT, extension_id))
    _write_new(package / "styles.css", _template(_WIDGET_STYLES, extension_id))
    _write_new(package / "README.md", _template(_WIDGET_README, extension_id))
    tests = package / "tests"; tests.mkdir()
    _write_new(tests / "README.md", "Add contract, render and interaction tests here.\n")
    return package


def _create_effect(root: Path, extension_id: str) -> Path:
    package = root / "effects" / extension_id
    if package.exists():
        raise FileExistsError(f"effect package already exists: {package}")
    package.mkdir(parents=True)
    _write_new(package / "manifest.json", _template(_EFFECT_MANIFEST, extension_id))
    _write_new(package / "effect.tsx", _template(_EFFECT_COMPONENT, extension_id))
    _write_new(package / "styles.css", _template(_EFFECT_STYLES, extension_id))
    _write_new(package / "README.md", _template(_EFFECT_README, extension_id))
    tests = package / "tests"; tests.mkdir()
    _write_new(tests / "README.md", "Add target and cleanup tests here.\n")
    return package


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and validate Lumi local extensions.")
    parser.add_argument("--extensions-root", type=Path, default=_PROJECT_ROOT / "extensions")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create-widget", "create-effect"):
        command = commands.add_parser(name); command.add_argument("extension_id")
    commands.add_parser("validate")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.extensions_root.resolve()
    try:
        if args.command == "validate":
            loaded = ExtensionLoader(root).load()
            print(f"Extension validation passed: {len(loaded.widgets)} widget(s), {len(loaded.effect_registry.effect_ids())} effect(s).")
            return 0
        extension_id = validate_extension_id(args.extension_id)
        package = _create_widget(root, extension_id) if args.command == "create-widget" else _create_effect(root, extension_id)
        print(f"Created {args.command.removeprefix('create-')} package: {package}")
        return 0
    except (ExtensionManifestError, FileExistsError, OSError, ValueError) as exc:
        print(f"Extension tools error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
