"""Money is always BIGINT piastres — 100 EGP = 10_000 (plan §9)."""

from __future__ import annotations

from typing import NewType

Piastres = NewType("Piastres", int)


def egp_to_piastres(egp: int | float) -> Piastres:
    return Piastres(round(egp * 100))


def format_egp(amount: Piastres) -> str:
    sign = "-" if amount < 0 else ""
    p = abs(int(amount))
    return f"{sign}{p // 100}.{p % 100:02d} EGP"


def validate_piastres(amount: int) -> Piastres:
    if amount < 0:
        raise ValueError("money amounts cannot be negative")
    return Piastres(amount)


def piastres_to_egp(amount: int | float | object | None) -> float:
    if amount is None:
        return 0.0
    return float(amount) / 100.0

