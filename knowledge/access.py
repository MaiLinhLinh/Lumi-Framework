"""Short-lived, attempt-scoped authorization for the Knowledge Gateway."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .tools import KNOWLEDGE_TOOL_REGISTRY


KNOWLEDGE_TOKEN_HEADER = "X-Lumi-Knowledge-Token"


class KnowledgeAccessError(PermissionError):
    """A caller is not authorized to use a Knowledge Gateway operation."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _unique_strings(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be an iterable of strings.")
    normalized = tuple(_text(value, f"{field_name}[]") for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates.")
    return normalized


@dataclass(frozen=True, slots=True)
class KnowledgeAccessContext:
    """Opaque bearer context; its credential is deliberately absent from repr."""

    _credential: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_credential",
            _text(self._credential, "knowledge access credential"),
        )

    @property
    def transport_token(self) -> str:
        """Return the token only for the trusted transport boundary."""

        return self._credential

    def log_fields(self) -> dict[str, str]:
        return {"kind": "knowledge_access_context", "credential": "[REDACTED]"}


@dataclass(frozen=True, slots=True)
class KnowledgeGrant:
    token: str = field(repr=False)
    run_id: str
    task_id: str
    allowed_tools: tuple[str, ...]
    allowed_package_ids: tuple[str, ...]
    expires_at: float


class KnowledgeGrantRegistry:
    """In-memory bearer grants issued independently for every execution attempt."""

    def __init__(
        self,
        *,
        default_ttl_seconds: float = 120.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            isinstance(default_ttl_seconds, bool)
            or not isinstance(default_ttl_seconds, (int, float))
            or default_ttl_seconds <= 0
        ):
            raise ValueError("default_ttl_seconds must be a positive number.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._default_ttl_seconds = float(default_ttl_seconds)
        self._clock = clock
        self._grants: dict[str, KnowledgeGrant] = {}
        self._lock = threading.RLock()

    def issue(
        self,
        *,
        run_id: str,
        task_id: str,
        allowed_tools: Iterable[str],
        allowed_package_ids: Iterable[str],
        ttl_seconds: float | None = None,
    ) -> KnowledgeAccessContext:
        safe_run_id = _text(run_id, "run_id")
        safe_task_id = _text(task_id, "task_id")
        safe_tools = _unique_strings(allowed_tools, "allowed_tools")
        KNOWLEDGE_TOOL_REGISTRY.validate_grants(safe_tools)
        safe_package_ids = _unique_strings(
            allowed_package_ids, "allowed_package_ids"
        )
        ttl = self._default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
            raise ValueError("ttl_seconds must be a positive number.")
        token = secrets.token_urlsafe(32)
        with self._lock:
            while token in self._grants:
                token = secrets.token_urlsafe(32)
            self._grants[token] = KnowledgeGrant(
                token=token,
                run_id=safe_run_id,
                task_id=safe_task_id,
                allowed_tools=safe_tools,
                allowed_package_ids=safe_package_ids,
                expires_at=self._clock() + float(ttl),
            )
        return KnowledgeAccessContext(token)

    def authenticate(self, token: str) -> KnowledgeAccessContext:
        """Create an opaque context from a bearer token at a trusted boundary."""

        context = KnowledgeAccessContext(token)
        self._active_grant(context)
        return context

    def authorize(
        self,
        access_context: KnowledgeAccessContext,
        *,
        tool_name: str,
        package_ids: Iterable[str],
    ) -> KnowledgeGrant:
        grant = self._active_grant(access_context)
        if tool_name not in grant.allowed_tools:
            raise KnowledgeAccessError(
                f"knowledge tool '{tool_name}' is not granted for this task."
            )
        requested = _unique_strings(package_ids, "package_ids")
        outside = sorted(set(requested) - set(grant.allowed_package_ids))
        if outside:
            raise KnowledgeAccessError(
                f"knowledge packages are outside this task grant: {outside}."
            )
        return grant

    def revoke(self, access_context: KnowledgeAccessContext) -> bool:
        token = self._credential(access_context)
        with self._lock:
            return self._grants.pop(token, None) is not None

    def revoke_run(self, run_id: str) -> int:
        safe_run_id = _text(run_id, "run_id")
        with self._lock:
            tokens = [
                token
                for token, grant in self._grants.items()
                if grant.run_id == safe_run_id
            ]
            for token in tokens:
                del self._grants[token]
            return len(tokens)

    def active_grant_count(self) -> int:
        with self._lock:
            self._remove_expired_locked()
            return len(self._grants)

    def _active_grant(self, access_context: KnowledgeAccessContext) -> KnowledgeGrant:
        token = self._credential(access_context)
        with self._lock:
            grant = self._grants.get(token)
            if grant is None:
                raise KnowledgeAccessError("knowledge access token is invalid or revoked.")
            if self._clock() >= grant.expires_at:
                del self._grants[token]
                raise KnowledgeAccessError("knowledge access token has expired.")
            return grant

    @staticmethod
    def _credential(access_context: KnowledgeAccessContext) -> str:
        if not isinstance(access_context, KnowledgeAccessContext):
            raise KnowledgeAccessError(
                "a valid KnowledgeAccessContext is required."
            )
        return access_context.transport_token

    def _remove_expired_locked(self) -> None:
        now = self._clock()
        for token in [
            token for token, grant in self._grants.items() if now >= grant.expires_at
        ]:
            del self._grants[token]


__all__ = [
    "KNOWLEDGE_TOKEN_HEADER",
    "KnowledgeAccessContext",
    "KnowledgeAccessError",
    "KnowledgeGrant",
    "KnowledgeGrantRegistry",
]
