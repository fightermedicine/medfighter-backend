"""Identity module (§13–§15, §43).

OWNS: users, sessions, devices, authentication state.
Phase 2 implements: registration, OTP, login, refresh rotation with families,
device keypairs + registration. Nothing else may write identity tables.
"""

TABLES_OWNED = ("users", "sessions", "devices", "roles", "user_roles")
