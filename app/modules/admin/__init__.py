"""Admin module (§38–§40).

Web-dashboard-facing surface. Authorization is permission-based (§39), never
`is_admin == true`. Sensitive financial actions use four-eyes approval above
configurable thresholds (§40).
"""

from app.modules.admin.models import PlatformSetting

TABLES_OWNED: tuple[str, ...] = ("platform_settings",)

__all__ = ["PlatformSetting", "TABLES_OWNED"]
