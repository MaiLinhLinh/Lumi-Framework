"""Standalone web entrypoint for the domainless compiled-surface application."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lumi_openui.live.gemini_session import GeminiLiveSession, GeminiLiveSessionError
from lumi_openui.live.persistent_transport import PersistentLiveTransportStore
from lumi_openui.live.registry import LiveToolRegistry
from lumi_openui.catalogs.resources import SharedResourceError
from lumi_openui.live.surface_runtime import SurfaceRuntime
from lumi_openui.live.task_runtime import LiveTaskRuntime
from lumi_openui.task_manager_agent import TaskManagerAgent
from lumi_openui.knowledge import (
    ChromaKnowledgeStore,
    FilesystemMediaStore,
    GeminiEmbeddingProvider,
    KnowledgeGateway,
    KnowledgeGrantRegistry,
    MediaReferenceStore,
    MediaStoreError,
)
from lumi_openui.knowledge.http import knowledge_gateway_routes
from lumi_openui.knowledge.runtime import build_knowledge_registry
from lumi_openui.registry_composition import load_framework_registries
from lumi_openui.settings import load_settings
from lumi_openui.trace import TRACE_LEVEL, trace
from lumi_openui.frontend_assets import FrontendBuild

ROOT = Path(__file__).resolve().parent
frontend_build = FrontendBuild(ROOT)
WEB = frontend_build.frontend
logger = logging.getLogger("lumi.gemini_live.web")
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()
_cleanup_tasks: dict[str, asyncio.Task[None]] = {}


def _trace_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def configure_logging() -> None:
    for name in (
        "lumi.trace",
        "lumi.gemini_live",
        "lumi.gemini_live.web",
        "lumi.task_manager",
        "lumi.task_manager_agent",
        "lumi.live_task_runtime",
        "lumi.surface_runtime",
        "lumi.task_runtime",
        "lumi.information_retrieval",
        "lumi.media_retrieval",
        "lumi.image_generation",
        "lumi.surface_assembly",
    ):
        current = logging.getLogger(name)
        current.setLevel(TRACE_LEVEL if name == "lumi.trace" else logging.INFO)
        current.propagate = False
        if not current.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"
            ))
            current.addHandler(handler)


configure_logging()
settings = load_settings()
knowledge_store = ChromaKnowledgeStore(
    GeminiEmbeddingProvider(settings.gemini_information_api_key),
)
knowledge_media_store = FilesystemMediaStore()
media_reference_store = MediaReferenceStore(knowledge_media_store)
knowledge_registry = build_knowledge_registry(
    knowledge_store,
    media_store=knowledge_media_store,
)
knowledge_gateway = KnowledgeGateway(
    knowledge_registry,
    KnowledgeGrantRegistry(
        default_ttl_seconds=settings.knowledge_tool_token_ttl_seconds
    ),
)
registries = load_framework_registries(
    ROOT,
    knowledge_registry=knowledge_registry,
)
browser_extension_catalog = frontend_build.browser_catalog(
    registries.extensions.browser_catalog(url_prefix="/extensions")
)
asset_urls = {
    asset.id: f"/assets/resources/{asset.id}"
    for asset in registries.asset_catalog().assets
}
surface_runtime = SurfaceRuntime(
    widget_registry=registries.widget_registry,
    asset_catalog=registries.asset_catalog(),
    effect_registry=registries.effect_registry,
    asset_urls=asset_urls,
)


def _task_runtime_factory(session_id: str, on_task_event: Any, on_draft: Any, on_complete: Any) -> LiveTaskRuntime:
    return LiveTaskRuntime(
        session_id=session_id,
        settings=settings,
        registries=registries,
        media_reference_store=media_reference_store,
        on_task_event=on_task_event,
        on_draft=on_draft,
        on_complete=on_complete,
        knowledge_gateway=knowledge_gateway,
    )


registry = LiveToolRegistry()
task_manager_agent = TaskManagerAgent.from_settings(settings=settings, registries=registries)
live_session = GeminiLiveSession(
    settings=settings,
    registry=registry,
    surface_runtime=surface_runtime,
    task_runtime_factory=_task_runtime_factory,
    task_manager_agent=task_manager_agent,
)
persistent_transports = PersistentLiveTransportStore()


async def _session_lock(session_id: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault(session_id, asyncio.Lock())


async def _cancel_scheduled_cleanup(session_id: str) -> None:
    task = _cleanup_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _close_after_grace(session_id: str) -> None:
    try:
        await asyncio.sleep(settings.live_reconnect_grace_seconds)
        await persistent_transports.close(session_id)
        surface_runtime.reset_session_state(session_id)
        logger.info("[WEB:PERSISTENT_CLEANUP] session=%s", session_id)
    except asyncio.CancelledError:
        raise
    finally:
        _cleanup_tasks.pop(session_id, None)


def _schedule_cleanup(session_id: str) -> None:
    if session_id and session_id not in _cleanup_tasks:
        _cleanup_tasks[session_id] = asyncio.create_task(_close_after_grace(session_id))


async def home(_: Any) -> FileResponse:
    return FileResponse(WEB / "index.html")


async def health(_: Any) -> JSONResponse:
    return JSONResponse({
        "ok": bool(settings.gemini_live_api_key),
        "mode": "lumi-openui",
        "task_types": [
            "search_information_task",
            *( ["search_image_task"] if settings.enable_search_image_task else [] ),
            "draw_image_task",
            "complete_surface_task",
        ],
    })


async def shared_asset(request: Any) -> FileResponse | PlainTextResponse:
    """Serve only assets declared by the trusted shared catalog."""

    try:
        asset = registries.asset_catalog().get(str(request.path_params["asset_id"]))
    except (SharedResourceError, ValueError):
        return PlainTextResponse("Asset not found.", status_code=404)
    return FileResponse(asset.path, media_type=asset.mime_type, headers={"Cache-Control": "public, max-age=3600"})


async def knowledge_media(request: Any) -> FileResponse | PlainTextResponse:
    """Serve only validated local media objects from the knowledge store."""

    object_key = str(request.path_params["object_key"])
    try:
        path = knowledge_media_store.path(object_key)
    except MediaStoreError:
        return PlainTextResponse("Media not found.", status_code=404)
    if not path.is_file():
        return PlainTextResponse("Media not found.", status_code=404)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})


async def client_debug(request: Any) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {"message": "invalid client diagnostic"}
    if not isinstance(payload, dict):
        payload = {"message": str(payload)}
    logger.info("[WEB:CLIENT_DIAGNOSTIC] phase=%s message=%s source=%s line=%s", str(payload.get("phase") or "unknown")[:80], str(payload.get("message") or "")[:500], str(payload.get("source") or "")[:300], payload.get("line") or "")
    return JSONResponse({"ok": True})


async def live_socket(websocket: WebSocket) -> None:
    """Keep the browser socket and one Gemini Live connection persistent."""
    await websocket.accept()
    conversation: Any | None = None
    text_task: asyncio.Task[Any] | None = None
    stream_task: asyncio.Task[Any] | None = None
    session_id = ""
    microphone_enabled = False
    try:
        start = await websocket.receive_json()
        if not isinstance(start, dict) or start.get("type") != "live:connect":
            raise ValueError("Sự kiện đầu tiên phải là live:connect.")
        session_id = str(start.get("session_id") or uuid.uuid4())[:200]
        lock = await _session_lock(session_id)
        async with lock:
            await _cancel_scheduled_cleanup(session_id)
            send_lock = asyncio.Lock()

            async def event(payload: dict[str, Any]) -> None:
                if payload.get("type") == "openui:document":
                    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
                    logger.info(
                        "[WEB:OPENUI_DOCUMENT_SENT] session=%s surface=%s revision=%s slots=%d source_chars=%d",
                        session_id,
                        document.get("surface_id") or "-",
                        document.get("revision") or "-",
                        len(document.get("slots", {})) if isinstance(document.get("slots"), dict) else 0,
                        len(document.get("source", "")) if isinstance(document.get("source"), str) else 0,
                    )
                async with send_lock:
                    await websocket.send_json(payload)

            last_audio_diagnostic_turn: str | None = None

            async def audio(pcm: bytes, _: int, marker: dict[str, Any] | None = None, turn_id: str = "") -> None:
                nonlocal last_audio_diagnostic_turn
                started = time.perf_counter()
                diagnostic = turn_id != last_audio_diagnostic_turn or marker is not None
                last_audio_diagnostic_turn = turn_id
                if diagnostic:
                    trace("WEB_AUDIO_SEND_BEGIN session=%s output_turn=%s bytes=%s marker=%s", session_id, turn_id, len(pcm), bool(marker))
                async with send_lock:
                    acquired = time.perf_counter()
                    await websocket.send_json({"type": "audio_chunk", "turn_id": turn_id})
                    if marker:
                        await websocket.send_json({"type": "audio_marker", **marker, "turn_id": turn_id})
                    await websocket.send_bytes(pcm)
                finished = time.perf_counter()
                if diagnostic or (finished - started) >= 0.1:
                    trace(
                        "WEB_AUDIO_SEND_END session=%s output_turn=%s lock_wait_ms=%.1f send_ms=%.1f total_ms=%.1f",
                        session_id, turn_id, (acquired - started) * 1000,
                        (finished - acquired) * 1000, (finished - started) * 1000,
                    )

            async def connect(reason: str | None = None) -> None:
                nonlocal conversation
                if reason:
                    await event({"type": "live:reconnecting", "reason": reason})
                    await persistent_transports.close(session_id)
                    surface_runtime.reset_session_state(session_id)
                conversation = await live_session.open_persistent_conversation(
                    session_id=session_id,
                    transport=persistent_transports.get(session_id),
                    on_event=event,
                    on_audio=audio,
                )
                if reason:
                    await event({"type": "live:reconnected", "session_id": session_id})

            await connect()
            surface_runtime.reset_session_state(session_id)
            await event({"type": "extensions:catalog", "catalog": browser_extension_catalog})
            await event({"type": "live:session_ready", "session_id": session_id})
            await event({"type": "live:state", "state": surface_runtime.session_state(session_id)})
            logger.info("[WEB:PERSISTENT_CONNECTED] session=%s reused=%s", session_id, persistent_transports.get(session_id).connected)

            async def consume_stream() -> None:
                nonlocal microphone_enabled
                while microphone_enabled:
                    try:
                        await conversation.consume_audio_stream()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception("[WEB:PERSISTENT_AUDIO_STREAM_ERROR] session=%s", session_id)
                        await event({"type": "live:error", "message": str(exc)})
                        return

            chunks = total_bytes = 0
            while True:
                packet = await websocket.receive()
                if packet["type"] == "websocket.disconnect":
                    break
                if packet.get("bytes") is not None:
                    if microphone_enabled:
                        chunk = packet["bytes"]
                        chunks += 1
                        total_bytes += len(chunk)
                        await conversation.send_audio(chunk)
                        if chunks == 1:
                            trace("PCM_SENT first_chunk bytes=%s", len(chunk))
                        elif chunks % 25 == 0:
                            trace("PCM_SENT chunks=%s total_bytes=%s", chunks, total_bytes)
                    continue
                raw = packet.get("text")
                if not raw:
                    continue
                command = json.loads(raw)
                kind = str(command.get("type") or "")
                if kind == "live:text":
                    query = str(command.get("query") or "").strip()
                    if not query:
                        await event({"type": "live:error", "message": "Câu hỏi văn bản không được để trống."})
                    elif stream_task and not stream_task.done():
                        # The continuous microphone stream owns Gemini's sole
                        # receive loop. Typed input joins that same session as
                        # a barge-in instead of waiting for turn_complete.
                        await conversation.interrupt_with_text(query)
                    elif text_task and not text_task.done():
                        await event({"type": "live:error", "message": "Lumi đang xử lý lượt trước."})
                    else:
                        text_task = asyncio.create_task(conversation.submit_text(query))
                elif kind == "runtime:diagnostic":
                    diagnostic_result = surface_runtime.report_runtime_diagnostic(
                        session_id=session_id, diagnostic=command
                    )
                    await conversation.report_runtime_diagnostic(diagnostic_result)
                    await event({
                        "type": "runtime:diagnostic_result",
                        "status": diagnostic_result.get("status", "error"),
                        "detail": diagnostic_result.get("detail"),
                    })
                elif kind == "runtime:repair_rendered":
                    try:
                        panel_context = surface_runtime.active_surface_context(session_id)
                        if panel_context is None:
                            raise ValueError("no active surface")
                        if (panel_context["surface_id"] != str(command.get("surface_id") or "")
                                or panel_context["revision"] != command.get("revision")):
                            raise ValueError("surface_id or revision is not active")
                        await conversation.sync_surface_context(panel_context)
                    except (ValueError, GeminiLiveSessionError) as exc:
                        trace("RUNTIME_REPAIR_CONTEXT_SYNC_IGNORED reason=%s", exc)
                        await event({"type": "runtime:diagnostic_result", "status": "ignored", "detail": str(exc)})
                    else:
                        await event({"type": "runtime:diagnostic_result", "status": "context_synced"})
                elif kind == "surface:rendered":
                    template_result = surface_runtime.confirm_rendered(
                        session_id=session_id,
                        surface_id=str(command.get("surface_id") or ""),
                        revision=command.get("revision"),
                    )
                    logger.info(
                        "[TIMING:SURFACE_RENDERED] session=%s surface=%s revision=%s confirmation=%s",
                        session_id,
                        command.get("surface_id") or "-",
                        command.get("revision") or "-",
                        template_result.get("status") if isinstance(template_result, dict) else "-",
                    )
                    trace(
                        "TEMPLATE_RENDER_CONFIRMATION status=%s detail=%s",
                        template_result.get("status"),
                        template_result.get("detail") or template_result.get("template_id"),
                    )
                    if template_result.get("status") == "confirmed":
                        ready_result = await conversation.confirm_surface_rendered(
                            surface_id=str(command.get("surface_id") or ""),
                            revision=command.get("revision"),
                        )
                        trace(
                            "FINAL_SURFACE_RENDER_CONFIRMATION status=%s discarded_progress=%s detail=%s",
                            ready_result.get("status"),
                            ready_result.get("discarded_progress_messages", 0),
                            ready_result.get("detail"),
                        )
                elif kind == "panel:interaction":
                    try:
                        interaction_result = surface_runtime.apply_panel_interaction(
                            session_id=session_id,
                            surface_id=str(command.get("surface_id") or ""),
                            revision=command.get("revision"),
                            anchor_id=str(command.get("anchor_id") or ""),
                            action=str(command.get("action") or ""),
                        )
                    except ValueError as exc:
                        trace("PANEL_INTERACTION_REJECTED reason=%s", exc)
                        await event({"type": "panel:interaction_rejected", "message": str(exc)})
                        continue
                    interaction = interaction_result.interaction
                    if interaction_result.document_update is not None:
                        await event({"type": "openui:document", "document": interaction_result.document_update})
                    await event({
                        "type": "live:debug_trace",
                        "timestamp": _trace_timestamp(),
                        "event": "panel_interaction",
                        "content": json.dumps(interaction, ensure_ascii=False, separators=(",", ":")),
                    })
                    if (stream_task and not stream_task.done()) or (text_task and not text_task.done()):
                        await conversation.interrupt_with_panel_interaction(interaction)
                    else:
                        text_task = asyncio.create_task(conversation.submit_panel_interaction(interaction))
                elif kind in {"live:audio_begin", "live:mic_enabled"}:
                    chunks = total_bytes = 0
                    microphone_enabled = True
                    await conversation.begin_audio()
                    if stream_task is None or stream_task.done():
                        stream_task = asyncio.create_task(consume_stream())
                elif kind in {"live:audio_end", "live:mic_disabled"}:
                    microphone_enabled = False
                    trace("MIC_END chunks=%s total_bytes=%s", chunks, total_bytes)
                    await conversation.end_audio_stream()
                elif kind == "live:close":
                    break
                else:
                    await event({"type": "live:error", "message": f"Sự kiện không hỗ trợ: {kind}"})
    except WebSocketDisconnect:
        logger.info("[WEB:DISCONNECT] session=%s", session_id or "unknown")
    except Exception as exc:
        logger.exception("[WEB:ERROR] session=%s", session_id or "unknown")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "live:error", "message": str(exc)})
    finally:
        for task in (text_task, stream_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        _schedule_cleanup(session_id)


@contextlib.asynccontextmanager
async def lifespan(_app):
    try:
        await task_manager_agent.warmup()
        yield
    finally:
        await task_manager_agent.close()


app = Starlette(lifespan=lifespan, routes=[
    Route("/", home),
    Route("/assets/resources/{asset_id}", shared_asset),
    Route("/knowledge/media/{object_key}", knowledge_media),
    Route("/api/health", health),
    Route("/api/client-debug", client_debug, methods=["POST"]),
    *knowledge_gateway_routes(knowledge_gateway),
    WebSocketRoute("/ws/live", live_socket),
])
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002)
