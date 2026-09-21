"""Audit module (§41, §68).

OWNS: audit_log, security_events. Append-only from the application
perspective: no ordinary endpoint may UPDATE or DELETE these rows (§41).
"""

TABLES_OWNED = ("audit_log", "security_events")
