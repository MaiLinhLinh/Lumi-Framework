"""Shared persistent-session states and allowed state transitions."""

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


