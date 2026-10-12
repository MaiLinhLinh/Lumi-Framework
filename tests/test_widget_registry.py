import unittest

from final_framework.widgets import StageMapPolicy, WidgetPropsError
from final_framework.tests.runtime_registry import runtime_widget_registry


class WidgetRegistryTests(unittest.TestCase):

    def test_choice_exposes_its_children_anchor_and_select_event(self) -> None:
        registry = runtime_widget_registry()
        choice = registry.get("choice")
        self.assertEqual(choice.validate({}), {})
        self.assertEqual(choice.anchors_for({})[0].key, "choice")
        self.assertEqual(choice.allowed_child_widget_ids, ("image", "text", "number_display", "object_group"))
        self.assertEqual(choice.interaction_event, "select")
        self.assertTrue(choice.allows_interaction("select"))
        self.assertFalse(choice.allows_interaction("flip"))

    def test_registry_materializes_only_widget_declared_initial_state(self) -> None:
        choice = self.registry.get("choice")
        self.assertEqual(choice.default_state, {"visibility": "visible", "selected": False})
        self.assertEqual(
            choice.materialize_initial_state({"visibility": "hidden", "selected": True}),
            {"visibility": "hidden", "selected": True},
        )
        with self.assertRaisesRegex(WidgetPropsError, "initial state fields"):
            choice.materialize_initial_state({"flipped": True})
        with self.assertRaisesRegex(WidgetPropsError, "state.selected"):
            choice.materialize_initial_state({"selected": "yes"})

    def test_registry_validates_container_child_widget_policy(self) -> None:
        choice = self.registry.get("choice")
        self.assertEqual(choice.validate_child_widget_ids(("image", "text")), ("image", "text"))
        with self.assertRaisesRegex(WidgetPropsError, "does not allow child widget ids"):
            choice.validate_child_widget_ids(("choice",))
        with self.assertRaisesRegex(WidgetPropsError, "does not allow child widgets"):
            self.registry.get("image").validate_child_widget_ids(("text",))

    def test_registry_exposes_structured_stage_map_policies(self) -> None:
        image_policy = self.registry.get("image").stage_map_policy
        self.assertIsNotNone(image_policy)
        self.assertEqual(image_policy.asset_source, "props.asset_id")
        self.assertEqual(image_policy.asset_text_source, "asset.caption")
        self.assertFalse(image_policy.text_rendered)
        self.assertEqual(
            self.registry.get("choice").stage_map_policy.to_public_contract(),
            {
                "kind": "container",
                "text_rendered": True,
                "quote_text": False,
                "anchor_key": "choice",
                "children_layout": "vertical",
            },
        )

        timeline_policy = self.registry.get("timeline").stage_map_policy
        group_policy = self.registry.get("object_group").stage_map_policy
        self.assertEqual(timeline_policy.collection_layout, "horizontal")
        self.assertEqual(group_policy.items_layout, "wrap")
        self.assertNotIn("collection_layout", timeline_policy.to_public_contract())
        self.assertNotIn("items_layout", group_policy.to_public_contract())

    def test_stage_map_layout_values_are_closed_internal_enums(self) -> None:
        for field_name in ("children_layout", "collection_layout", "items_layout"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(WidgetPropsError, field_name):
                    StageMapPolicy(kind="test", **{field_name: "diagonal"})
    def setUp(self) -> None:
        self.registry = runtime_widget_registry()

    def test_default_widget_ids(self) -> None:
        self.assertEqual(
            self.registry.widget_ids(),
            ("answer", "choice", "flashcard", "image", "number_display", "object_group", "text", "timeline"),
        )

    def test_flashcard_declares_two_faces_flip_rule_and_card_anchor(self) -> None:
        flashcard = self.registry.get("flashcard")
        props = {
            "front": {"asset_id": "cat", "text": "Con mèo"},
            "back": {"word": "CAT", "phonetic": "/kæt/", "meaning": "con mèo"},
        }
        self.assertEqual(flashcard.validate(props), props)
        self.assertEqual(flashcard.default_state, {"visibility": "visible", "flipped": False})
        self.assertEqual(flashcard.anchors_for(props)[0].key, "card")
        self.assertTrue(flashcard.allows_interaction("flip"))
        self.assertEqual(
            flashcard.interaction_state_changes(
                action="flip", current_state={"visibility": "visible", "flipped": False},
            ),
            {"flipped": True},
        )
        self.assertEqual(flashcard.asset_references[0].path, "props.front.asset_id")
        with self.assertRaisesRegex(WidgetPropsError, "exactly front and back"):
            flashcard.validate({"front": props["front"]})

    def test_widget_index_is_the_short_full_discovery_catalog(self) -> None:
        index = self.registry.widget_index()
        self.assertEqual({item["id"] for item in index}, set(self.registry.widget_ids()))
        self.assertTrue(all(set(item) >= {"id", "purpose"} for item in index))

    def test_widget_index_marks_discoverable_composite_and_interactive_widgets(self) -> None:
        self.assertEqual(
            tuple(item for item in self.registry.widget_index() if item["id"] in {"choice", "flashcard"}),
            (
                {
                    "id": "choice",
                    "purpose": "Tạo một lựa chọn có thể chạm/chọn; hiển thị ảnh hoặc nhóm ở trên và nhãn/chữ ở dưới.",
                    "allows_children": True,
                    "interaction_actions": ["select"],
                },
                {
                    "id": "flashcard",
                    "purpose": "Hiển thị thẻ từ vựng có thể lật giữa mặt ảnh và mặt kiến thức.",
                    "interaction_actions": ["flip"],
                },
            ),
        )

    def test_public_widget_contract_identifies_asset_backed_props(self) -> None:
        image = self.registry.get("image")
        self.assertEqual(image.widget_id, "image")
        self.assertEqual(image.purpose, "Hiển thị một ảnh từ Asset Catalog hoặc kết quả search ảnh đã được backend xác minh.")
        self.assertEqual(
            image.public_props_contract()["asset_id"],
            {
                "type": "string",
                "required": False,
                "template_value_kind": "binding",
                "description": "ID của asset ảnh sẽ hiển thị.",
                "source": "asset_catalog.id",
            },
        )
        self.assertEqual(
            self.registry.get("object_group").public_props_contract()["count"]["minimum"],
            1,
        )

    def test_widget_props_declare_template_binding_or_structure(self) -> None:
        text_contract = self.registry.get("text").public_props_contract()
        image_contract = self.registry.get("image").public_props_contract()

        self.assertEqual(text_contract["content"]["template_value_kind"], "binding")
        self.assertEqual(text_contract["role"]["template_value_kind"], "structural")
        self.assertEqual(image_contract["asset_id"]["template_value_kind"], "binding")

    def test_text_image_answer_and_number_display_have_visual_anchors(self) -> None:
        self.assertEqual(self.registry.get("text").anchors_for({"content": "Xin chào"})[0].key, "text")
        anchors = self.registry.get("image").anchors_for({"asset_id": "dog", "label": "Chó"})
        self.assertEqual(anchors[0].key, "image")
        answer = self.registry.get("answer").anchors_for({"value": "3"})
        self.assertEqual(answer[0].key, "answer")
        number = self.registry.get("number_display").anchors_for({"value": "12"})
        self.assertEqual(number[0].key, "number")

    def test_object_group_declares_group_and_per_item_anchors(self) -> None:
        anchors = self.registry.get("object_group").anchors_for({"asset_id": "dog", "count": 3})
        self.assertEqual([anchor.key for anchor in anchors], ["group", "item_1", "item_2", "item_3"])

    def test_object_group_accepts_one_composite_media_without_item_anchors(self) -> None:
        group = self.registry.get("object_group")
        props = group.validate({"media_id": "generated_fruit", "count": 3})
        self.assertEqual(props, {"count": 3, "media_id": "generated_fruit"})
        self.assertEqual([anchor.key for anchor in group.anchors_for(props)], ["group"])

        with self.assertRaisesRegex(WidgetPropsError, "exactly one"):
            group.validate({
                "asset_id": "apple",
                "media_id": "generated_fruit",
                "count": 3,
            })

    def test_invalid_widget_props_are_rejected(self) -> None:
        with self.assertRaises(WidgetPropsError):
            self.registry.get("object_group").validate({"asset_id": "dog", "count": 0})
        with self.assertRaises(WidgetPropsError):
            self.registry.get("image").validate({"asset_id": "dog", "unknown": True})
        with self.assertRaises(WidgetPropsError):
            self.registry.get("answer").validate({"value": ""})


if __name__ == "__main__":
    unittest.main()
