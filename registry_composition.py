"""Composition root for framework-owned, startup-validated registries.

Task Manager executors receive shared resources, widget/effect extensions, and
one independently loaded knowledge registry local to ``lumi_openui``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .remote_tasks.manifest import (
    AsyncTaskManifestError,
    AsyncTaskManifestLoader,
    AsyncTaskManifestRegistry,
)
from .knowledge.registry import KnowledgeRegistry
from .catalogs.resources import SharedResourceRegistry, SharedResources
from .extension_loader import LoadedExtensions, ExtensionLoader
from .task_catalog import (
    RUNTIME_OWNED_TASK_TYPES,
    STATIC_TASK_TYPES,
    enabled_static_task_catalog,
    merge_task_catalog,
)
class FrameworkRegistryError(ValueError):
    """The independent registry composition could not be initialized."""


@dataclass(frozen=True, slots=True)
class FrameworkRegistries:
    """The registry set shared by future Task Manager executors."""

    root: Path
    shared_resources: SharedResources
    extensions: LoadedExtensions
    knowledge_registry: KnowledgeRegistry | None
    task_manifest_registry: AsyncTaskManifestRegistry

    @property
    def widget_registry(self):
        return self.extensions.widget_registry

    @property
    def effect_registry(self):
        return self.extensions.effect_registry

    def template_catalog(self):
        return self.shared_resources.templates

    def asset_catalog(self):
        return self.shared_resources.assets

    def task_catalog(self, settings: object) -> tuple[dict[str, str], ...]:
        """Return the public catalog shared with Task Manager Agent."""

        try:
            merged = merge_task_catalog(
                enabled_static_task_catalog(settings),
                self.task_manifest_registry.public_catalog(),
            )
        except ValueError as exc:
            raise FrameworkRegistryError(str(exc)) from exc
        return tuple(dict(item) for item in merged)


def load_framework_registries(
    root: Path,
    *,
    knowledge_registry: KnowledgeRegistry | None = None,
) -> FrameworkRegistries:
    """Load local UI registries; caller may supply one retrieval registry.

    Retrieval backends need model/store configuration and are constructed by a
    task executor in later checkpoints.  This function never reads or modifies
    ``.env`` and never initializes a network client.
    """

    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise FrameworkRegistryError("framework root must be an existing directory.")
    resources = SharedResourceRegistry(resolved_root / "resources").load()
    extensions = ExtensionLoader(resolved_root / "extensions").load()
    try:
        task_manifests = AsyncTaskManifestLoader(
            resolved_root / "extensions" / "tasks"
        ).load()
    except AsyncTaskManifestError as exc:
        raise FrameworkRegistryError(str(exc)) from exc
    reserved_task_types = STATIC_TASK_TYPES | RUNTIME_OWNED_TASK_TYPES
    conflicts = set(task_manifests.task_types()) & reserved_task_types
    if conflicts:
        raise FrameworkRegistryError(
            "Async Task manifest IDs conflict with static or Runtime-owned task types: "
            + ", ".join(sorted(conflicts))
            + "."
        )
    if knowledge_registry is not None and not isinstance(
        knowledge_registry, KnowledgeRegistry
    ):
        raise FrameworkRegistryError(
            "knowledge_registry must be a KnowledgeRegistry."
        )
    return FrameworkRegistries(
        root=resolved_root,
        shared_resources=resources,
        extensions=extensions,
        knowledge_registry=knowledge_registry,
        task_manifest_registry=task_manifests,
    )
