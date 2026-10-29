"""Small trace helpers shared by the independent application."""

from __future__ import annotations

import contextvars
import logging
import uuid
from collections.abc import Mapping

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


def _usage_value(usage: object, name: str) -> object:
    return usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)


def _token_count(usage: object, name: str) -> int | None:
    value = _usage_value(usage, name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def token_counts(usage: object, *, api: str) -> dict[str, int | None]:
    """Normalize provider usage; output_tokens always excludes thinking.

    Cerebras completion_tokens includes reasoning. Gemini candidate/response
    counts and Interactions total_output_tokens already exclude thoughts.
    Missing counters remain unknown, including missing reasoning metadata.
    """
    if api == "cerebras":
        input_tokens = _token_count(usage, "prompt_tokens")
        thinking = _token_count(_usage_value(usage, "completion_tokens_details"), "reasoning_tokens")
        combined = _token_count(usage, "completion_tokens")
        output = combined - thinking if combined is not None and thinking is not None and combined >= thinking else None
        total = _token_count(usage, "total_tokens")
    elif api in ("gemini", "gemini_live", "gemini_interactions"):
        if api == "gemini_interactions":
            input_name, output_name, thinking_name, total_name = (
                "total_input_tokens", "total_output_tokens", "total_thought_tokens", "total_tokens"
            )
        else:
            input_name, output_name, thinking_name, total_name = (
                "prompt_token_count",
                "response_token_count" if api == "gemini_live" else "candidates_token_count",
                "thoughts_token_count", "total_token_count",
            )
        input_tokens = _token_count(usage, input_name)
        thinking = _token_count(usage, thinking_name)
        output = _token_count(usage, output_name)
        combined = output + thinking if output is not None and thinking is not None else None
        total = _token_count(usage, total_name)
    else:
        raise ValueError(f"Unsupported usage API: {api}")
    return {
        "input_tokens": input_tokens,
        "thinking_tokens": thinking,
        "output_tokens": output,
        "output_tokens_including_thinking": combined,
        "total_tokens": total,
    }


def log_token_usage(*, agent: str, model: str, api: str, usage: object, **context: object) -> None:
    """Write runtime token usage without prompts, response content or secrets.

    Live records are provider updates, not independent calls to sum together.
    """
    fields = {"agent": agent, "api": api, "model": model, **context, **token_counts(usage, api=api)}
    rendered = " ".join(f"{key}={value if value is not None else 'N/A'}" for key, value in fields.items())
    logging.getLogger("lumi.trace").info("turn=%s | [LLM:USAGE] %s", _turn_id.get(), rendered)
