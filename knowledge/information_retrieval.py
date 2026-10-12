"""Independent Gemini agent for ``search_information_task``.

This module owns the text-information retrieval prompt, Gemini client and
result contract.  It intentionally does not import the legacy Plan Agent
workers or their shared worker runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

try:  # Keep pure contract/registry imports usable without the optional SDK.
    from google import genai
    from google.genai import types
except ModuleNotFoundError:  # pragma: no cover - exercised by dependency checks.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from final_framework.agent_tasks import AgentTaskRequest
from final_framework.knowledge.access import KnowledgeAccessContext, KnowledgeAccessError
from final_framework.knowledge.gateway import KnowledgeGateway
from final_framework.knowledge.registry import KnowledgeRegistryError
from final_framework.knowledge.tools import (
    SEARCH_LOCAL_TEXT,
    KnowledgeToolArgumentsError,
    knowledge_tool_definition,
)
from final_framework.settings import Settings


logger = logging.getLogger("lumi.information_retrieval")

LOCAL_TEXT_SEARCH_TOOL = knowledge_tool_definition(SEARCH_LOCAL_TEXT).to_dict()

# A local item carries the candidate ID returned by the local tool. A Google
# grounded item carries only the model-written passage; Runtime does not ask the
# model to reproduce a Google URI or verify passage-to-source attribution.
INFORMATION_RESULT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["payload"],
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "description": "Chỉ có với item chọn từ candidate local đã xuất hiện trong evidence.",
                    },
                    "payload": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["passage"],
                        "properties": {
                            "passage": {
                                "type": "string",
                                "description": "Passage trích lọc từ local candidate hoặc do Gemini viết từ Google grounding.",
                            }
                        },
                    },
                },
            },
        }
    },
}

TEXT_MIN_SIMILARITY_SCORE = 0.65
MAX_LOCAL_SEARCHES = 3
MAX_RESULT_VALIDATION_RETRIES = 1
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20.0
ClientFactory = Callable[..., Any]


class InformationRetrievalError(RuntimeError):
    """The independent information-retrieval task could not complete."""


class InformationResultValidationError(InformationRetrievalError):
    """The model result does not meet the public information contract."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class InformationRetrievalResult:
    """Worker-selected evidence ready for the executor to store."""

    items: tuple[dict[str, Any], ...]
    local_search_count: int
    google_grounding_used: bool
    duration_ms: int


