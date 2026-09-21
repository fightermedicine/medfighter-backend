"""Cursor pagination (§44)."""

from __future__ import annotations

import base64
from dataclasses import dataclass

MAX_LIMIT = 100
DEFAULT_LIMIT = 50


class CursorError(ValueError):
    pass


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    padding = "=" * (-len(cursor) % 4)
    try:
        return base64.urlsafe_b64decode(cursor + padding).decode()
    except Exception as exc:
        raise CursorError("invalid cursor") from exc


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None
