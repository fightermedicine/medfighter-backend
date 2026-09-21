"""Typed, environment-driven settings.

Everything operational is a product/security *configuration*, not an
architectural constant (plan §5: the 2-device limit is "a product
configuration, not an architectural constant"). Values arrive via
FIGHTERS_-prefixed env vars; see .env.example.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIGHTERS_",
        env_file=(_ENV_PATH, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"

    # §12 — PostgreSQL is the single source of truth (local or Supabase hosted).
    database_url: str = "postgresql+asyncpg://fighters:fighters@localhost:5432/fighters"

    # §75 — raised when a critical client vulnerability ships.
    minimum_supported_app_version: str = "0.0.0"

    # ── Cloudflare & Supabase Cloud Integration ──────────────────────────────
    supabase_url: str | None = None
    supabase_key: str | None = None

    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    r2_bucket_name: str = "fighters-vault-packages"
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    cloudflare_access_key_id: str | None = None
    cloudflare_secret_access_key: str | None = None
    r2_public_domain: str | None = None
    cloudflare_edge_domain: str = ""  # Set via FIGHTERS_CLOUDFLARE_EDGE_DOMAIN env var in production

    # ── Google OAuth 2.0 Credentials ─────────────────────────────────────────
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # ── SMTP / Email Delivery ─────────────────────────────────────────────────
    # Supabase SMTP: host=smtp.supabase.io, port=465, user=<project_ref>,
    #                password=<service_role_key>
    smtp_host: str = "smtp.supabase.io"
    smtp_port: int = 465
    smtp_user: str | None = None            # FIGHTERS_SMTP_USER
    smtp_password: str | None = None        # FIGHTERS_SMTP_PASSWORD
    smtp_from_email: str = "noreply@medfighter.app"
    smtp_from_name: str = "MedFighter"
    smtp_use_tls: bool = True               # port 465 = SSL/TLS

    @property
    def email_enabled(self) -> bool:
        """True only when SMTP credentials are configured."""
        return bool(self.smtp_user and self.smtp_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