def validate_information_result(
    raw_text: object,
    *,
    local_evidence: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Validate identity and JSON shape, never factual adequacy.

    The validator deliberately does not judge whether passages fully answer
    the instruction.  That is the retrieval agent's own evaluation step.
    """

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

    local_ids = {
        candidate_id.strip()
        for item in local_evidence
        if isinstance(item, Mapping)
        and isinstance((candidate_id := item.get("candidate_id")), str)
        and candidate_id.strip()
    }
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"items[{index}] phải là object.")
            continue
        if set(item) - {"candidate_id", "payload"}:
            errors.append(f"items[{index}] chỉ được có candidate_id và payload.")
        candidate_id = item.get("candidate_id")
        if candidate_id is not None:
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                errors.append(f"items[{index}].candidate_id phải là chuỗi không rỗng.")
            elif candidate_id.strip() not in local_ids:
                errors.append(f"items[{index}].candidate_id không tồn tại trong local evidence.")
        payload = item.get("payload")
        passage = payload.get("passage") if isinstance(payload, Mapping) else None
        if not isinstance(passage, str) or not passage.strip():
            errors.append(f"items[{index}].payload.passage đang rỗng.")
    return errors


class InformationRetrievalAgent:
    """Gemini sub-agent dedicated only to ``search_information_task``."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        max_local_searches: int = MAX_LOCAL_SEARCHES,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not settings.gemini_information_api_key:
            raise InformationRetrievalError("GEMINI_INFORMATION_API_KEY is not configured.")
        if not settings.gemini_information_model:
            raise InformationRetrievalError("GEMINI_INFORMATION_MODEL is not configured.")
        if max_local_searches < 1:
            raise InformationRetrievalError("max_local_searches must be positive.")
        if request_timeout_seconds <= 0:
            raise InformationRetrievalError("request_timeout_seconds must be positive.")
        if types is None:
            raise InformationRetrievalError(
                "google-genai is required for search_information_task."
            )
        if client_factory is None:
            if genai is None:  # Defensive: kept separate for static type clarity.
                raise InformationRetrievalError(
                    "google-genai is required for search_information_task."
                )
            client_factory = genai.Client
        self._settings = settings
        self._max_local_searches = max_local_searches
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._client = client_factory(
            api_key=settings.gemini_information_api_key,
            http_options=types.HttpOptions(timeout=round(request_timeout_seconds * 1000)),
        )

    @property
    def model_name(self) -> str:
        return self._settings.gemini_information_model

    async def retrieve(
        self,
        request: AgentTaskRequest,
        gateway: KnowledgeGateway,
        access_context: KnowledgeAccessContext,
    ) -> InformationRetrievalResult:
        if not isinstance(request, AgentTaskRequest):
            raise TypeError("request must be an AgentTaskRequest.")
        if not isinstance(gateway, KnowledgeGateway):
            raise TypeError("gateway must be a KnowledgeGateway.")
        task = request.task
        if task.task_type != "search_information_task":
            raise InformationRetrievalError("InformationRetrievalAgent requires search_information_task.")
        request_payload = request.to_dict()
        knowledge_catalog = request.framework_context.knowledge_catalog
        logger.info(
            "[INFORMATION:START] run=%s task=%s evidence=%s model=%s instruction=%r packages=%s",
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
            response_json_schema=INFORMATION_RESULT_JSON_SCHEMA,
            tools=[
                types.Tool(functionDeclarations=[
                    types.FunctionDeclaration(
                        name=LOCAL_TEXT_SEARCH_TOOL["name"],
                        description=LOCAL_TEXT_SEARCH_TOOL["description"],
                        parametersJsonSchema=LOCAL_TEXT_SEARCH_TOOL["input_schema"],
                    )
                ]),
                # Google is always available.  Local-first is a prompt rule,
                # not a Runtime phase gate.
                types.Tool(google_search=types.GoogleSearch()),
            ],
            tool_config=types.ToolConfig(include_server_side_tool_invocations=True),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        )
        started = time.perf_counter()
        local_search_count = 0
        local_evidence: list[dict[str, Any]] = []
        grounding_records: list[dict[str, Any]] = []
        validation_retries = 0
        model_calls = 0

        while True:
            response = await self._generate(messages, config)
            model_calls += 1
            new_grounding = self._grounding_records(response)
            for record in new_grounding:
                if record not in grounding_records:
                    grounding_records.append(record)

            calls = tuple(getattr(response, "function_calls", None) or ())
            logger.info(
                "[INFORMATION:MODEL_RESPONSE] run=%s task=%s call=%s local_tool_calls=%s new_grounding=%s total_grounding=%s",
                task.run_id,
                task.task_id,
                model_calls,
                [getattr(call, "name", None) for call in calls],
                len(new_grounding),
                len(grounding_records),
            )
            if new_grounding:
                logger.info(
                    "[INFORMATION:GROUNDING_RESULT] run=%s task=%s new_sources=%s",
                    task.run_id,
                    task.task_id,
                    [
                        {
                            "source_uri": record.get("source_uri"),
                            "title": record.get("title"),
                        }
                        for record in new_grounding
                    ],
                )
            if not calls:
                raw_text = getattr(response, "text", None)
                if local_search_count == 0:
                    logger.warning(
                        "[INFORMATION:LOCAL_NOT_CALLED] run=%s task=%s packages=%s grounded_sources=%s",
                        task.run_id,
                        task.task_id,
                        [entry.get("package_id") for entry in knowledge_catalog],
                        [record.get("source_uri") for record in grounding_records],
                    )
                errors = validate_information_result(
                    raw_text,
                    local_evidence=local_evidence,
                )
                if errors:
                    logger.warning(
                        "[INFORMATION:INVALID_OUTPUT] run=%s task=%s retry=%s local_searches=%s local_candidates=%s grounded_sources=%s errors=%s raw=%r",
                        task.run_id,
                        task.task_id,
                        validation_retries,
                        local_search_count,
                        [candidate.get("candidate_id") for candidate in local_evidence],
                        [record.get("source_uri") for record in grounding_records],
                        errors,
                        raw_text,
                    )
                    if validation_retries >= MAX_RESULT_VALIDATION_RETRIES:
                        raise InformationResultValidationError(errors)
                    validation_retries += 1
                    messages.append(self._model_content(response, ()))
                    messages.append(types.Content(
                        role="user",
                        parts=[types.Part(text=self._validation_feedback(errors))],
                    ))
                    continue
                parsed = json.loads(raw_text)
                items = tuple(self._normalize_item(item) for item in parsed["items"])
                result = InformationRetrievalResult(
                    items=items,
                    local_search_count=local_search_count,
                    google_grounding_used=bool(grounding_records),
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )
                logger.info(
                    "[INFORMATION:READY] run=%s task=%s items=%s local_searches=%s google_grounding=%s duration_ms=%s",
                    task.run_id,
                    task.task_id,
                    [dict(item) for item in result.items],
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
                            "message": "Đã dùng hết lượt local retrieval của task; hãy đánh giá evidence hiện có hoặc dùng Google grounding nếu cần.",
                        }
                    }
                else:
                    try:
                        search_request = self._search_request(call)
                        local_search_count += 1
                        logger.info(
                            "[INFORMATION:LOCAL_SEARCH] run=%s task=%s attempt=%s query=%r sources=%s",
                            task.run_id,
                            task.task_id,
                            local_search_count,
                            search_request["query"],
                            search_request["package_ids"],
                        )
                        raw_result = await gateway.search_text(
                            search_request,
                            access_context,
                        )
                        result = self._filter_local_candidates(raw_result)
                        self._collect_local_evidence(result, local_evidence)
                        logger.info(
                            "[INFORMATION:LOCAL_RESULT] run=%s task=%s raw_candidates=%s accepted=%s evidence_total=%s package_errors=%s",
                            task.run_id,
                            task.task_id,
                            len(raw_result.get("candidates", [])) if isinstance(raw_result.get("candidates"), list) else 0,
                            [
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "similarity_score": candidate.get("similarity_score"),
                                    "passage_preview": str(
                                        payload.get("passage", "")
                                        if isinstance((payload := candidate.get("payload")), Mapping)
                                        else ""
                                    )[:180],
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
                        InformationRetrievalError,
                    ) as exc:
                        local_search_count += 1
                        result = {"candidates": [], "package_errors": [{"message": str(exc)}]}
                        logger.exception(
                            "[INFORMATION:LOCAL_SEARCH_FAILED] run=%s task=%s",
                            task.run_id,
                            task.task_id,
                        )
                function_responses.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=getattr(call, "name", SEARCH_LOCAL_TEXT),
                        id=getattr(call, "id", None),
                        response=result,
                    )
                ))
            messages.append(types.Content(role="user", parts=function_responses))

    async def _generate(self, messages: list[types.Content], config: types.GenerateContentConfig) -> Any:
        try:
            return await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._settings.gemini_information_model,
                    contents=messages,
                    config=config,
                ),
                timeout=self._request_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise InformationRetrievalError("Gemini information retrieval timed out.") from exc
        except Exception as exc:
            raise InformationRetrievalError(
                f"Gemini information retrieval failed: {str(exc)[:300] or type(exc).__name__}."
            ) from exc

    def _system_instruction(self) -> str:
        return """Bạn là Search Information Agent độc lập của Lumi.

Bạn chỉ thực hiện một search_information_task. Đọc instruction và Knowledge Catalog được cung cấp.

Quy trình bắt buộc:
1. Nếu Knowledge Catalog có ít nhất một kho text phù hợp, BẮT BUỘC lượt truy xuất đầu tiên phải chọn kho phù hợp và gọi search_local_text. Có thể đưa nhiều package_ids vào cùng một lần gọi; Runtime sẽ truy xuất chúng song song. Không dùng Google Search/grounding trước khi đã nhận FunctionResponse của lần local search này.
2. Đọc và tự đánh giá candidate local theo instruction. Chỉ sau bước đó, nếu local không đủ hoặc không liên quan, Google Search/grounding đã luôn sẵn để bạn tự dùng.
3. Với item từ local, bắt buộc dùng candidate_id đã có trong evidence local. Với item từ Google grounding, chỉ trả payload.passage, không trả candidate_id, source_uri, URL hay provenance.
4. payload.passage được phép trích lọc hoặc viết lại ngắn gọn để đúng instruction, nhưng tuyệt đối không bịa dữ kiện. Bạn tự chịu trách nhiệm đánh giá kết quả Google grounding trước khi trả passage.

Kết quả cuối phải tuân thủ JSON Schema đã cấu hình. Có thể trộn item local và Google trong cùng items. Không đưa warnings, status, provenance, score, package_id hoặc field nào ngoài schema vào JSON cuối."""

    @staticmethod
    def _search_request(call: Any) -> dict[str, Any]:
        if getattr(call, "name", None) != SEARCH_LOCAL_TEXT:
            raise InformationRetrievalError("Information agent called an unsupported local tool.")
        arguments = getattr(call, "args", None)
        if not isinstance(arguments, Mapping) or set(arguments) != {"query", "package_ids"}:
            raise InformationRetrievalError("search_local_text requires exactly query and package_ids.")
        query = arguments.get("query")
        package_ids = arguments.get("package_ids")
        if not isinstance(query, str) or not query.strip():
            raise InformationRetrievalError("search_local_text.query must be a non-empty string.")
        if (
            not isinstance(package_ids, list)
            or not package_ids
            or not all(isinstance(item, str) and item.strip() for item in package_ids)
        ):
            raise InformationRetrievalError("search_local_text.package_ids must be a non-empty array of strings.")
        return {"query": query.strip(), "package_ids": [item.strip() for item in package_ids]}

    @staticmethod
    def _filter_local_candidates(result: Mapping[str, Any]) -> dict[str, Any]:
        raw_candidates = result.get("candidates")
        accepted: list[dict[str, Any]] = []
        if isinstance(raw_candidates, list):
            for candidate in raw_candidates:
                if not isinstance(candidate, Mapping):
                    continue
                score = candidate.get("similarity_score")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or float(score) < TEXT_MIN_SIMILARITY_SCORE
                ):
                    continue
                accepted.append(dict(candidate))
        return {"candidates": accepted, "package_errors": result.get("package_errors", [])}

    @staticmethod
    def _collect_local_evidence(result: Mapping[str, Any], evidence: list[dict[str, Any]]) -> None:
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            return
        known = {item.get("candidate_id") for item in evidence}
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            candidate_id = candidate.get("candidate_id")
            if isinstance(candidate_id, str) and candidate_id and candidate_id not in known:
                evidence.append(dict(candidate))
                known.add(candidate_id)

    @staticmethod
    def _grounding_records(response: Any) -> list[dict[str, Any]]:
        candidates = getattr(response, "candidates", None) or ()
        candidate = candidates[0] if candidates else None
        metadata = getattr(candidate, "grounding_metadata", None) if candidate is not None else None
        if metadata is None:
            metadata = getattr(response, "grounding_metadata", None)
        records: list[dict[str, Any]] = []
        for chunk in getattr(metadata, "grounding_chunks", None) or ():
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None) if web is not None else None
            if not isinstance(uri, str) or not uri.strip():
                continue
            record: dict[str, Any] = {"source_uri": uri.strip()}
            title = getattr(web, "title", None)
            if isinstance(title, str) and title.strip():
                record["title"] = title.strip()
            records.append(record)
        return records

    @staticmethod
    def _model_content(response: Any, calls: Sequence[Any]) -> types.Content:
        candidates = getattr(response, "candidates", None) or ()
        if candidates and getattr(candidates[0], "content", None) is not None:
            return candidates[0].content
        # Real Gemini responses include candidate content (with its
        # thought_signature).  Lightweight test doubles may not; an empty
        # compatibility turn is sufficient there and avoids fabricating SDK
        # FunctionCall objects from an incomplete double.
        return types.Content(
            role="model",
            parts=[],
        )

    @staticmethod
    def _normalize_item(item: Mapping[str, Any]) -> dict[str, Any]:
        payload = item["payload"]
        result: dict[str, Any] = {"payload": {"passage": payload["passage"].strip()}}
        if isinstance(item.get("candidate_id"), str) and item["candidate_id"].strip():
            result["candidate_id"] = item["candidate_id"].strip()
        return result

    @staticmethod
    def _validation_feedback(errors: Sequence[str]) -> str:
        error_lines = "\n".join(f"- {error}" for error in errors)
        return (
            "Kết quả JSON vừa rồi chưa hợp lệ. Hãy sinh lại JSON mới.\n\n"
            f"Lỗi:\n{error_lines}\n\n"
            "Chỉ trả JSON dạng {\"items\":[...]}. Item local chỉ dùng candidate_id có trong local evidence; "
            "item Google chỉ có payload.passage; passage không được rỗng; không thêm field khác."
        )
