"""In-memory final-only Artifact Store."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from lumi_openui.task_manager import Artifact


class ArtifactStoreError(RuntimeError):
    """Artifact persistence or lookup failed its runtime contract."""


class ArtifactStore:
    """Keep one current final Artifact per ``(run_id, produced_by)`` task."""

    def __init__(self) -> None:
        self._by_id: dict[str, Artifact] = {}
        self._by_task: dict[tuple[str, str], str] = {}
        self._by_run: dict[str, OrderedDict[str, str]] = {}
        self._by_evidence: dict[tuple[str, str], str] = {}

    def create(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        run_id: str,
        produced_by: str,
        data: Mapping[str, Any],
        evidence_ref: str | None = None,
    ) -> Artifact:
        """Construct and atomically persist one final Artifact."""

        return self.write(Artifact(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            run_id=run_id,
            produced_by=produced_by,
            data=data,
            evidence_ref=evidence_ref,
        ))

    def write(self, artifact: Artifact) -> Artifact:
        """Atomically install or replace the final output of one task."""

        if not isinstance(artifact, Artifact):
            raise ArtifactStoreError("artifact must be an Artifact instance.")
        task_key = (artifact.run_id, artifact.produced_by)
        previous_id = self._by_task.get(task_key)
        id_owner = self._by_id.get(artifact.artifact_id)
        if id_owner is not None and (
            id_owner.run_id != artifact.run_id or id_owner.produced_by != artifact.produced_by
        ):
            raise ArtifactStoreError(f"artifact_id '{artifact.artifact_id}' already exists.")

        previous = self._by_id.get(previous_id) if previous_id is not None else None
        run_index = self._by_run.setdefault(artifact.run_id, OrderedDict())
        if previous is not None:
            self._by_id.pop(previous.artifact_id, None)
            if previous.evidence_ref is not None:
                evidence_key = (previous.run_id, previous.evidence_ref)
                if self._by_evidence.get(evidence_key) == previous.artifact_id:
                    self._by_evidence.pop(evidence_key, None)

        self._by_id[artifact.artifact_id] = artifact
        self._by_task[task_key] = artifact.artifact_id
        run_index[artifact.produced_by] = artifact.artifact_id
        if artifact.evidence_ref is not None:
            self._by_evidence[(artifact.run_id, artifact.evidence_ref)] = artifact.artifact_id
        return artifact

    def get(self, artifact_id: str) -> Artifact:
        try:
            return self._by_id[artifact_id]
        except KeyError as exc:
            raise ArtifactStoreError(f"unknown artifact_id '{artifact_id}'.") from exc

    def artifact_for_task(self, run_id: str, produced_by: str) -> Artifact | None:
        artifact_id = self._by_task.get((run_id, produced_by))
        return self._by_id.get(artifact_id) if artifact_id is not None else None

    def artifacts_for_run(self, run_id: str) -> tuple[Artifact, ...]:
        """Return the current final Artifact of every successful task in a run."""

        return tuple(
            self._by_id[artifact_id]
            for artifact_id in self._by_run.get(run_id, {}).values()
        )

    def artifact_for_evidence(self, run_id: str, evidence_ref: str) -> Artifact | None:
        artifact_id = self._by_evidence.get((run_id, evidence_ref))
        return self._by_id.get(artifact_id) if artifact_id is not None else None

    def evidence_by_ref(self, run_id: str) -> dict[str, Artifact]:
        """Return the single current Artifact selected for every evidence ref."""

        return {
            evidence_ref: self._by_id[artifact_id]
            for (candidate_run_id, evidence_ref), artifact_id in self._by_evidence.items()
            if candidate_run_id == run_id
        }
