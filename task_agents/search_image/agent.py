"""Independent Gemini agent for ``search_image_task``.

This module owns only media retrieval.  It neither imports nor wraps the
information retrieval executor, and it has its own prompt, client and result
contract so its model can be changed without changing information retrieval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

try:  # Keep source/contract imports usable when the optional SDK is absent.
    from google import genai
    from google.genai import types
except ModuleNotFoundError:  # pragma: no cover - dependency boundary.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from lumi_openui.agent_contracts import AgentTaskRequest
from lumi_openui.knowledge.access import KnowledgeAccessContext, KnowledgeAccessError
from lumi_openui.knowledge.gateway import KnowledgeGateway
from lumi_openui.knowledge.registry import KnowledgeRegistryError
from lumi_openui.knowledge.tools import (
    SEARCH_LOCAL_MEDIA,
    KnowledgeToolArgumentsError,
    knowledge_tool_definition,
)
from lumi_openui.settings import Settings

from .prompts import SEARCH_IMAGE_SYSTEM_PROMPT


LOCAL_MEDIA_SEARCH_TOOL = knowledge_tool_definition(SEARCH_LOCAL_MEDIA).to_dict()

# The final model turn may select either a local MediaRecord or a direct image
# URL from Google grounding.  The runtime later resolves either form to a
# trusted internal MediaReference.
MEDIA_RESULT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["media_id"],
                        "properties": {
                            "media_id": {
                                "type": "string",
                                "description": "media_id của ảnh local đã xuất hiện trong evidence.",
                            }
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["url"],
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "URL ảnh trực tiếp đã xuất hiện trong Google image grounding evidence.",
                            }
                        },
                    },
                ]
            },
        }
    },
}

MEDIA_MIN_SIMILARITY_SCORE = 0.65
MAX_LOCAL_MEDIA_SEARCHES = 1
MAX_RESULT_VALIDATION_RETRIES = 1
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20.0
REMOTE_MEDIA_PACKAGE_ID = "google_image_grounding"
REMOTE_IMAGE_MIME_BY_SUFFIX = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
ClientFactory = Callable[..., Any]


from lumi_openui.trace import log_token_usage


logger = logging.getLogger("lumi.media_retrieval")


class MediaRetrievalError(RuntimeError):
    """The independent media-retrieval task could not complete."""


class MediaResultValidationError(MediaRetrievalError):
    """The model result does not meet the public media-selection contract."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class MediaRetrievalResult:
    """Trusted media selections and their runtime-only resolution metadata."""

    items: tuple[dict[str, str], ...]
    references: tuple[dict[str, Any], ...]
    local_search_count: int
    google_grounding_used: bool
    duration_ms: int


def validate_media_result(
    raw_text: object,
    *,
    local_evidence: Sequence[Mapping[str, Any]],
    grounded_media_urls: Sequence[str],
) -> list[str]:
    """Validate JSON identity only; relevance remains the agent's judgment."""

    try:
        value = json.loads(raw_text)  # type: ignore[arg-type]
    except (TypeError, json.JSONDecodeError):
        return ["JSON không hợp lệ."]
    if not isinstance(value, Mapping):
        return ["JSON gốc phải là object."]
    if set(value) != {"items"}:
        return ["JSON chỉ được có field items."]
    items = value.get("items")
    if not isinstance(items, list):
        return ["items phải là array."]

    local_media_ids = {
        media_id.strip()
        for candidate in local_evidence
        if isinstance(candidate, Mapping)
        and isinstance(candidate.get("payload"), Mapping)
        and isinstance((media_id := candidate["payload"].get("media_id")), str)
        and media_id.strip()
    }
    verified_urls = {
        url.strip() for url in grounded_media_urls if isinstance(url, str) and url.strip()
    }
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"items[{index}] phải là object.")
            continue
        if set(item) == {"media_id"}:
            media_id = item.get("media_id")
            if not isinstance(media_id, str) or not media_id.strip():
                errors.append(f"items[{index}].media_id phải là chuỗi không rỗng.")
            elif media_id.strip() not in local_media_ids:
                errors.append(f"items[{index}].media_id không tồn tại trong local evidence.")
            continue
        if set(item) == {"url"}:
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                errors.append(f"items[{index}].url phải là chuỗi không rỗng.")
            elif url.strip() not in verified_urls:
                errors.append(f"items[{index}].url không tồn tại trong Google image grounding evidence.")
            continue
        errors.append(f"items[{index}] chỉ được có media_id hoặc url.")
    return errors


