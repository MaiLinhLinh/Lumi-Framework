"""Small trace helpers shared by the independent application."""

from __future__ import annotations

import contextvars
import logging
import uuid

TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")
_turn_id: contextvars.ContextVar[str] = contextvars.ContextVar("turn_id", default="----")


def begin_turn() -> str:
    value = uuid.uuid4().hex[:4]
    _turn_id.set(value)
    return value


def trace(message: str, *args: object) -> None:
    logging.getLogger("lumi.trace").log(TRACE_LEVEL, "turn=%s | " + message, _turn_id.get(), *args)


def warning(message: str, *args: object) -> None:
    logging.getLogger("lumi.trace").warning("turn=%s | " + message, _turn_id.get(), *args)
