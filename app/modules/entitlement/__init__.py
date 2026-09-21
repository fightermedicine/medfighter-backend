"""Entitlement module (§31, §43).

OWNS: entitlements, device_licenses — the ONLY writer. Entitlement is
separate from purchase (D7) and license is separate from entitlement (D8).
Licenses are device-bound (D9), short-lived, and revocable (§31, §32).
"""

TABLES_OWNED = ("entitlements", "device_licenses")
