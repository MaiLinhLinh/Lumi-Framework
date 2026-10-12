"""Gemini Live contract for closing the active surface."""

from __future__ import annotations


DELETE_SURFACE_TOOL = {
    "name": "delete_surface",
    "description": (
        "Close the current visual surface when it is no longer needed. "
        "Use the current surface_id and base_revision from the active panel context."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["surface_id", "base_revision"],
        "properties": {
            "surface_id": {"type": "string"},
            "base_revision": {"type": "integer", "minimum": 1},
        },
    },
}
