"""Native Gemini image-generation adapter for ``draw_image_task``.

This adapter is intentionally independent from information/media retrieval.
It sends an instruction to one configured Nano Banana model and returns image
bytes to the existing provider-neutral ``GeneratedImageResult`` boundary.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Callable

try:  # Keep contract imports usable when the deployment SDK is absent.
    from google import genai
    from google.genai import types
except ModuleNotFoundError:  # pragma: no cover - deployment dependency boundary.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from final_framework.agent_tasks import AgentTaskRequest
from final_framework.media.generation import (
    GeneratedImageResult,
    ImageGenerationError,
    validate_draw_configuration,
)
from final_framework.settings import Settings


logger = logging.getLogger("lumi.image_generation")

DEFAULT_REQUEST_TIMEOUT_SECONDS = 45.0
ClientFactory = Callable[..., Any]


class GeminiDrawImageAgent:
    """Gemini/Nano Banana implementation for exactly ``draw_image_task``."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(settings, Settings):
            raise TypeError("settings must be a Settings instance.")
        validate_draw_configuration(settings)
        if request_timeout_seconds <= 0:
            raise ImageGenerationError("draw request_timeout_seconds must be positive.")
        if types is None:
            raise ImageGenerationError("google-genai is required for draw_image_task.")
        if client_factory is None:
            if genai is None:
                raise ImageGenerationError("google-genai is required for draw_image_task.")
            client_factory = genai.Client

        self._settings = settings
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._client = client_factory(
            api_key=settings.gemini_draw_key,
            http_options=types.HttpOptions(timeout=round(request_timeout_seconds * 1000)),
        )

    @property
    def model_name(self) -> str:
        return self._settings.gemini_draw_model

    async def generate(self, request: AgentTaskRequest) -> GeneratedImageResult:
        if not isinstance(request, AgentTaskRequest):
            raise TypeError("request must be an AgentTaskRequest.")
        if request.task.task_type != "draw_image_task":
            raise ImageGenerationError("GeminiDrawImageAgent requires draw_image_task.")
        instruction = request.task.instruction

        prompt = _draw_prompt(instruction=instruction)
        started_at = time.monotonic()
        logger.info(
            "[DRAW:START] run=%s task=%s model=%s instruction=%r",
            request.task.run_id,
            request.task.task_id,
            self.model_name,
            instruction,
        )
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.interactions.create,
                    model=self.model_name,
                    input=prompt,
                ),
                timeout=self._request_timeout_seconds,
            )
        except Exception as exc:
            logger.exception(
                "[DRAW:MODEL_FAILED] run=%s task=%s model=%s",
                request.task.run_id,
                request.task.task_id,
                self.model_name,
            )
            raise ImageGenerationError("Gemini image generation request failed.") from exc

        image = getattr(response, "output_image", None)
        image_bytes = _image_bytes(getattr(image, "data", None))
        if not image_bytes:
            logger.warning(
                "[DRAW:INVALID_RESPONSE] run=%s task=%s reason=no_image_output",
                request.task.run_id,
                request.task.task_id,
            )
            raise ImageGenerationError("Gemini image generation returned no image output.")
        mime_type = _image_mime_type(image)
        duration_ms = round((time.monotonic() - started_at) * 1000)
        metadata = {
            "provider": "gemini",
            "model": self.model_name,
        }
        response_id = getattr(response, "id", None)
        if isinstance(response_id, str) and response_id.strip():
            metadata["provider_response_id"] = response_id.strip()
        logger.info(
            "[DRAW:READY] run=%s task=%s bytes=%s mime=%s duration_ms=%s",
            request.task.run_id,
            request.task.task_id,
            len(image_bytes),
            mime_type,
            duration_ms,
        )
        return GeneratedImageResult(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption=instruction.strip(),
            generation_metadata=metadata,
            duration_ms=duration_ms,
        )


def _draw_prompt(*, instruction: str) -> str:
    return (
        "Create exactly one image for an interactive Lumi panel. "
        "Follow the visual instruction faithfully. Do not return explanation, markdown, "
        "a URL, or text-only output. Avoid readable text inside the illustration unless "
        "the instruction explicitly requests it.\n\n"
        f"Visual instruction: {instruction.strip()}"
    )


def _image_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str) or not value.strip():
        return b""
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, UnicodeEncodeError):
        return b""


def _image_mime_type(image: object) -> str:
    value = getattr(image, "mime_type", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    # Gemini native image output is PNG unless its response supplies a MIME type.
    return "image/png"
