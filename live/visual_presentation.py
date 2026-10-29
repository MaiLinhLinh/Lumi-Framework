"""Present-tool contract for cues on the active compiled surface."""

from __future__ import annotations

PRESENT_VISUAL_TOOL = {
    "name": "present_visual",
    "description": "Animate one verified visual anchor on the active panel.",
    "parameters": {
        "type": "object",
        "properties": {"anchor_id": {"type": "string"}, "effect_id": {"type": "string"}},
        "required": ["anchor_id", "effect_id"],
        "additionalProperties": False,
    },
}

