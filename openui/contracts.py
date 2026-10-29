from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProgramSource:
    """Accepted OpenUI Lang source plus backend-owned statement bindings.

    ``source`` is the canonical UI payload sent to OpenUI's browser Renderer.
    Bindings and patch intent remain trusted runtime metadata owned by Lumi.
    """
    source: str
    slots: tuple[str, ...]
    used_widgets: tuple[str, ...]
    tasks: tuple[str, ...] = ()
    base_source: str | None = None
    # Patch intent, captured before merging inherited statements/defaults.
    state_updates: dict[str, dict[str, Any]] = field(default_factory=dict)
    prop_updates: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Evaluated Slot tree returned by the official OpenUI parser/evaluator.
    # It is used for Stage Map projection, never as browser render content.
    stage_slots: tuple[dict[str, Any], ...] = field(default=(), repr=False, compare=False)
