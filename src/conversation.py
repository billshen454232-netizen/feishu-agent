from __future__ import annotations

from collections import defaultdict, deque
from typing import Literal

Role = Literal["user", "assistant"]
Message = dict[str, str]


class ConversationStore:
    """In-memory bounded conversation history keyed by Feishu chat_id."""

    def __init__(self, max_messages: int) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be at least 1")
        self._messages: dict[str, deque[Message]] = defaultdict(lambda: deque(maxlen=max_messages))

    def append(self, chat_id: str, role: Role, content: str) -> None:
        self._messages[chat_id].append({"role": role, "content": content})

    def get(self, chat_id: str) -> list[Message]:
        return list(self._messages.get(chat_id, []))

    def clear(self, chat_id: str) -> None:
        self._messages.pop(chat_id, None)
