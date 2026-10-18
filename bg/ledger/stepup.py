"""Per-user step-up (one-time code) attempt counter. Bank-side state, independent of chat sessions (AUDIT-3).

Wrong codes are counted per USER, not per action: re-sending the request, correcting it or opening a new
session does not give a fresh budget. After MAX_OTP_ATTEMPTS consecutive wrong codes, code approval is locked
for that user until an explicit ``reset`` (in a bank: a call to support or an in-branch check; in the demo:
"Reset demo" builds a fresh backend). A correct code clears the count of consecutive failures.
"""
from __future__ import annotations

import threading

from bg.contracts import params


class StepUpAttempts:
    def __init__(self, max_attempts: int = params.MAX_OTP_ATTEMPTS) -> None:
        self.max_attempts = max_attempts
        self._failures: dict[str, int] = {}
        self._locked: set[str] = set()
        self._lock = threading.Lock()

    def failures(self, user_id: str) -> int:
        with self._lock:
            return self._failures.get(user_id, 0)

    def remaining(self, user_id: str) -> int:
        with self._lock:
            if user_id in self._locked:
                return 0
            return max(self.max_attempts - self._failures.get(user_id, 0), 0)

    def locked(self, user_id: str) -> bool:
        with self._lock:
            return user_id in self._locked

    def record_failure(self, user_id: str) -> int:
        """Count one wrong code; returns the attempts left (0 means the user is now locked)."""
        with self._lock:
            n = self._failures.get(user_id, 0) + 1
            self._failures[user_id] = n
            if n >= self.max_attempts:
                self._locked.add(user_id)
            return 0 if user_id in self._locked else self.max_attempts - n

    def record_success(self, user_id: str) -> None:
        with self._lock:
            if user_id not in self._locked:
                self._failures.pop(user_id, None)

    def reset(self, user_id: str) -> None:
        """Explicit unlock (support / identity re-check). Never reachable from chat."""
        with self._lock:
            self._failures.pop(user_id, None)
            self._locked.discard(user_id)
