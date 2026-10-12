"""Shared persistent-session state and browser event vocabulary.

This module deliberately contains no Gemini transport or business-category logic.  It is
the contract that the persistent transport, orchestrator and browser will use
in subsequent checkpoints.
"""

from __future__ import annotations

from enum import StrEnum


class LiveSessionState(StrEnum):
    """Technical state of one persistent Live session.

    Activity-specific state (for example ``awaiting_answer``) is stored with
    the active surface and must not be represented here.
    """

    IDLE = "idle"
    LISTENING = "listening"
    WAITING_FOR_TOOL = "waiting_for_tool"
    SPEAKING = "speaking"
    ERROR = "error"


# State transitions are intentionally explicit so that a later transport cannot
# accidentally accept microphone audio while a presentation scene is speaking.
ALLOWED_STATE_TRANSITIONS: dict[LiveSessionState, frozenset[LiveSessionState]] = {
    LiveSessionState.IDLE: frozenset({LiveSessionState.LISTENING, LiveSessionState.ERROR}),
    LiveSessionState.LISTENING: frozenset({LiveSessionState.WAITING_FOR_TOOL, LiveSessionState.SPEAKING, LiveSessionState.IDLE, LiveSessionState.ERROR}),
    LiveSessionState.WAITING_FOR_TOOL: frozenset({LiveSessionState.SPEAKING, LiveSessionState.LISTENING, LiveSessionState.ERROR}),
    LiveSessionState.SPEAKING: frozenset({LiveSessionState.LISTENING, LiveSessionState.IDLE, LiveSessionState.ERROR}),
    LiveSessionState.ERROR: frozenset({LiveSessionState.IDLE}),
}


def can_transition(current: LiveSessionState, target: LiveSessionState) -> bool:
    """Return whether a technical state transition is permitted."""

    return target in ALLOWED_STATE_TRANSITIONS[current]


# Browser/backend event names reserved for persistent-session control.  Surface
# tools and presentation events remain category-neutral and use their existing
# contracts (``panel``, ``scene``, ``tool_result`` and PCM frames).
PERSISTENT_BROWSER_EVENTS = frozenset(
    {
        "live:session_ready",
        "live:state",
        "live:audio_begin",
        "live:audio_end",
        "live:timeout",
        "live:reconnecting",
        "live:reconnected",
        "live:closed",
        "live:error",
    }
)
