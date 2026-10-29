"""Framework-neutral contracts for generated visual panels."""

from .contracts import (
    AnchorBinding,
    ChoiceChild,
    ComponentChild,
    ComponentNode,
    PlanBlock,
    GridRect,
)
__all__ = [
    "AnchorBinding",
    "ChoiceChild",
    "ComponentChild",
    "ComponentNode",
    "PlanBlock",
    "GridRect",
    "render_visual_stage_map",
]


def __getattr__(name: str):
    """Keep lightweight surface contracts usable without loading map dependencies."""

    renderer_exports = {"render_visual_stage_map"}
    if name in renderer_exports:
        from . import renderers

        return getattr(renderers, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
