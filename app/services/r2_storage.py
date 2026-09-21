"""Cloudflare R2 S3-compatible Object Storage Service.

Manages encrypted medical packages (.enc, chunks, manifests) stored in
Cloudflare R2 buckets with zero egress fees and global edge delivery.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from app.core.config import get_settings


class R2StorageService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def is_configured(self) -> bool:
        """Returns True if S3 credentials for R2 are fully provided."""
        return bool(
            self.settings.cloudflare_account_id
            and self.settings.r2_access_key_id
            and self.settings.r2_secret_access_key
        )

    def generate_download_url(
        self,
        package_id: str,
        filename: str | None = None,
        expires_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Generates an edge download URL for an encrypted package.

        Supports:
        1. Custom domain CDN acceleration if r2_public_domain is configured.
        2. Cryptographic S3 presigned URL if R2 S3 access keys are present.
        3. Authenticated edge streaming URL through Cloudflare Worker.
        """
        obj_key = f"packages/{package_id}.fght"
        if filename:
            obj_key = f"packages/{package_id}/{filename}"

        if self.settings.r2_public_domain:
            domain = self.settings.r2_public_domain.rstrip("/")
            return {
                "download_url": f"{domain}/{obj_key}",
                "provider": "cloudflare_r2_cdn",
                "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_seconds)).isoformat(),
                "object_key": obj_key,
                "is_direct_cdn": True,
            }

        if self.is_configured:
            presigned = self._sign_s3_url(
                method="GET",
                object_key=obj_key,
                expires_seconds=expires_seconds,
            )
            return {
                "download_url": presigned,
                "provider": "cloudflare_r2_presigned",
                "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_seconds)).isoformat(),
                "object_key": obj_key,
                "is_direct_cdn": False,
            }

        # Fallback to Cloudflare Worker edge proxy / application endpoint
        edge_domain = (self.settings.cloudflare_edge_domain or "").rstrip("/")
        download_path = f"/v1/packages/{package_id}/stream"
        full_url = f"{edge_domain}{download_path}" if edge_domain else download_path
        return {
            "download_url": full_url,
            "provider": "edge_proxy",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_seconds)).isoformat(),
            "object_key": obj_key,
            "is_direct_cdn": False,
        }

    def generate_upload_url(
        self,
        package_id: str,
        expires_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Generates an S3 presigned PUT URL for creators/admins to push encrypted packages."""
        obj_key = f"packages/{package_id}.fght"

        if self.is_configured:
            presigned = self._sign_s3_url(
                method="PUT",
                object_key=obj_key,
                expires_seconds=expires_seconds,
            )
            return {
                "upload_url": presigned,
                "object_key": obj_key,
                "bucket": self.settings.r2_bucket_name,
                "provider": "cloudflare_r2_presigned",
            }

        return {
            "upload_url": f"/v1/admin/packages/{package_id}/upload",
            "object_key": obj_key,
            "bucket": self.settings.r2_bucket_name,
            "provider": "edge_proxy",
        }

    def _sign_s3_url(
        self,
        method: str,
        object_key: str,
        expires_seconds: int,
    ) -> str:
        """Lightweight AWS SigV4 Presigned URL implementation for Cloudflare R2."""
        access_key = self.settings.r2_access_key_id or ""
        secret_key = self.settings.r2_secret_access_key or ""
        bucket = self.settings.r2_bucket_name
        account_id = self.settings.cloudflare_account_id

        host = f"{account_id}.r2.cloudflarestorage.com"
        now = datetime.now(UTC)
        datestamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")

        region = "auto"
        service = "s3"
        credential_scope = f"{datestamp}/{region}/{service}/aws4_request"

        encoded_key = quote(f"/{bucket}/{object_key.lstrip('/')}", safe="/")

        canonical_querystring = (
            f"X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-Credential={quote(f'{access_key}/{credential_scope}', safe='')}"
            f"&X-Amz-Date={amz_date}"
            f"&X-Amz-Expires={expires_seconds}"
            f"&X-Amz-SignedHeaders=host"
        )

        canonical_headers = f"host:{host}\n"
        signed_headers = "host"
        payload_hash = "UNSIGNED-PAYLOAD"

        canonical_request = (
            f"{method}\n"
            f"{encoded_key}\n"
            f"{canonical_querystring}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        string_to_sign = (
            f"{algorithm}\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        # Signing key calculation
        k_date = hmac.new(f"AWS4{secret_key}".encode("utf-8"), datestamp.encode("utf-8"), hashlib.sha256).digest()
        k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
        k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
        k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()

        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        return f"https://{host}{encoded_key}?{canonical_querystring}&X-Amz-Signature={signature}"
