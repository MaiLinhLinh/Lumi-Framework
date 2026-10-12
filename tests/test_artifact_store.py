"""Direct CP2 tests for final-only Artifact Store behavior."""

from __future__ import annotations

import unittest

from final_framework.artifacts import ArtifactStore, ArtifactStoreError
from final_framework.task_manager import Artifact, TaskContractError, TaskResult


class ArtifactStoreTests(unittest.TestCase):
    def test_replaces_one_task_final_output_and_keeps_runs_isolated(self) -> None:
        store = ArtifactStore()
        first = store.create(
            artifact_id="artifact_1",
            artifact_type="knowledge",
            run_id="run_1",
            produced_by="task_1",
            evidence_ref="#E1",
            data={"items": [{"candidate_id": "p1"}]},
        )
        replacement = store.create(
            artifact_id="artifact_2",
            artifact_type="knowledge",
            run_id="run_1",
            produced_by="task_1",
            evidence_ref="#E1",
            data={"items": [{"candidate_id": "p2"}]},
        )
        other_run = store.create(
            artifact_id="artifact_3",
            artifact_type="knowledge",
            run_id="run_2",
            produced_by="task_3",
            evidence_ref="#E1",
            data={"items": []},
        )

        self.assertFalse(hasattr(first, "version"))
        self.assertEqual(store.artifact_for_task("run_1", "task_1"), replacement)
        self.assertEqual(store.artifact_for_evidence("run_1", "#E1"), replacement)
        self.assertEqual(store.evidence_by_ref("run_1"), {"#E1": replacement})
        self.assertEqual(store.evidence_by_ref("run_2"), {"#E1": other_run})
        self.assertEqual(store.artifacts_for_run("run_1"), (replacement,))
        with self.assertRaisesRegex(ArtifactStoreError, "unknown artifact_id"):
            store.get("artifact_1")

    def test_rejects_artifact_id_owned_by_another_task(self) -> None:
        store = ArtifactStore()
        store.create(
            artifact_id="artifact_1",
            artifact_type="media",
            run_id="run_1",
            produced_by="task_1",
            data={"items": []},
            evidence_ref="#E1",
        )
        collision = Artifact(
            artifact_id="artifact_1",
            artifact_type="media",
            run_id="run_1",
            produced_by="task_2",
            data={"items": []},
            evidence_ref="#E2",
        )
        with self.assertRaisesRegex(ArtifactStoreError, "already exists"):
            store.write(collision)

    def test_task_result_and_serialization_use_one_artifact(self) -> None:
        artifact = Artifact(
            artifact_id="artifact_1",
            artifact_type="knowledge",
            run_id="run_1",
            produced_by="task_1",
            data={"items": []},
            evidence_ref="#E1",
        )
        result = TaskResult(
            task_id="task_1",
            status="succeeded",
            artifact=artifact,
            evidence_ref="#E1",
            metrics={"duration_ms": 10},
        )
        self.assertEqual(result.artifact, artifact)
        self.assertEqual(result.to_dict(), {
            "task_id": "task_1",
            "status": "succeeded",
            "artifact_id": "artifact_1",
            "evidence_ref": "#E1",
            "warnings": [],
            "metrics": {"duration_ms": 10},
        })
        self.assertNotIn("version", artifact.to_dict())

    def test_task_result_enforces_success_and_failure_invariant(self) -> None:
        artifact = Artifact(
            artifact_id="artifact_1",
            artifact_type="knowledge",
            run_id="run_1",
            produced_by="task_1",
            data={},
        )
        with self.assertRaisesRegex(TaskContractError, "succeeded.*artifact"):
            TaskResult(task_id="task_1", status="succeeded")
        with self.assertRaisesRegex(TaskContractError, "non-succeeded"):
            TaskResult(task_id="task_1", status="failed", artifact=artifact)


if __name__ == "__main__":
    unittest.main()
