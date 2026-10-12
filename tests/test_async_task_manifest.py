"""CP1 tests for the independent Async Agent Task manifest boundary."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from final_framework.async_tasks import (
    AsyncTaskManifestError,
    AsyncTaskManifestLoader,
    AsyncTaskManifestRegistry,
    compile_json_path,
)
from final_framework.agent_tasks import SEARCH_LOCAL_MEDIA, SEARCH_LOCAL_TEXT


def valid_manifest() -> dict:
    return {
        "id": "search_image_task",
        "description": "Tìm ảnh phù hợp với yêu cầu.",
        "knowledge": {"allowed_tools": [SEARCH_LOCAL_MEDIA]},
        "input": {
            "payload": {"query": "{{task.instruction}}", "source": "lumi"},
            "schema": {
                "type": "object",
                "required": ["query", "source"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "source": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "executor": {
            "type": "http_async_agent",
            "submit": {
                "url": "https://agent.example.com/tasks/search-image",
                "method": "POST",
                "job_id_path": "$.job_id",
            },
            "status": {
                "url": "https://agent.example.com/jobs/{{job_id}}?view=status",
                "method": "GET",
                "state_path": "$.status",
                "states": {
                    "queued": ["queued", "pending"],
                    "running": ["running", "processing"],
                    "succeeded": ["succeeded", "completed"],
                    "failed": ["failed", "error"],
                    "cancelled": ["cancelled"],
                },
                "error_path": "$.error",
            },
            "cancel": {
                "url": "https://agent.example.com/jobs/{{job_id}}/cancel",
                "method": "POST",
            },
            "auth": {"type": "bearer_env", "env": "SEARCH_IMAGE_AGENT_TOKEN"},
            "poll_interval_seconds": 1,
            "timeout_seconds": 60,
        },
        "output": {
            "path": "$.payload",
            "schema": {
                "type": "object",
                "required": ["images"],
                "properties": {
                    "images": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["url", "mime_type"],
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string", "format": "uri"},
                                "mime_type": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    }
                },
                "additionalProperties": False,
            },
            "resources": [
                {
                    "items_path": "$.images[*]",
                    "uri_path": "$.url",
                    "mime_type_path": "$.mime_type",
                    "caption_path": "$.title",
                }
            ],
        },
    }


class AsyncTaskManifestTests(unittest.TestCase):
    def test_loads_valid_package_into_immutable_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "tasks"
            self._write(root, "search_image_task", valid_manifest())

            registry = AsyncTaskManifestLoader(root).load()
            definition = registry.get("search_image_task")

            self.assertEqual(registry.task_types(), ("search_image_task",))
            self.assertEqual(
                registry.public_catalog(),
                ({"task_type": "search_image_task", "description": "Tìm ảnh phù hợp với yêu cầu."},),
            )
            self.assertEqual(definition.executor.submit.job_id_path.source, "$.job_id")
            self.assertEqual(definition.knowledge.allowed_tools, (SEARCH_LOCAL_MEDIA,))
            self.assertTrue(definition.output.resources[0].items_path.is_plural)
            with self.assertRaises(FrozenInstanceError):
                definition.description = "changed"  # type: ignore[misc]
            with self.assertRaises(TypeError):
                definition.input.payload["query"] = "changed"  # type: ignore[index]
            with self.assertRaises(TypeError):
                definition.output.schema["type"] = "array"  # type: ignore[index]

    def test_missing_root_is_created_but_not_published_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions" / "tasks"
            registry = AsyncTaskManifestLoader(root).load()
            self.assertTrue(root.is_dir())
            self.assertEqual(registry.definitions(), ())

    def test_accepts_each_combination_of_canonical_knowledge_tools(self) -> None:
        cases = (
            [],
            [SEARCH_LOCAL_TEXT],
            [SEARCH_LOCAL_MEDIA],
            [SEARCH_LOCAL_TEXT, SEARCH_LOCAL_MEDIA],
        )
        for allowed_tools in cases:
            with self.subTest(allowed_tools=allowed_tools), tempfile.TemporaryDirectory() as temp_dir:
                manifest = valid_manifest()
                manifest["knowledge"]["allowed_tools"] = allowed_tools
                root = Path(temp_dir) / "tasks"
                self._write(root, "search_image_task", manifest)

                definition = AsyncTaskManifestLoader(root).load().get("search_image_task")

                self.assertEqual(definition.knowledge.allowed_tools, tuple(allowed_tools))

    def test_rejects_missing_unknown_duplicate_or_malformed_knowledge_tools(self) -> None:
        mutations = (
            (lambda manifest: manifest.pop("knowledge"), "missing"),
            (
                lambda manifest: manifest["knowledge"].update(
                    {"allowed_tools": ["search_everything"]}
                ),
                "unknown Agent Task tool",
            ),
            (
                lambda manifest: manifest["knowledge"].update(
                    {"allowed_tools": [SEARCH_LOCAL_TEXT, SEARCH_LOCAL_TEXT]}
                ),
                "duplicates",
            ),
            (
                lambda manifest: manifest["knowledge"].update(
                    {"allowed_tools": SEARCH_LOCAL_TEXT}
                ),
                "must be an array",
            ),
            (
                lambda manifest: manifest["knowledge"].update({"provider": "local"}),
                "unsupported",
            ),
        )
        for mutate, message in mutations:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                manifest = valid_manifest()
                mutate(manifest)
                root = Path(temp_dir) / "tasks"
                self._write(root, "search_image_task", manifest)
                with self.assertRaisesRegex(AsyncTaskManifestError, message):
                    AsyncTaskManifestLoader(root).load()

    def test_public_catalog_does_not_expose_knowledge_grants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "tasks"
            manifest = valid_manifest()
            manifest["knowledge"]["allowed_tools"] = [
                SEARCH_LOCAL_TEXT,
                SEARCH_LOCAL_MEDIA,
            ]
            self._write(root, "search_image_task", manifest)

            entry = AsyncTaskManifestLoader(root).load().public_catalog()[0]

            self.assertEqual(set(entry), {"task_type", "description"})
            self.assertNotIn("knowledge", entry)
            self.assertNotIn("allowed_tools", entry)

    def test_rejects_missing_and_unsupported_fields(self) -> None:
        for mutate, message in (
            (lambda manifest: manifest.pop("description"), "missing"),
            (lambda manifest: manifest.update({"fallback": {}}), "unsupported"),
            (lambda manifest: manifest["executor"].update({"provider": "gemini"}), "unsupported"),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                manifest = valid_manifest()
                mutate(manifest)
                root = Path(temp_dir) / "tasks"
                self._write(root, "search_image_task", manifest)
                with self.assertRaisesRegex(AsyncTaskManifestError, message):
                    AsyncTaskManifestLoader(root).load()

    def test_rejects_directory_id_mismatch_and_duplicate_registry_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "tasks"
            self._write(root, "different_task", valid_manifest())
            with self.assertRaisesRegex(AsyncTaskManifestError, "must match package directory"):
                AsyncTaskManifestLoader(root).load()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "tasks"
            self._write(root, "search_image_task", valid_manifest())
            definition = AsyncTaskManifestLoader(root).load().get("search_image_task")
            with self.assertRaisesRegex(AsyncTaskManifestError, "duplicate"):
                AsyncTaskManifestRegistry((definition, definition))

    def test_rejects_invalid_jsonpath_and_plural_path_for_single_value(self) -> None:
        for path in ("$..job_id", "$.jobs[?(@.ready)]", "$.jobs[0:2]", "$.jobs[*].id"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    compile_json_path(path, field="test.path", cardinality="single")
        with self.assertRaisesRegex(ValueError, "single value"):
            compile_json_path("$.jobs[*]", field="test.path", cardinality="single")

    def test_rejects_non_object_invalid_and_remote_ref_schemas(self) -> None:
        cases = (
            ({"type": "array"}, "root type"),
            ({"type": "object", "properties": []}, "Draft 2020-12"),
            (
                {"type": "object", "properties": {"value": {"$ref": "https://schemas.example/value"}}},
                "local reference",
            ),
        )
        for schema, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                manifest = valid_manifest()
                manifest["output"]["schema"] = schema
                root = Path(temp_dir) / "tasks"
                self._write(root, "search_image_task", manifest)
                with self.assertRaisesRegex(AsyncTaskManifestError, message):
                    AsyncTaskManifestLoader(root).load()

    def test_accepts_local_defs_reference(self) -> None:
        manifest = valid_manifest()
        manifest["output"]["schema"] = {
            "type": "object",
            "$defs": {"label": {"type": "string"}},
            "properties": {"label": {"$ref": "#/$defs/label"}},
        }
        manifest["output"].pop("resources", None)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "tasks"
            self._write(root, "search_image_task", manifest)
            AsyncTaskManifestLoader(root).load()

    def test_rejects_partial_unknown_and_key_input_placeholders(self) -> None:
        values = (
            {"query": "Hãy xử lý: {{task.instruction}}"},
            {"query": "{{task.unknown}}"},
            {"{{task.instruction}}": "value"},
        )
        for payload in values:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp_dir:
                manifest = valid_manifest()
                manifest["input"]["payload"] = payload
                root = Path(temp_dir) / "tasks"
                self._write(root, "search_image_task", manifest)
                with self.assertRaisesRegex(AsyncTaskManifestError, "placeholder"):
                    AsyncTaskManifestLoader(root).load()

    def test_rejects_job_placeholder_in_host_query_key_or_submit_url(self) -> None:
        changes = (
            ("status", "https://{{job_id}}.example.com/status"),
            ("status", "https://agent.example.com/status?{{job_id}}=value"),
            ("submit", "https://agent.example.com/tasks/{{job_id}}"),
        )
        for endpoint, url in changes:
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as temp_dir:
                manifest = valid_manifest()
                manifest["executor"][endpoint]["url"] = url
                root = Path(temp_dir) / "tasks"
                self._write(root, "search_image_task", manifest)
                with self.assertRaisesRegex(AsyncTaskManifestError, "job_id|query value|hostname"):
                    AsyncTaskManifestLoader(root).load()

    def test_rejects_insecure_url_literal_secret_and_invalid_state_mapping(self) -> None:
        mutations = (
            (
                lambda manifest: manifest["executor"]["status"].update(
                    {"url": "http://agent.example.com/jobs/{{job_id}}"}
                ),
                "HTTPS",
            ),
            (
                lambda manifest: manifest["executor"].update(
                    {"auth": {"type": "bearer", "token": "secret"}}
                ),
                "missing|unsupported",
            ),
            (
                lambda manifest: manifest["executor"]["status"]["states"]["failed"].append(
                    "completed"
                ),
                "mapped to both",
            ),
        )
        for mutate, message in mutations:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                manifest = valid_manifest()
                mutate(manifest)
                root = Path(temp_dir) / "tasks"
                self._write(root, "search_image_task", manifest)
                with self.assertRaisesRegex(AsyncTaskManifestError, message):
                    AsyncTaskManifestLoader(root).load()

    def test_rejects_duplicate_json_fields_and_non_json_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "tasks"
            package = root / "search_image_task"
            package.mkdir(parents=True)
            (package / "manifest.json").write_text(
                '{"id":"search_image_task","id":"other_task"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(AsyncTaskManifestError, "duplicate JSON field"):
                AsyncTaskManifestLoader(root).load()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "tasks"
            package = root / "search_image_task"
            package.mkdir(parents=True)
            raw = json.dumps(valid_manifest()).replace('"timeout_seconds": 60', '"timeout_seconds": NaN')
            (package / "manifest.json").write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(AsyncTaskManifestError, "non-JSON numeric"):
                AsyncTaskManifestLoader(root).load()

    @staticmethod
    def _write(root: Path, package_id: str, manifest: dict) -> None:
        package = root / package_id
        package.mkdir(parents=True)
        (package / "manifest.json").write_text(
            json.dumps(copy.deepcopy(manifest), ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
