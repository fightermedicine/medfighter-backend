"""Domain exceptions shared across modules.

Modules raise these; routers map them to problem+json. Keeps domain logic
importable without FastAPI (helps unit tests and future workers).
"""

from __future__ import annotations


class FightersError(Exception):
    """Base for expected domain errors (mapped to problem+json at the edge)."""


class NotFoundError(FightersError):
    pass


class ConflictError(FightersError):
    pass


class ForbiddenError(FightersError):
    pass
