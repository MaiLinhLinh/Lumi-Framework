"""Contracts for the independent ``draw_image_task``.

This CP7 module deliberately contains no Gemini client or provider-specific
response parsing.  A concrete adapter is bound only after its image model and
API contract have been selected.  The executor can nevertheless be tested
against this stable result boundary now.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from final_framework.settings import Settings
from final_framework.task_manager.execution import TaskExecutionContext


class ImageGenerationError(RuntimeError):
    """The independent image-generation task cannot produce a valid image."""


def validate_draw_configuration(settings: Settings) -> None:
    """Check configuration only; this function never creates an API client.

    Keeping this at the Draw Agent boundary makes a missing model a clear
    configuration failure when a future Gemini adapter is bound, rather than
    silently trying to execute with an unspecified model.
    """

    if not settings.gemini_draw_key:
        raise ImageGenerationError("GEMINI_DRAW_KEY is not configured.")
    if not settings.gemini_draw_model:
        raise ImageGenerationError("GEMINI_DRAW_MODEL is not configured.")


@dataclass(frozen=True, slots=True)
class GeneratedImageResult:
    """Provider-neutral image result consumed by ``DrawImageTaskExecutor``.

    ``generation_metadata`` remains internal provenance.  It can later record
    a selected model, provider request ID, or safety metadata without changing
    the Surface JSON contract.
    """

    image_bytes: bytes
    mime_type: str
    caption: str
    generation_metadata: Mapping[str, Any]
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.image_bytes, bytes) or not self.image_bytes:
            raise ImageGenerationError("generated image_bytes must be non-empty bytes.")
        if not isinstance(self.mime_type, str) or not self.mime_type.strip():
            raise ImageGenerationError("generated mime_type must be a non-empty string.")
        if not isinstance(self.caption, str) or not self.caption.strip():
            raise ImageGenerationError("generated caption must be a non-empty string.")
        if not isinstance(self.generation_metadata, Mapping):
            raise ImageGenerationError("generation_metadata must be an object.")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int) or self.duration_ms < 0:
            raise ImageGenerationError("duration_ms must be a non-negative integer.")
        object.__setattr__(self, "mime_type", self.mime_type.strip())
        object.__setattr__(self, "caption", self.caption.strip())
        object.__setattr__(self, "generation_metadata", dict(self.generation_metadata))


class ImageGenerationAgent(Protocol):
    """Independent provider boundary for exactly one ``draw_image_task``."""

    async def generate(self, context: TaskExecutionContext) -> GeneratedImageResult: ...
