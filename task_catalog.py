"""Public task catalog shared by Task Manager Agent and Runtime composition."""

from __future__ import annotations

from collections.abc import Mapping


STATIC_TASK_CATALOG: tuple[Mapping[str, str], ...] = (
    {
        "task_type": "search_information_task",
        "description": "Tìm fact, passage hoặc tư liệu chữ cần xác minh.",
    },
    {
        "task_type": "search_image_task",
        "description": "Tìm ảnh hoặc tư liệu media cần nguồn.",
    },
    {
        "task_type": "draw_image_task",
        "description": "Tạo một minh họa mới khi mục tiêu cần ảnh được tạo.",
    },
)

STATIC_TASK_TYPES = frozenset(item["task_type"] for item in STATIC_TASK_CATALOG)
RUNTIME_OWNED_TASK_TYPES = frozenset({
    "complete_surface_task",
    "surface_draft",
    "surface_task",
})


def enabled_static_task_catalog(settings: object) -> tuple[Mapping[str, str], ...]:
    """Return static tasks accepted by this Runtime configuration."""

    if getattr(settings, "enable_search_image_task", True):
        return STATIC_TASK_CATALOG
    return tuple(
        item
        for item in STATIC_TASK_CATALOG
        if item["task_type"] != "search_image_task"
    )


def merge_task_catalog(
    static_catalog: tuple[Mapping[str, str], ...],
    manifest_catalog: tuple[Mapping[str, str], ...],
) -> tuple[Mapping[str, str], ...]:
    """Merge already-validated entries while enforcing one public task ID."""

    merged: list[Mapping[str, str]] = []
    seen: set[str] = set()
    for item in (*static_catalog, *manifest_catalog):
        task_type = item.get("task_type")
        description = item.get("description")
        if not isinstance(task_type, str) or not task_type.strip():
            raise ValueError("task catalog entries require a non-empty task_type.")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("task catalog entries require a non-empty description.")
        normalized = task_type.strip()
        if normalized in seen:
            raise ValueError(f"duplicate task catalog id '{normalized}'.")
        if normalized in RUNTIME_OWNED_TASK_TYPES:
            raise ValueError(
                f"Runtime-owned task type '{normalized}' may not enter the public catalog."
            )
        seen.add(normalized)
        merged.append({
            "task_type": normalized,
            "description": description.strip(),
        })
    return tuple(merged)


__all__ = [
    "RUNTIME_OWNED_TASK_TYPES",
    "STATIC_TASK_CATALOG",
    "STATIC_TASK_TYPES",
    "enabled_static_task_catalog",
    "merge_task_catalog",
]
