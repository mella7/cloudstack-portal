"""Redacts secrets from every line before it is stored or streamed."""

from __future__ import annotations

import re

MASK = "***"
_KEY_VALUE = re.compile(
    r"(?i)\b((?:api[_-]?key|secret[_-]?key|secret|signature|password|token)\"?\s*[=:]\s*)(\"?)[^\s\"&,]+(\"?)"
)


class Masker:
    def __init__(self, secrets: list[str] | None = None) -> None:
        # Longest first so a secret containing another is fully replaced.
        self._secrets = sorted({s for s in (secrets or []) if len(s) >= 4}, key=len, reverse=True)

    def mask(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}{m.group(3)}", text)
