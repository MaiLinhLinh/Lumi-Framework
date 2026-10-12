"""Configuration owned only by ``final_framework``.

Values are read lazily by the running process from this project's own .env.
No secret is logged or copied by source code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_ASYNC_TASK_RESOURCE_MIME_TYPES = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "audio/mpeg",
    "audio/wav",
    "audio/ogg",
    "video/mp4",
    "video/webm",
    "application/pdf",
)


@dataclass(frozen=True)
class Settings:
    gemini_live_api_key: str
    gemini_live_model: str
    gemini_live_voice: str
    live_turn_timeout_seconds: float
    live_idle_timeout_seconds: float
    live_reconnect_grace_seconds: float
    presentation_animation_delay_ms: int
    cerebras_api_key: str = ""
    cerebras_manager_model: str = "gpt-oss-120b"
    cerebras_assembly_model: str = "gpt-oss-120b"
    gemini_information_api_key: str = ""
    gemini_information_model: str = ""
    gemini_media_key: str = ""
    gemini_media_model: str = ""
    enable_search_image_task: bool = True
    gemini_draw_key: str = ""
    gemini_draw_model: str = ""
    async_task_max_job_id_chars: int = 2048
    async_task_max_response_bytes: int = 16 * 1024 * 1024
    async_task_max_json_depth: int = 64
    async_task_max_collection_items: int = 100_000
    async_task_cancel_timeout_seconds: float = 10.0
    knowledge_tool_token_ttl_seconds: float = 120.0
    async_task_resource_allowed_mime_types: tuple[str, ...] = (
        DEFAULT_ASYNC_TASK_RESOURCE_MIME_TYPES
    )


def load_settings() -> Settings:
    load_dotenv(APP_ROOT / ".env", override=False)

    def integer(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    def boolean(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def positive_integer(name: str, default: int) -> int:
        value = integer(name, default)
        return value if value > 0 else default

    def positive_float(name: str, default: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except ValueError:
            return default
        return value if value > 0 else default

    def csv_tokens(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        raw = os.getenv(name)
        if raw is None:
            return default
        values = tuple(token.strip().lower() for token in raw.split(",") if token.strip())
        return values or default

    return Settings(
        gemini_live_api_key=os.getenv("GEMINI_LIVE_API_KEY", "").strip(),
        gemini_live_model=(os.getenv("GEMINI_LIVE_SPEECH_MODEL") or os.getenv("GEMINI_LIVE_MODEL") or "gemini-3.1-flash-live-preview").strip(),
        gemini_live_voice=os.getenv("GEMINI_LIVE_VOICE", "kore").strip() or "kore",
        live_turn_timeout_seconds=float(os.getenv("LIVE_TURN_TIMEOUT_SECONDS", "45")),
        live_idle_timeout_seconds=float(os.getenv("LIVE_IDLE_TIMEOUT_SECONDS", "900")),
        live_reconnect_grace_seconds=float(os.getenv("LIVE_RECONNECT_GRACE_SECONDS", "30")),
        presentation_animation_delay_ms=max(0, integer("PRESENTATION_ANIMATION_DELAY_MS", 300)),
        cerebras_api_key=os.getenv("CEREBRAS_API_KEY", "").strip(),
        cerebras_manager_model=(os.getenv("CEREBRAS_MANAGER_MODEL", "gpt-oss-120b").strip() or "gpt-oss-120b"),
        cerebras_assembly_model=(os.getenv("CEREBRAS_ASSEMBLY_MODEL", "gpt-oss-120b").strip() or "gpt-oss-120b"),
        gemini_information_api_key=os.getenv("GEMINI_INFORMATION_API_KEY", "").strip(),
        gemini_information_model=os.getenv("GEMINI_INFORMATION_MODEL", "").strip(),
        gemini_media_key=os.getenv("GEMINI_MEDIA_KEY", "").strip(),
        gemini_media_model=os.getenv("GEMINI_MEDIA_MODEL", "").strip(),
        enable_search_image_task=boolean("ENABLE_SEARCH_IMAGE_TASK", True),
        gemini_draw_key=os.getenv("GEMINI_DRAW_KEY", "").strip(),
        gemini_draw_model=os.getenv("GEMINI_DRAW_MODEL", "").strip(),
        async_task_max_job_id_chars=positive_integer(
            "ASYNC_TASK_MAX_JOB_ID_CHARS", 2048
        ),
        async_task_max_response_bytes=positive_integer(
            "ASYNC_TASK_MAX_RESPONSE_BYTES", 16 * 1024 * 1024
        ),
        async_task_max_json_depth=positive_integer(
            "ASYNC_TASK_MAX_JSON_DEPTH", 64
        ),
        async_task_max_collection_items=positive_integer(
            "ASYNC_TASK_MAX_COLLECTION_ITEMS", 100_000
        ),
        async_task_cancel_timeout_seconds=positive_float(
            "ASYNC_TASK_CANCEL_TIMEOUT_SECONDS", 10.0
        ),
        knowledge_tool_token_ttl_seconds=positive_float(
            "KNOWLEDGE_TOOL_TOKEN_TTL_SECONDS", 120.0
        ),
        async_task_resource_allowed_mime_types=csv_tokens(
            "ASYNC_TASK_RESOURCE_ALLOWED_MIME_TYPES",
            DEFAULT_ASYNC_TASK_RESOURCE_MIME_TYPES,
        ),
    )
