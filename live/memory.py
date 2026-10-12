"""Backend-owned bounded conversation memory."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionMemory:
    history: list[dict[str, str]] = field(default_factory=list)

    def append(self, role: str, content: str) -> None:
        if role in {"user", "assistant"} and content.strip():
            self.history.append({"role": role, "content": content.strip()[:4000]})
            del self.history[:-6]


class SessionMemoryStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionMemory] = {}

    def get(self, session_id: str) -> SessionMemory:
        return self._sessions.setdefault(session_id, SessionMemory())
