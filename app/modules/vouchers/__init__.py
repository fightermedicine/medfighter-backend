"""Voucher & Promotion Subsystem (§11, §35).

Manages promo codes and batch scratch-card vouchers for wallet funding and course unlocking.
"""

TABLES_OWNED: tuple[str, ...] = ("voucher_batches", "vouchers", "voucher_redemptions")
