"""Draft validation, early placeholder rendering and presentation regression."""
import unittest
from pathlib import Path

from final_framework.registry_composition import load_framework_registries
from final_framework.surface import (
    AnchorManifest,
    CompleteSurface,
    DraftCompilationError,
    DraftPresenter,
    DraftSurfaceCompiler,
    FinalSurfaceCompiler,
    SurfaceDraft,
)
from final_framework.panel.renderers import render_visual_stage_map, surface_document_client_payload


class DraftCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registries = load_framework_registries(Path(__file__).resolve().parents[1])

    def _compiler(self):
        return DraftSurfaceCompiler(self.registries.widget_registry, self.registries.asset_catalog())

    def test_partial_children_render_existing_placeholder_and_ascii_map(self):
        draft = SurfaceDraft.from_dict({"surface": {"blocks": [
            {"widget_id": "text", "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1}, "props": {"content": "Tiêu đề", "role": "title"}},
            {"widget_id": "choice", "slot_role": "choice_data", "description": "Lựa chọn đang chờ dữ liệu", "grid": {"col": 1, "row": 2, "col_span": 8, "row_span": 4}, "props": {}, "children": [{"widget_id": "text", "props": {}}]},
        ]}})
        compiled = self._compiler().compile(draft)
        self.assertEqual(compiled.components[1].draft_placeholder, "Lựa chọn đang chờ dữ liệu")
        self.assertEqual(compiled.components[1].children[0].props, {})
        payload = surface_document_client_payload(compiled, asset_urls={})
        self.assertNotIn("domain_id", payload["surface"])
        stage_map = render_visual_stage_map(compiled, widget_registry=self.registries.widget_registry, asset_catalog=self.registries.asset_catalog())
        self.assertIn("ĐANG TÌM DỮ LIỆU", stage_map)
        presenter = DraftPresenter(widget_registry=self.registries.widget_registry, asset_catalog=self.registries.asset_catalog())
        presentation = presenter.build(draft, compiled)
        self.assertEqual(presentation.browser_payload, payload)
        self.assertEqual(presentation.ascii_map, stage_map)

    def test_partial_props_require_description(self):
        draft = SurfaceDraft.from_dict({"surface": {"blocks": [{
            "widget_id": "text", "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1}, "props": {},
        }]}})
        with self.assertRaisesRegex(DraftCompilationError, "content"):
            self._compiler().compile(draft)

    def test_stage_map_expands_tracks_for_anchor_in_one_column_region(self):
        draft = SurfaceDraft.from_dict({"surface": {"blocks": [
            {
                "widget_id": "text",
                "grid": {"col": 8, "row": 1, "col_span": 1, "row_span": 1},
                "props": {"content": "+", "role": "body"},
            },
        ]}})
        compiled = self._compiler().compile(draft, surface_id="panel-narrow-anchor")

        stage_map = render_visual_stage_map(
            compiled,
            widget_registry=self.registries.widget_registry,
            asset_catalog=self.registries.asset_catalog(),
        )

        self.assertIn("+", stage_map)
        self.assertEqual(stage_map.count("[anchor: a1]"), 1)

    def test_reports_all_independent_grid_errors(self):
        draft = SurfaceDraft.from_dict({"surface": {"blocks": [
            {"widget_id": "text", "grid": {"col": 1, "row": 10, "col_span": 8, "row_span": 2}, "props": {"content": "A"}},
            {"widget_id": "text", "grid": {"col": 6, "row": 10, "col_span": 8, "row_span": 2}, "props": {"content": "B"}},
        ]}})
        with self.assertRaises(DraftCompilationError) as raised:
            self._compiler().compile(draft)
        self.assertEqual(len(raised.exception.issues), 3)
        self.assertIn("blocks 1 and 2 overlap.", raised.exception.issues)

    def test_draft_reserves_dynamic_anchors_and_final_compile_reuses_them(self):
        draft = SurfaceDraft.from_dict({"surface": {"blocks": [
            {
                "widget_id": "text",
                "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                "props": {"content": "Hành trình", "role": "title"},
            },
            {
                "widget_id": "timeline",
                "slot_role": "history_timeline",
                "description": "Năm mốc lịch sử đang chờ dữ liệu đã xác minh.",
                "grid": {"col": 1, "row": 2, "col_span": 16, "row_span": 5},
                "props": {"items": [{}, {}, {}, {}, {}]},
            },
            {
                "widget_id": "choice",
                "grid": {"col": 1, "row": 8, "col_span": 5, "row_span": 2},
                "props": {},
                "children": [{"widget_id": "text", "props": {"content": "1890"}}],
            },
        ]}})
        compiled_draft = self._compiler().compile(draft, surface_id="panel-stable")
        self.assertEqual(
            [(item.anchor_id, item.component_id, item.anchor_key) for item in compiled_draft.anchors],
            [
                ("a1", "1", "text"),
                ("a2", "2", "milestone_1"),
                ("a3", "2", "milestone_2"),
                ("a4", "2", "milestone_3"),
                ("a5", "2", "milestone_4"),
                ("a6", "2", "milestone_5"),
                ("a7", "3", "choice"),
            ],
        )

        complete = CompleteSurface(SurfaceDraft.from_dict({"surface": {"blocks": [
            draft.blocks[0].to_dict(),
            {
                **draft.blocks[1].to_dict(),
                "props": {"items": [
                    {"label": "1890", "title": "Mốc 1", "description": "Nội dung 1"},
                    {"label": "1911", "title": "Mốc 2", "description": "Nội dung 2"},
                    {"label": "1930", "title": "Mốc 3", "description": "Nội dung 3"},
                    {"label": "1945", "title": "Mốc 4", "description": "Nội dung 4"},
                    {"label": "1969", "title": "Mốc 5", "description": "Nội dung 5"},
                ]},
            },
            draft.blocks[2].to_dict(),
        ]}}))
        compiled_final = FinalSurfaceCompiler(
            self.registries.widget_registry,
            asset_catalog=self.registries.asset_catalog(),
        ).compile(
            complete,
            surface_id="panel-stable",
            media_session_id="run-stable",
            anchor_manifest=AnchorManifest.from_compiled(compiled_draft),
        )
        self.assertEqual(compiled_final.anchors, compiled_draft.anchors)
        final_map = render_visual_stage_map(
            compiled_final,
            widget_registry=self.registries.widget_registry,
            asset_catalog=self.registries.asset_catalog(),
        )
        for anchor_id in ("a1", "a2", "a3", "a4", "a5", "a6", "a7"):
            self.assertEqual(final_map.count(f"[anchor: {anchor_id}]"), 1)
        timeline_anchor_line = next(
            line for line in final_map.splitlines() if "[anchor: a2]" in line
        )
        for anchor_id in ("a3", "a4", "a5", "a6"):
            self.assertIn(f"[anchor: {anchor_id}]", timeline_anchor_line)

    def test_compact_timeline_expands_stage_map_without_cutting_content(self):
        descriptions = [
            "Mốc đầu tiên có phần mô tả dài và kết thúc bằng đuôi-một.",
            "Mốc thứ hai có phần mô tả dài và kết thúc bằng đuôi-hai.",
            "Mốc thứ ba có phần mô tả dài và kết thúc bằng đuôi-ba.",
            "Mốc thứ tư có phần mô tả dài và kết thúc bằng đuôi-bốn.",
        ]
        draft = SurfaceDraft.from_dict({"surface": {"blocks": [
            {
                "widget_id": "timeline",
                "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 2},
                "props": {
                    "title": "Bốn mốc quan trọng",
                    "items": [
                        {
                            "label": str(1900 + index),
                            "title": f"Mốc {index}",
                            "description": description,
                        }
                        for index, description in enumerate(descriptions, start=1)
                    ],
                },
            },
            {
                "widget_id": "text",
                "grid": {"col": 1, "row": 3, "col_span": 16, "row_span": 1},
                "props": {"content": "Nội dung sau timeline", "role": "body"},
            },
        ]}})
        compiled = self._compiler().compile(draft, surface_id="panel-compact-timeline")
        stage_map = render_visual_stage_map(
            compiled,
            widget_registry=self.registries.widget_registry,
            asset_catalog=self.registries.asset_catalog(),
        )

        for suffix in ("đuôi-một.", "đuôi-hai.", "đuôi-ba.", "đuôi-bốn."):
            self.assertIn(suffix, stage_map)
        anchor_line = next(
            line for line in stage_map.splitlines() if "[anchor: a1]" in line
        )
        for anchor_id in ("a2", "a3", "a4"):
            self.assertIn(f"[anchor: {anchor_id}]", anchor_line)
        self.assertEqual(stage_map.count("Nội dung sau timeline"), 1)
        for anchor_id in ("a1", "a2", "a3", "a4", "a5"):
            self.assertEqual(stage_map.count(f"[anchor: {anchor_id}]"), 1)
