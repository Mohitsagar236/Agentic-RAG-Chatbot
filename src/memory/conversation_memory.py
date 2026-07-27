"""Bounded, thread-safe sliding-window conversation memory."""

from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Deque, List


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class ConversationMemory:
    """Keep only the latest complete window of user/assistant messages."""

    def __init__(self, window: int = 6):
        if window < 1:
            raise ValueError(f"window must be at least 1, got {window}")
        self._turns_window = window
        self._history: Deque[Message] = deque(maxlen=window * 2)
        self._lock = RLock()

    def add_user(self, content: str) -> None:
        with self._lock:
            self._history.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        with self._lock:
            self._history.append(Message(role="assistant", content=content))

    def get_history(self) -> List[Message]:
        with self._lock:
            return list(self._history)

    def to_langchain_messages(self) -> List[dict]:
        return [
            {"role": message.role, "content": message.content}
            for message in self.get_history()
        ]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._history)
