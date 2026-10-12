import json
import tempfile
import unittest
from pathlib import Path

from final_framework.extension_loader import ExtensionLoader, ExtensionManifestError


class ExtensionLoaderTests(unittest.TestCase):
    def test_creates_empty_roots_and_returns_empty_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            loaded = ExtensionLoader(Path(temp_dir) / "extensions").load()
            self.assertEqual(loaded.browser_catalog(), {"widgets": [], "effects": []})

    def test_loads_bound_widget_and_effect_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_effect(root, "circle")
            self._write_widget(root, "card")

            loaded = ExtensionLoader(root).load()

            self.assertEqual(loaded.widgets[0].definition.widget_id, "card")
            self.assertEqual(loaded.widget_registry.widget_ids(), ("card",))
            self.assertEqual(loaded.effect_registry.effect_ids(), ("circle",))
            self.assertEqual(
                loaded.browser_catalog()["widgets"],
                [{
                    "id": "card",
                    "renderer": "widgets/card/renderer.js",
                    "styles": "widgets/card/styles.css",
                    "interaction_actions": [],
                }],
            )
            self.assertEqual(loaded.browser_catalog()["effects"][0]["id"], "circle")

    def test_widget_contract_does_not_depend_on_installed_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_widget(root, "card")

            loaded = ExtensionLoader(root).load()
            self.assertEqual(loaded.widget_registry.widget_ids(), ("card",))
            self.assertEqual(loaded.effect_registry.effect_ids(), ())

    def test_rejects_manifest_id_that_does_not_match_package_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_effect(root, "circle")
            self._write_widget(root, "card", manifest_id="wrong")

            with self.assertRaisesRegex(ExtensionManifestError, "must match package directory"):
                ExtensionLoader(root).load()

    def test_rejects_a_second_package_that_claims_an_installed_id(self) -> None:
        """Directory-owned IDs prevent two packages from claiming one registry ID."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_effect(root, "circle")
            self._write_effect(root, "circle_copy", manifest_id="circle")

            with self.assertRaisesRegex(ExtensionManifestError, "must match package directory"):
                ExtensionLoader(root).load()

    def test_rejects_contract_that_repeats_manifest_owned_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_widget(root, "card", bound_id="card")

            with self.assertRaisesRegex(ExtensionManifestError, "must not declare widget_id"):
                ExtensionLoader(root).load()

    def test_rejects_renderer_actions_that_do_not_match_the_backend_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_widget(
                root,
                "card",
                interaction_actions=("flip",),
                renderer_actions=(),
            )

            with self.assertRaisesRegex(ExtensionManifestError, "must exactly match backend contract actions"):
                ExtensionLoader(root).load()

    def test_rejects_renderer_that_declares_an_action_but_never_emits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_widget(
                root,
                "card",
                interaction_actions=("flip",),
                emitted_actions=(),
            )

            with self.assertRaisesRegex(ExtensionManifestError, "renderer emits actions"):
                ExtensionLoader(root).load()

    def test_installed_widget_may_depend_on_installed_effect_and_catalog_has_safe_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_effect(root, "circle")
            self._write_widget(root, "card")

            loaded = ExtensionLoader(root).load()

            self.assertEqual(
                loaded.browser_catalog(url_prefix="/extensions")["widgets"],
                [{
                    "id": "card",
                    "renderer": "/extensions/widgets/card/renderer.js",
                    "styles": "/extensions/widgets/card/styles.css",
                    "interaction_actions": [],
                }],
            )
            self.assertEqual(
                loaded.browser_asset("widgets/card/renderer.js").name,
                "renderer.js",
            )
            with self.assertRaisesRegex(ExtensionManifestError, "not in the catalog"):
                loaded.browser_asset("widgets/card/contract.py")

    def test_loaded_registry_and_catalog_are_extension_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self._write_effect(root, "circle")
            self._write_widget(root, "card")
            loaded = ExtensionLoader(root).load()

            widgets = loaded.widget_registry
            effects = loaded.effect_registry
            catalog = loaded.browser_catalog()

            self.assertEqual(widgets.widget_ids(), ("card",))
            self.assertEqual(effects.get("circle").usage_guidance, "Use for tests.")
            self.assertEqual([item["id"] for item in catalog["widgets"]], ["card"])

    def test_installed_flashcard_package_owns_contract_and_browser_assets(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        loaded = ExtensionLoader(project_root / "extensions").load()

        flashcard = loaded.widget_registry.get("flashcard")
        props = {
            "front": {"asset_id": "cat", "text": "Con mèo"},
            "back": {"word": "CAT", "phonetic": "/kæt/", "meaning": "con mèo"},
        }

        self.assertEqual(flashcard.validate(props), props)
        self.assertEqual(flashcard.default_state, {"visibility": "visible", "flipped": False})
        self.assertEqual(flashcard.anchors_for(props)[0].key, "card")
        self.assertEqual(flashcard.interaction_state_changes(
            action="flip", current_state=flashcard.default_state
        ), {"flipped": True})
        self.assertEqual(
            [entry["id"] for entry in loaded.browser_catalog(url_prefix="/extensions")["widgets"]],
            ["answer", "choice", "flashcard", "image", "number_display", "object_group", "text", "timeline"],
        )

    def test_installed_circle_package_owns_effect_metadata_and_assets(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        loaded = ExtensionLoader(project_root / "extensions").load()

        self.assertEqual(
            loaded.effect_registry.effect_ids(),
            ("circle", "draw_arrow", "highlight", "pulse", "spotlight", "trace_line"),
        )
        circle = loaded.effect_registry.get("circle")
        self.assertEqual(circle.description, "Khoanh rõ một vùng đang nói tới.")
        self.assertEqual(
            circle.usage_guidance,
            "Dùng khi chỉ chính xác đối tượng, đáp án hoặc kết quả.",
        )
        self.assertEqual(
            loaded.browser_catalog(url_prefix="/extensions")["effects"][0],
            {
                "id": "circle",
                "handler": "/extensions/effects/circle/effect.js",
                "styles": "/extensions/effects/circle/styles.css",
                "description": "Khoanh rõ một vùng đang nói tới.",
                "usage_guidance": "Dùng khi chỉ chính xác đối tượng, đáp án hoặc kết quả.",
            },
        )

    def test_author_kit_examples_load_as_real_widget_and_effect_packages(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        loaded = ExtensionLoader(project_root / "extensions").load()

        timeline = loaded.widget_registry.get("timeline")
        props = timeline.validate({
            "title": "Vòng đời bướm",
            "items": [
                {"label": "1", "title": "Trứng", "description": "Bướm đẻ trứng."},
                {"label": "2", "title": "Sâu", "description": "Ấu trùng ăn lá."},
            ],
        })
        self.assertEqual(props["items"][1]["title"], "Sâu")
        anchors = timeline.anchors_for(props)
        self.assertEqual([anchor.key for anchor in anchors], ["milestone_1", "milestone_2"])
        spotlight = loaded.effect_registry.get("spotlight")
        self.assertIn("Làm tối", spotlight.description)
        self.assertIn("hướng ánh nhìn", spotlight.usage_guidance)

    def test_project_startup_has_only_installed_extension_packages_without_core_fallback(self) -> None:
        """The application registry/catalog must be wholly package-owned after EF8."""

        project_root = Path(__file__).resolve().parents[1]
        loaded = ExtensionLoader(project_root / "extensions").load()
        catalog = loaded.browser_catalog(url_prefix="/extensions")

        self.assertEqual(
            loaded.widget_registry.widget_ids(),
            ("answer", "choice", "flashcard", "image", "number_display", "object_group", "text", "timeline"),
        )
        self.assertEqual(
            loaded.effect_registry.effect_ids(),
            ("circle", "draw_arrow", "highlight", "pulse", "spotlight", "trace_line"),
        )
        self.assertEqual(
            [entry["id"] for entry in catalog["widgets"]],
            list(loaded.widget_registry.widget_ids()),
        )
        self.assertEqual(
            [entry["id"] for entry in catalog["effects"]],
            list(loaded.effect_registry.effect_ids()),
        )
        for package in loaded.widgets:
            backend_actions = tuple(item.action for item in package.definition.interactions)
            self.assertEqual(package.interaction_actions, backend_actions)
            for action in backend_actions:
                package.definition.interaction_state_changes(
                    action=action,
                    current_state=package.definition.default_state,
                )

    @staticmethod
    def _write_effect(root: Path, effect_id: str, *, manifest_id: str | None = None) -> None:
        package = root / "effects" / effect_id
        package.mkdir(parents=True)
        (package / "effect.js").write_text("export function run() {}", encoding="utf-8")
        (package / "manifest.json").write_text(
            json.dumps({
                "id": manifest_id or effect_id,
                "handler": "effect.js",
                "description": "Test effect.",
                "usage_guidance": "Use for tests.",
            }),
            encoding="utf-8",
        )

    @staticmethod
    def _write_widget(
        root: Path,
        package_id: str,
        *,
        interaction_actions: tuple[str, ...] = (),
        renderer_actions: tuple[str, ...] | None = None,
        emitted_actions: tuple[str, ...] | None = None,
        manifest_id: str | None = None,
        bound_id: str | None = None,
    ) -> None:
        package = root / "widgets" / package_id
        package.mkdir(parents=True)
        rendered_actions = interaction_actions if renderer_actions is None else renderer_actions
        emitted = rendered_actions if emitted_actions is None else emitted_actions
        emit_calls = "\n".join(
            f'  emitInteraction({{ anchor_id: "test", action: {json.dumps(action)} }});'
            for action in emitted
        )
        (package / "renderer.js").write_text(
            "export const interactionActions = " + json.dumps(list(rendered_actions)) + ";\n"
            "export function render(component, { emitInteraction = () => {} } = {}) {\n"
            + emit_calls + "\n  return document.createElement('div');\n}",
            encoding="utf-8",
        )
        (package / "styles.css").write_text(".card {}", encoding="utf-8")
        widget_id_line = f"    widget_id={bound_id!r},\n" if bound_id is not None else ""
        contract = (
            "from final_framework.widgets import WidgetDefinition, WidgetInteractionDefinition\n"
            "WIDGET_EXTENSION = WidgetDefinition(\n"
            "    validate_props=lambda props: {},\n"
            "    anchor_policy=lambda props: (),\n"
            "    purpose='Test widget',\n"
            "    props=(),\n"
            + widget_id_line
            + f"    interactions=tuple(WidgetInteractionDefinition(action, 'Test action') for action in {interaction_actions!r}),\n"
            + ")\n"
        )
        (package / "contract.py").write_text(contract, encoding="utf-8")
        (package / "manifest.json").write_text(
            json.dumps({
                "id": manifest_id or package_id,
                "contract": "contract.py",
                "renderer": "renderer.js",
                "styles": "styles.css",
            }),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
