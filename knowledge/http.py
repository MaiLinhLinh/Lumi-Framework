"""HTTP boundary exposing scoped Knowledge Gateway operations to Agent Services."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from starlette.responses import JSONResponse
from starlette.routing import Route

from final_framework.agent_tasks import AgentToolRegistryError

from .access import KNOWLEDGE_TOKEN_HEADER, KnowledgeAccessError
from .gateway import KnowledgeGateway
from .registry import KnowledgeRegistryError
from .tools import (
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    KnowledgeToolArgumentsError,
    knowledge_tool_definition,
)


logger = logging.getLogger("lumi.knowledge_gateway_http")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
    )


def _authenticate(request: Any, gateway: KnowledgeGateway):
    token = request.headers.get(KNOWLEDGE_TOKEN_HEADER, "").strip()
    if not token:
        raise KnowledgeAccessError("missing knowledge access token.")
    return gateway.grant_registry.authenticate(token)


async def _request_body(request: Any) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise KnowledgeToolArgumentsError(
            "request body must contain valid JSON."
        ) from exc
    if not isinstance(payload, Mapping):
        raise KnowledgeToolArgumentsError("request body must be a JSON object.")
    return dict(payload)


async def _search(request: Any, gateway: KnowledgeGateway, tool_name: str) -> JSONResponse:
    try:
        access = _authenticate(request, gateway)
    except KnowledgeAccessError:
        return _error(401, "invalid_knowledge_token", "Knowledge access denied.")
    try:
        arguments = await _request_body(request)
        if tool_name == SEARCH_LOCAL_TEXT:
            result = await gateway.search_text(arguments, access)
        else:
            result = await gateway.search_media(arguments, access)
        return JSONResponse(result)
    except KnowledgeToolArgumentsError:
        return _error(400, "invalid_tool_arguments", "Invalid knowledge search request.")
    except KnowledgeAccessError:
        return _error(403, "knowledge_access_forbidden", "Knowledge access forbidden.")
    except KnowledgeRegistryError:
        return _error(400, "invalid_knowledge_request", "Invalid knowledge search request.")
    except Exception as exc:
        logger.error(
            "[KNOWLEDGE_GATEWAY_HTTP:ERROR] operation=%s error_type=%s",
            tool_name,
            type(exc).__name__,
        )
        return _error(500, "knowledge_gateway_error", "Knowledge Gateway failed.")


def knowledge_gateway_routes(gateway: KnowledgeGateway) -> tuple[Route, ...]:
    """Build the three deployment routes around one shared Gateway instance."""

    if not isinstance(gateway, KnowledgeGateway):
        raise TypeError("gateway must be a KnowledgeGateway.")

    async def search_text(request: Any) -> JSONResponse:
        return await _search(request, gateway, SEARCH_LOCAL_TEXT)

    async def search_media(request: Any) -> JSONResponse:
        return await _search(request, gateway, SEARCH_LOCAL_MEDIA)

    async def describe_tool(request: Any) -> JSONResponse:
        try:
            access = _authenticate(request, gateway)
        except KnowledgeAccessError:
            return _error(401, "invalid_knowledge_token", "Knowledge access denied.")
        tool_name = str(request.path_params.get("tool_name") or "")
        try:
            definition = knowledge_tool_definition(tool_name)
        except AgentToolRegistryError:
            return _error(404, "knowledge_tool_not_found", "Knowledge tool not found.")
        try:
            gateway.grant_registry.authorize(
                access,
                tool_name=tool_name,
                package_ids=(),
            )
        except KnowledgeAccessError:
            return _error(403, "knowledge_access_forbidden", "Knowledge access forbidden.")
        return JSONResponse(definition.to_dict())

    return (
        Route("/api/knowledge/search-text", search_text, methods=["POST"]),
        Route("/api/knowledge/search-media", search_media, methods=["POST"]),
        Route("/api/agent-tools/{tool_name}", describe_tool, methods=["GET"]),
    )


__all__ = ["knowledge_gateway_routes"]
