"""Gemini Live tool contract for validated active-surface state changes."""

from __future__ import annotations


UPDATE_SURFACE_STATE_TOOL = {
    "name": "update_surface_state",
    "description": (
        "Change allowed runtime state for one or more anchored regions on the active surface. "
        "Use visibility='visible' to reveal a currently hidden region. "
        "For NumberDisplay, changes.value is the new displayed string (at most 20 characters), "
        "for example {'value':'120,000đ'}. Use this tool for dynamic values instead of replanning. "
        "Only fields declared in runtime_slots.state_fields are allowed. All updates apply atomically. "
        "Unchanged values are accepted. Use the returned revision and state for subsequent calls."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["surface_id", "base_revision", "updates"],
        "properties": {
            "surface_id": {"type": "string"},
            "base_revision": {"type": "integer", "minimum": 1},
            "updates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["anchor_id", "changes"],
                    "properties": {
                        "anchor_id": {"type": "string"},
                        "changes": {"type": "object", "minProperties": 1},
                    },
                },
            },
        },
    },
}