class MediaRetrievalAgent:
    """Gemini sub-agent dedicated only to ``search_image_task``."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        max_local_searches: int = MAX_LOCAL_MEDIA_SEARCHES,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not settings.gemini_media_key:
            raise MediaRetrievalError("GEMINI_MEDIA_KEY is not configured.")
        if not settings.gemini_media_model:
            raise MediaRetrievalError("GEMINI_MEDIA_MODEL is not configured.")
        if max_local_searches != 1:
            raise MediaRetrievalError("Media retrieval uses exactly one local search round.")
        if request_timeout_seconds <= 0:
            raise MediaRetrievalError("request_timeout_seconds must be positive.")
        if types is None:
            raise MediaRetrievalError("google-genai is required for search_image_task.")
        if client_factory is None:
            if genai is None:
                raise MediaRetrievalError("google-genai is required for search_image_task.")
            client_factory = genai.Client
        self._settings = settings
        self._max_local_searches = max_local_searches
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._client = client_factory(
            api_key=settings.gemini_media_key,
            http_options=types.HttpOptions(timeout=round(request_timeout_seconds * 1000)),
        )

    @property
    def model_name(self) -> str:
        return self._settings.gemini_media_model

    async def retrieve(
        self,
        request: AgentTaskRequest,
        gateway: KnowledgeGateway,
        access_context: KnowledgeAccessContext,
    ) -> MediaRetrievalResult:
        if not isinstance(request, AgentTaskRequest):
            raise TypeError("request must be an AgentTaskRequest.")
        if not isinstance(gateway, KnowledgeGateway):
            raise TypeError("gateway must be a KnowledgeGateway.")
        task = request.task
        if task.task_type != "search_image_task":
            raise MediaRetrievalError("MediaRetrievalAgent requires search_image_task.")
        request_payload = request.to_dict()
        knowledge_catalog = request.framework_context.knowledge_catalog
        logger.info(
            "[MEDIA:START] run=%s task=%s evidence=%s model=%s instruction=%r packages=%s",
            task.run_id,
            task.task_id,
            task.evidence_ref,
            self.model_name,
            task.instruction,
            [entry.get("package_id") for entry in knowledge_catalog],
        )
        messages: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=json.dumps(request_payload, ensure_ascii=False))])
        ]
        config = types.GenerateContentConfig(
            system_instruction=self._system_instruction(),
            response_mime_type="application/json",
            response_json_schema=MEDIA_RESULT_JSON_SCHEMA,
            tools=[
                types.Tool(functionDeclarations=[
                    types.FunctionDeclaration(
                        name=LOCAL_MEDIA_SEARCH_TOOL["name"],
                        description=LOCAL_MEDIA_SEARCH_TOOL["description"],
                        parametersJsonSchema=LOCAL_MEDIA_SEARCH_TOOL["input_schema"],
                    )
                ]),
                # Google is always available. Local-first remains a prompt rule.
                types.Tool(google_search=types.GoogleSearch()),
            ],
            tool_config=types.ToolConfig(include_server_side_tool_invocations=True),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        )
        started = time.perf_counter()
        local_search_count = 0
        local_evidence: list[dict[str, Any]] = []
        remote_references_by_url: dict[str, dict[str, Any]] = {}
        grounding_used = False
        validation_retries = 0
        model_calls = 0

        while True:
            response = await self._generate(messages, config)
            model_calls += 1
            log_token_usage(
                agent="search_image", model=self._settings.gemini_media_model, api="gemini",
                usage=getattr(response, "usage_metadata", None),
                run=task.run_id, task=task.task_id, call=model_calls,
            )
            remote_records = self._remote_media_records(response, remote_references_by_url)
            if remote_records:
                grounding_used = True

            calls = tuple(getattr(response, "function_calls", None) or ())
            logger.info(
                "[MEDIA:MODEL_RESPONSE] run=%s task=%s call=%s local_tool_calls=%s new_grounded_images=%s total_grounded_images=%s",
                task.run_id,
                task.task_id,
                model_calls,
                [getattr(call, "name", None) for call in calls],
                len(remote_records),
                len(remote_references_by_url),
            )
            if remote_records:
                logger.info(
                    "[MEDIA:GROUNDING_RESULT] run=%s task=%s new_images=%s",
                    task.run_id,
                    task.task_id,
                    [
                        {
                            "url": record.get("source_uri"),
                            "caption": record.get("caption"),
                            "caption": record.get("caption"),
                        }
                        for record in remote_records
                    ],
                )
            if not calls:
                raw_text = getattr(response, "text", None)
                if local_search_count == 0:
                    logger.info(
                        "[MEDIA:NO_LOCAL_SEARCH] run=%s task=%s packages=%s grounded_image_urls=%s",
                        task.run_id,
                        task.task_id,
                        [entry.get("package_id") for entry in knowledge_catalog],
                        list(remote_references_by_url),
                    )
                errors = validate_media_result(
                    raw_text,
                    local_evidence=local_evidence,
                    grounded_media_urls=tuple(remote_references_by_url),
                )
                if errors:
                    logger.warning(
                        "[MEDIA:INVALID_OUTPUT] run=%s task=%s retry=%s local_searches=%s local_media=%s grounded_image_urls=%s errors=%s raw=%r",
                        task.run_id,
                        task.task_id,
                        validation_retries,
                        local_search_count,
                        [
                            candidate.get("payload", {}).get("media_id")
                            for candidate in local_evidence
                            if isinstance(candidate, Mapping)
                        ],
                        list(remote_references_by_url),
                        errors,
                        raw_text,
                    )
                    if validation_retries >= MAX_RESULT_VALIDATION_RETRIES:
                        raise MediaResultValidationError(errors)
                    validation_retries += 1
                    messages.append(self._model_content(response, ()))
                    messages.append(types.Content(
                        role="user",
                        parts=[types.Part(text=self._validation_feedback(errors))],
                    ))
                    continue
                parsed = json.loads(raw_text)
                items, references = self._selected_media(
                    parsed["items"],
                    local_evidence=local_evidence,
                    remote_references_by_url=remote_references_by_url,
                )
                result = MediaRetrievalResult(
                    items=tuple(items),
                    references=tuple(references),
                    local_search_count=local_search_count,
                    google_grounding_used=grounding_used,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )
                logger.info(
                    "[MEDIA:READY] run=%s task=%s items=%s references=%s local_searches=%s google_grounding=%s duration_ms=%s",
                    task.run_id,
                    task.task_id,
                    [dict(item) for item in result.items],
                    [
                        {
                            "media_id": reference.get("media_id"),
                            "package_id": reference.get("package_id"),
                            "source_uri": reference.get("source_uri"),
                        }
                        for reference in result.references
                    ],
                    result.local_search_count,
                    result.google_grounding_used,
                    result.duration_ms,
                )
                return result

            messages.append(self._model_content(response, calls))
            function_responses: list[types.Part] = []
            for call in calls:
                if local_search_count >= self._max_local_searches:
                    result: dict[str, Any] = {
                        "error": {
                            "code": "tool_limit_reached",
                            "message": "Task chỉ có một lượt local. Không gọi lại hoặc đổi query. Đánh giá ảnh đã nhận có đáp ứng đúng request không; nếu không, dùng Google image grounding ngay.",
                        }
                    }
                else:
                    local_search_count += 1
                    try:
                        search_request = self._search_request(call)
                        logger.info(
                            "[MEDIA:LOCAL_SEARCH] run=%s task=%s attempt=%s query=%r sources=%s",
                            task.run_id,
                            task.task_id,
                            local_search_count,
                            search_request["query"],
                            search_request["package_ids"],
                        )
                        raw_result = await gateway.search_media(
                            search_request,
                            access_context,
                        )
                        result = self._filter_local_candidates(raw_result)
                        self._collect_local_evidence(result, local_evidence)
                        logger.info(
                            "[MEDIA:LOCAL_RESULT] run=%s task=%s raw_candidates=%s accepted=%s evidence_total=%s package_errors=%s",
                            task.run_id,
                            task.task_id,
                            len(raw_result.get("candidates", [])) if isinstance(raw_result.get("candidates"), list) else 0,
                            [
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "similarity_score": candidate.get("similarity_score"),
                                    "media_id": candidate.get("payload", {}).get("media_id"),
                                    "caption": candidate.get("payload", {}).get("caption"),
                                }
                                for candidate in result.get("candidates", [])
                                if isinstance(candidate, Mapping)
                            ],
                            len(local_evidence),
                            result.get("package_errors", []),
                        )
                    except (
                        KnowledgeAccessError,
                        KnowledgeRegistryError,
                        KnowledgeToolArgumentsError,
                        MediaRetrievalError,
                    ) as exc:
                        result = {"candidates": [], "package_errors": [{"message": str(exc)}]}
                        logger.exception(
                            "[MEDIA:LOCAL_SEARCH_FAILED] run=%s task=%s",
                            task.run_id,
                            task.task_id,
                        )
                function_responses.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=getattr(call, "name", SEARCH_LOCAL_MEDIA),
                        id=getattr(call, "id", None),
                        response=result,
                    )
                ))
            messages.append(types.Content(role="user", parts=function_responses))

    async def _generate(self, messages: list[types.Content], config: types.GenerateContentConfig) -> Any:
        try:
            return await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._settings.gemini_media_model,
                    contents=messages,
                    config=config,
                ),
                timeout=self._request_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise MediaRetrievalError("Gemini media retrieval timed out.") from exc
        except Exception as exc:
            raise MediaRetrievalError(
                f"Gemini media retrieval failed: {str(exc)[:300] or type(exc).__name__}."
            ) from exc

    def _system_instruction(self) -> str:
        return SEARCH_IMAGE_SYSTEM_PROMPT

    @staticmethod
    def _search_request(call: Any) -> dict[str, Any]:
        if getattr(call, "name", None) != SEARCH_LOCAL_MEDIA:
            raise MediaRetrievalError("Media agent called an unsupported local tool.")
        arguments = getattr(call, "args", None)
        if not isinstance(arguments, Mapping) or set(arguments) != {"query", "package_ids"}:
            raise MediaRetrievalError("search_local_media requires exactly query and package_ids.")
        query = arguments.get("query")
        package_ids = arguments.get("package_ids")
        if not isinstance(query, str) or not query.strip():
            raise MediaRetrievalError("search_local_media.query must be a non-empty string.")
        if (
            not isinstance(package_ids, list)
            or not package_ids
            or not all(isinstance(item, str) and item.strip() for item in package_ids)
        ):
            raise MediaRetrievalError("search_local_media.package_ids must be a non-empty array of strings.")
        return {"query": query.strip(), "package_ids": [item.strip() for item in package_ids]}

    @staticmethod
    def _filter_local_candidates(result: Mapping[str, Any]) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        raw_candidates = result.get("candidates")
        if isinstance(raw_candidates, list):
            for candidate in raw_candidates:
                if not isinstance(candidate, Mapping):
                    continue
                score = candidate.get("similarity_score")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or float(score) < MEDIA_MIN_SIMILARITY_SCORE
                    or (
                        isinstance(candidate.get("payload"), Mapping)
                        and candidate["payload"].get("local_file_missing") is True
                    )
                    or MediaRetrievalAgent._local_reference(candidate) is None
                ):
                    continue
                accepted.append(dict(candidate))
        return {"candidates": accepted, "package_errors": result.get("package_errors", [])}

    @staticmethod
    def _collect_local_evidence(result: Mapping[str, Any], evidence: list[dict[str, Any]]) -> None:
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            return
        known_ids = {
            item.get("payload", {}).get("media_id")
            for item in evidence
            if isinstance(item.get("payload"), Mapping)
        }
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or MediaRetrievalAgent._local_reference(candidate) is None:
                continue
            media_id = candidate["payload"]["media_id"]
            if media_id not in known_ids:
                evidence.append(dict(candidate))
                known_ids.add(media_id)

    @staticmethod
    def _local_reference(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
        payload = candidate.get("payload")
        if not isinstance(payload, Mapping):
            return None
        media_id = payload.get("media_id")
        package_id = candidate.get("package_id")
        mime_type = payload.get("mime_type")
        source_uri = payload.get("source_uri")
        if not all(isinstance(value, str) and value.strip() for value in (media_id, package_id, mime_type, source_uri)):
            return None
        caption = next(
            (
                value.strip()
                for key in ("caption", "alt_text", "title")
                if isinstance((value := payload.get(key)), str) and value.strip()
            ),
            "",
        )
        object_key = payload.get("object_key")
        return {
            "media_id": media_id.strip(),
            "package_id": package_id.strip(),
            "object_key": object_key.strip() if isinstance(object_key, str) and object_key.strip() else None,
            "mime_type": mime_type.strip(),
            "source_uri": source_uri.strip(),
            "caption": caption,
            "local_file_missing": payload.get("local_file_missing") is True,
        }

    @staticmethod
    def _remote_media_records(
        response: Any,
        references_by_url: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        candidates = getattr(response, "candidates", None) or ()
        candidate = candidates[0] if candidates else None
        metadata = getattr(candidate, "grounding_metadata", None) if candidate is not None else None
        if metadata is None:
            metadata = getattr(response, "grounding_metadata", None)
        for chunk in getattr(metadata, "grounding_chunks", None) or ():
            web = getattr(chunk, "web", None)
            url = getattr(web, "uri", None) if web is not None else None
            if not isinstance(url, str) or not url.strip():
                continue
            clean_url = url.strip()
            parsed = urlparse(clean_url)
            suffix = "." + parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path.rsplit("/", 1)[-1] else ""
            mime_type = REMOTE_IMAGE_MIME_BY_SUFFIX.get(suffix)
            if parsed.scheme != "https" or not parsed.netloc or mime_type is None:
                continue
            title = getattr(web, "title", None)
            reference = references_by_url.setdefault(clean_url, {
                "media_id": f"remote_{uuid4().hex}",
                "package_id": REMOTE_MEDIA_PACKAGE_ID,
                "object_key": None,
                "mime_type": mime_type,
                "source_uri": clean_url,
                "caption": title.strip() if isinstance(title, str) and title.strip() else "",
                "local_file_missing": False,
            })
            records.append(reference)
        return records

    @staticmethod
    def _selected_media(
        raw_items: Sequence[Mapping[str, Any]],
        *,
        local_evidence: Sequence[Mapping[str, Any]],
        remote_references_by_url: Mapping[str, Mapping[str, Any]],
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        local_references = {
            reference["media_id"]: reference
            for candidate in local_evidence
            if (reference := MediaRetrievalAgent._local_reference(candidate)) is not None
        }
        items: list[dict[str, str]] = []
        references: list[dict[str, Any]] = []
        for item in raw_items:
            if "media_id" in item:
                media_id = item["media_id"].strip()
                reference = local_references[media_id]
            else:
                reference = dict(remote_references_by_url[item["url"].strip()])
                media_id = reference["media_id"]
            items.append({"media_id": media_id})
            references.append(dict(reference))
        return items, references

    @staticmethod
    def _model_content(response: Any, calls: Sequence[Any]) -> types.Content:
        candidates = getattr(response, "candidates", None) or ()
        if candidates and getattr(candidates[0], "content", None) is not None:
            return candidates[0].content
        parts = [types.Part(function_call=call) for call in calls]
        return types.Content(role="model", parts=parts or [types.Part(text="")])

    @staticmethod
    def _validation_feedback(errors: Sequence[str]) -> str:
        return (
            "Kết quả JSON vừa rồi chưa hợp lệ. Hãy sinh lại JSON mới.\n\nLỗi:\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\n\nChỉ trả JSON dạng {\"items\":[...]}; local chỉ dùng media_id có trong evidence; "
            "Google chỉ dùng URL ảnh có trong grounding evidence; không thêm field khác."
        )
