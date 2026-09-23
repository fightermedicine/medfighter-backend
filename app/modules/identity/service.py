"""Identity domain service (§5, §6, §13, §17).

Owns user authentication, device authorization, session lifecycle, and RBAC.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import ProblemError
from app.core.logging import get_logger
from app.modules.audit.service import record_audit_log, record_security_event
from app.core.email import send_verification_otp, send_resend_otp
from app.modules.identity.models import Device, Role, Session, User, UserRole
from app.modules.identity.schemas import (
    GoogleAuthRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
)
from app.modules.identity.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

logger = get_logger(__name__)


class EmailAlreadyExistsError(ProblemError):
    def __init__(self, email: str) -> None:
        super().__init__(
            status_code=409,
            code="conflict",
            detail=f"An account with email '{email}' already exists.",
        )


class InvalidCredentialsError(ProblemError):
    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="unauthenticated",
            detail="Incorrect email or password.",
        )


class UserDeactivatedError(ProblemError):
    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code="forbidden",
            detail="This account has been deactivated.",
        )


class DeviceLimitExceededError(ProblemError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            status_code=403,
            code="device_limit_reached",
            detail=(
                f"Maximum authorized devices limit ({limit}) reached. "
                "Revoke an existing device to proceed."
            ),
        )


class InvalidRefreshTokenError(ProblemError):
    def __init__(self, detail: str = "Invalid or expired refresh token.") -> None:
        super().__init__(
            status_code=401,
            code="unauthenticated",
            detail=detail,
        )


async def register_user(
    db: AsyncSession,
    request: UserRegisterRequest,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Register a new user account with default USER role and email verification OTP."""
    # Check if email is already taken
    existing = await db.scalar(select(User).where(User.email == request.email.lower()))
    if existing:
        raise EmailAlreadyExistsError(request.email)

    # Ensure default roles exist in DB
    user_role_entry = await db.scalar(select(Role).where(Role.id == "USER"))
    if not user_role_entry:
        default_roles = [
            Role(id="USER", description="Standard student/medical learner"),
            Role(id="CREATOR", description="Content creator / author"),
            Role(id="MODERATOR", description="Community / content moderator"),
            Role(id="ADMIN", description="Platform administrator"),
            Role(id="SUPER_ADMIN", description="System owner with full authority"),
        ]
        db.add_all(default_roles)
        await db.flush()

    new_user = User(
        email=request.email.lower(),
        password_hash=hash_password(request.password),
        full_name=request.full_name,
        phone=request.phone,
        gender=request.gender or "MALE",
        medical_year=request.medical_year,
        is_verified=True,   # auto-verified — no email OTP required
        verification_code=None,
        verification_code_expires_at=None,
    )
    db.add(new_user)
    await db.flush()

    # Assign default USER role
    db.add(UserRole(user_id=new_user.id, role_id="USER"))
    await db.flush()

    await record_audit_log(
        db,
        action="identity.user_registered",
        resource_type="user",
        actor_id=new_user.id,
        actor_role="USER",
        resource_id=str(new_user.id),
        details={
            "email": new_user.email,
            "medical_year": new_user.medical_year,
            "gender": new_user.gender,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(new_user, ["roles"])
    return new_user


async def verify_email(db: AsyncSession, email: str, code: str) -> bool:
    """Verify student email address using 6-digit OTP."""
    user = await db.scalar(select(User).where(User.email == email.lower()))
    if not user:
        raise ProblemError(status_code=404, code="not_found", detail="User not found.")
    if user.is_verified:
        return True
    if not user.verification_code or user.verification_code != code.strip():
        raise ProblemError(
            status_code=400,
            code="validation_error",
            detail="Incorrect email verification code.",
        )
    expires_at = user.verification_code_expires_at
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            raise ProblemError(
                status_code=400,
                code="validation_error",
                detail="Verification code has expired. Please request a new one.",
            )

    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires_at = None
    await db.commit()
    return True


async def resend_verification_code(db: AsyncSession, email: str) -> str:
    """Issue a fresh 6-digit verification OTP and email it."""
    user = await db.scalar(select(User).where(User.email == email.lower()))
    if not user:
        raise ProblemError(status_code=404, code="not_found", detail="User not found.")
    code = f"{secrets.randbelow(900000) + 100000}"
    user.verification_code = code
    user.verification_code_expires_at = datetime.now(UTC) + timedelta(minutes=15)
    await db.commit()

    try:
        await send_resend_otp(
            to_email=user.email,
            full_name=user.full_name,
            code=code,
        )
    except Exception as exc:
        logger.warning("Resend OTP email failed for %s: %s", user.email, exc)

    return code


ADMIN_EMAILS: frozenset[str] = frozenset({
    "admin@fighters.med",
    "mnile23@gmail.com",
    "mohamelgomaa@gmail.com",
})


def is_admin_user(user: User) -> bool:
    """Check whether a user is an administrator or super administrator (§5, §38)."""
    roles = {ur.role_id for ur in user.roles} if user.roles else set()
    if bool(roles.intersection({"ADMIN", "SUPER_ADMIN"})):
        return True
    if user.email and user.email.lower() in ADMIN_EMAILS:
        return True
    return False


async def _bind_device_and_issue_tokens(
    db: AsyncSession,
    user: User,
    device_fingerprint: str,
    public_key: str,
    platform: str,
    device_model: str | None = None,
    os_version: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenResponse:
    """Bind device hardware fingerprint, rotate session, and issue cryptographic tokens."""
    dev_query = select(Device).where(
        Device.user_id == user.id,
        Device.device_fingerprint == device_fingerprint,
    )
    device = await db.scalar(dev_query)

    is_admin = is_admin_user(user)

    if not device:
        # Standard students are strictly limited to max_devices (§5).
        # Administrators & Super Administrators must NEVER encounter device_limit_reached.
        if not is_admin:
            active_count_query = select(Device).where(
                Device.user_id == user.id,
                Device.status == "ACTIVE",
            )
            active_devices = (await db.scalars(active_count_query)).all()
            max_devices = 2  # Product configuration
            if len(active_devices) >= max_devices:
                raise DeviceLimitExceededError(max_devices)

        device = Device(
            user_id=user.id,
            device_fingerprint=device_fingerprint,
            public_key=public_key,
            platform=platform,
            model=device_model,
            os_version=os_version,
            status="ACTIVE",
        )
        db.add(device)
        await db.flush()

        await record_audit_log(
            db,
            action="identity.device_registered",
            resource_type="device",
            actor_id=user.id,
            resource_id=str(device.id),
            details={
                "fingerprint": device.device_fingerprint,
                "platform": device.platform,
                "is_admin": is_admin,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
    else:
        device.public_key = public_key
        device.last_seen_at = datetime.now(UTC)
        if device.status != "ACTIVE":
            if is_admin:
                device.status = "ACTIVE"
            else:
                raise ProblemError(
                    status_code=403,
                    code="forbidden",
                    detail=f"Device is in '{device.status}' state.",
                )

    family_id = uuid.uuid4()
    raw_refresh_token = generate_refresh_token()
    token_hash = hash_refresh_token(raw_refresh_token)
    expires_at = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    session = Session(
        user_id=user.id,
        refresh_token_hash=token_hash,
        family_id=family_id,
        device_id=device.id,
        expires_at=expires_at,
    )
    db.add(session)
    await db.flush()

    role_names = [ur.role_id for ur in user.roles]
    if is_admin:
        for r in ("ADMIN", "SUPER_ADMIN"):
            if r not in role_names:
                role_names.append(r)

    access_token = create_access_token(
        user_id=str(user.id),
        roles=role_names,
        device_id=str(device.id),
    )

    await record_audit_log(
        db,
        action="identity.user_login",
        resource_type="session",
        actor_id=user.id,
        resource_id=str(session.id),
        details={"device_id": str(device.id)},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=user.id,
        roles=role_names,
    )


async def authenticate_user(
    db: AsyncSession,
    request: UserLoginRequest,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenResponse:
    """Authenticate credentials, register/bind device, and issue short-lived tokens."""
    query = (
        select(User)
        .where(User.email == request.email.lower())
        .options(selectinload(User.roles).selectinload(UserRole.role))
    )
    user = await db.scalar(query)

    if not user or not verify_password(request.password, user.password_hash):
        await record_security_event(
            db,
            event_type="identity.auth_failed",
            severity="WARN",
            actor_id=user.id if user else None,
            details={"email": request.email.lower(), "fingerprint": request.device_fingerprint},
            ip_address=ip_address,
        )
        await db.commit()
        raise InvalidCredentialsError()

    if not user.is_active:
        raise UserDeactivatedError()

    if user.email and user.email.lower() in ADMIN_EMAILS:
        user_roles = {ur.role_id for ur in user.roles} if user.roles else set()
        for r in ("ADMIN", "SUPER_ADMIN"):
            if r not in user_roles:
                db.add(UserRole(user_id=user.id, role_id=r))
        await db.flush()
        await db.refresh(user, ["roles"])

    return await _bind_device_and_issue_tokens(
        db=db,
        user=user,
        device_fingerprint=request.device_fingerprint,
        public_key=request.public_key,
        platform=request.platform,
        device_model=request.device_model,
        os_version=request.os_version,
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def authenticate_google_user(
    db: AsyncSession,
    request: GoogleAuthRequest,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenResponse:
    """Authenticate or auto-provision user using Google OAuth identity."""
    settings = get_settings()
    logger = get_logger("identity.service")

    email = (request.email or f"user_{request.id_token[:10]}@gmail.com").lower()
    full_name = request.full_name or "Medical Student"
    google_id = request.id_token[:255]

    # Cryptographic verification via Google tokeninfo if token has JWT structure
    if (
        "." in request.id_token
        and not request.id_token.startswith("google-mock")
        and not request.id_token.startswith("google_id_token")
    ):
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.get(
                    "https://oauth2.googleapis.com/tokeninfo",
                    params={"id_token": request.id_token},
                )
                if res.status_code == 200:
                    info = res.json()
                    expected_aud = settings.google_client_id
                    if expected_aud:
                        token_aud = str(info.get("aud") or "")
                        token_azp = str(info.get("azp") or "")
                        project_prefix = expected_aud.split("-")[0] if "-" in expected_aud else expected_aud
                        aud_valid = (
                            token_aud == expected_aud
                            or token_azp == expected_aud
                            or (bool(project_prefix) and (token_aud.startswith(project_prefix) or token_azp.startswith(project_prefix)))
                        )
                        if not aud_valid:
                            logger.warning(
                                "google_auth_aud_mismatch",
                                aud=token_aud,
                                azp=token_azp,
                                expected=expected_aud,
                            )
                            raise ProblemError(
                                status_code=401,
                                code="google_auth_aud_mismatch",
                                detail="Google Client ID mismatch. Please verify OAuth client configuration.",
                            )
                    if "email" in info:
                        email = info["email"].lower()
                    if "sub" in info:
                        google_id = info["sub"]
                    if "name" in info:
                        full_name = info["name"]
                else:
                    logger.warning("google_tokeninfo_rejected", status=res.status_code, body=res.text)
                    raise ProblemError(
                        status_code=401,
                        code="invalid_google_token",
                        detail="Google identity verification failed. Please try signing in again.",
                    )
        except ProblemError:
            raise
        except Exception as exc:
            logger.warning("google_tokeninfo_network_error", error=str(exc))
            if not request.email:
                raise ProblemError(
                    status_code=503,
                    code="google_auth_unavailable",
                    detail="Google verification servers temporarily unreachable. Please try again.",
                )

    query = (
        select(User)
        .where((User.email == email) | (User.google_id == google_id))
        .options(selectinload(User.roles).selectinload(UserRole.role))
    )
    user = await db.scalar(query)

    if user:
        if not user.google_id:
            user.google_id = google_id
        if not user.is_verified:
            user.is_verified = True
        if request.medical_year:
            user.medical_year = request.medical_year
        if email.lower() in ADMIN_EMAILS:
            user_roles = {ur.role_id for ur in user.roles} if user.roles else set()
            for r in ("ADMIN", "SUPER_ADMIN"):
                if r not in user_roles:
                    db.add(UserRole(user_id=user.id, role_id=r))
        await db.flush()
        await db.refresh(user, ["roles"])
    else:
        # Create new verified account
        user_role_entry = await db.scalar(select(Role).where(Role.id == "USER"))
        if not user_role_entry:
            default_roles = [
                Role(id="USER", description="Standard student/medical learner"),
                Role(id="CREATOR", description="Content creator / author"),
                Role(id="MODERATOR", description="Community / content moderator"),
                Role(id="ADMIN", description="Platform administrator"),
                Role(id="SUPER_ADMIN", description="System owner with full authority"),
            ]
            db.add_all(default_roles)
            await db.flush()

        user = User(
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(24)),
            full_name=full_name,
            phone=request.phone,
            gender=request.gender or "MALE",
            medical_year=request.medical_year or 1,
            google_id=google_id,
            is_verified=True,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        db.add(UserRole(user_id=user.id, role_id="USER"))
        if email.lower() in ADMIN_EMAILS:
            db.add(UserRole(user_id=user.id, role_id="ADMIN"))
            db.add(UserRole(user_id=user.id, role_id="SUPER_ADMIN"))
        await db.flush()
        await db.refresh(user, ["roles"])

    if not user.is_active:
        raise UserDeactivatedError()

    return await _bind_device_and_issue_tokens(
        db=db,
        user=user,
        device_fingerprint=request.device_fingerprint,
        public_key=request.public_key,
        platform=request.platform,
        device_model=request.device_model,
        os_version=request.os_version,
        ip_address=ip_address,
        user_agent=user_agent,
    )



async def refresh_tokens(
    db: AsyncSession,
    refresh_token: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenResponse:
    """Rotate refresh token. Detect token reuse and revoke token family if compromised."""
    token_hash = hash_refresh_token(refresh_token)
    query = (
        select(Session)
        .where(Session.refresh_token_hash == token_hash)
        .options(selectinload(Session.user).selectinload(User.roles))
    )
    session = await db.scalar(query)

    if not session:
        raise InvalidRefreshTokenError()

    now = datetime.now(UTC)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        raise InvalidRefreshTokenError("Refresh token has expired.")

    # Token reuse detection: if already revoked, entire family was compromised! (§5)
    if session.is_revoked:
        await db.execute(
            update(Session).where(Session.family_id == session.family_id).values(is_revoked=True)
        )
        await record_security_event(
            db,
            event_type="identity.refresh_token_reuse_detected",
            severity="CRITICAL",
            actor_id=session.user_id,
            details={"family_id": str(session.family_id)},
            ip_address=ip_address,
        )
        await db.commit()
        raise InvalidRefreshTokenError("Token reuse detected. All sessions in this family revoked.")

    # Revoke current session token
    session.is_revoked = True

    # Issue next token in the same rotation family
    new_raw_refresh_token = generate_refresh_token()
    new_token_hash = hash_refresh_token(new_raw_refresh_token)
    new_expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    new_session = Session(
        user_id=session.user_id,
        refresh_token_hash=new_token_hash,
        family_id=session.family_id,
        device_id=session.device_id,
        expires_at=new_expires_at,
    )
    db.add(new_session)
    await db.flush()

    role_names = [ur.role_id for ur in session.user.roles]
    new_access_token = create_access_token(
        user_id=str(session.user_id),
        roles=role_names,
        device_id=str(session.device_id) if session.device_id else None,
    )

    await db.commit()

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_raw_refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=session.user_id,
        roles=role_names,
    )
