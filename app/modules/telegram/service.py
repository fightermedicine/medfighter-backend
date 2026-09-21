"""Telegram Bot API service — low-level send helpers and DB helpers.

All functions are non-blocking (async httpx) and never raise — failures are
logged and swallowed so that Telegram delivery never blocks a payment flow.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.models import PlatformSetting
from app.modules.telegram.models import TelegramChat

logger = logging.getLogger(__name__)

_PROTECTED_ADMINS = {"mohamed_hamed_samaha", "moh_gom3a"}

# ── Telegram API helpers ──────────────────────────────────────────────────────

_BOT_TOKEN: str = ""  # populated once at startup via init_service()
_API_BASE: str = ""
_client: httpx.AsyncClient | None = None


def init_service(token: str) -> None:
    """Called once at startup with the bot token from settings."""
    global _BOT_TOKEN, _API_BASE, _client  # noqa: PLW0603
    _BOT_TOKEN = token
    _API_BASE = f"https://api.telegram.org/bot{token}"
    _client = httpx.AsyncClient(timeout=10.0)


async def _post(method: str, **kwargs: Any) -> dict | None:
    """Fire-and-forget POST to the Bot API. Returns response dict or None on error."""
    if not _client:
        logger.warning("Telegram service not initialised — skipping %s", method)
        return None
    try:
        resp = await _client.post(f"{_API_BASE}/{method}", json=kwargs)
        data: dict = resp.json()
        if not data.get("ok"):
            logger.warning("Telegram %s failed: %s", method, data.get("description"))
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram %s error: %s", method, exc)
        return None


def build_payment_keyboard(reference: str) -> dict:
    """Build Telegram inline keyboard for payment verification."""
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"pay_app:{reference}"},
                {"text": "Decline", "callback_data": f"pay_dec:{reference}"},
                {"text": "Request Proof", "callback_data": f"pay_req:{reference}"},
            ]
        ]
    }


async def send_message(
    chat_id: int,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> bool:
    """Send a text message to a chat. Returns True on success."""
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    data = await _post("sendMessage", **payload)
    return bool(data and data.get("ok"))


async def send_photo(
    chat_id: int,
    photo: bytes | str,
    caption: str = "",
    reply_markup: dict | None = None,
) -> bool:
    """Send a photo (bytes or Telegram file_id) to a chat with optional inline keyboard. Returns True on success."""
    if not _client:
        return False
    try:
        if isinstance(photo, str):
            # Telegram file_id — send via JSON POST (instant delivery, zero re-upload)
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "photo": photo,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            data = await _post("sendPhoto", **payload)
            return bool(data and data.get("ok"))
        else:
            # Raw bytes (e.g. uploaded from mobile app) — upload via multipart
            import json

            form_data: dict[str, Any] = {
                "chat_id": str(chat_id),
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_markup is not None:
                form_data["reply_markup"] = json.dumps(reply_markup)
            resp = await _client.post(
                f"{_API_BASE}/sendPhoto",
                data=form_data,
                files={"photo": ("receipt.jpg", photo, "image/jpeg")},
            )
            data: dict = resp.json()
            return bool(data.get("ok"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram sendPhoto error: %s", exc)
        return False


async def answer_callback_query(
    callback_query_id: str,
    text: str = "",
    show_alert: bool = False,
) -> bool:
    """Acknowledge an incoming callback_query from an inline keyboard button."""
    data = await _post(
        "answerCallbackQuery",
        callback_query_id=callback_query_id,
        text=text,
        show_alert=show_alert,
    )
    return bool(data and data.get("ok"))


async def edit_message_caption(
    chat_id: int,
    message_id: int,
    caption: str,
    reply_markup: dict | None = None,
) -> bool:
    """Edit the caption of a photo message."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    data = await _post("editMessageCaption", **payload)
    return bool(data and data.get("ok"))


async def edit_message_reply_markup(
    chat_id: int,
    message_id: int,
    reply_markup: dict | None = None,
) -> bool:
    """Edit the inline keyboard attached to a message."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    data = await _post("editMessageReplyMarkup", **payload)
    return bool(data and data.get("ok"))


async def get_updates(offset: int, timeout: int = 30) -> list[dict]:
    """Long-poll for updates. Returns list of update objects."""
    if not _client:
        return []
    try:
        resp = await _client.get(
            f"{_API_BASE}/getUpdates",
            params={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": '["message", "callback_query"]',
            },
            timeout=timeout + 5,
        )
        data: dict = resp.json()
        return data.get("result", []) if data.get("ok") else []
    except Exception as exc:  # noqa: BLE001
        logger.debug("Telegram getUpdates error: %s", exc)
        return []


# ── DB helpers ────────────────────────────────────────────────────────────────


async def get_or_create_chat(
    db: AsyncSession,
    *,
    chat_id: int,
    chat_type: str,
    title: str | None,
    username: str | None,
) -> TelegramChat:
    """Return existing TelegramChat row or insert a new one."""
    existing = await db.scalar(
        select(TelegramChat).where(TelegramChat.chat_id == chat_id)
    )
    if existing:
        return existing
    chat = TelegramChat(
        chat_id=chat_id,
        chat_type=chat_type,
        title=title,
        username=username,
    )
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return chat


async def mark_admin_channel(db: AsyncSession, chat_id: int) -> None:
    """Mark a chat as an admin channel."""
    chat = await db.scalar(
        select(TelegramChat).where(TelegramChat.chat_id == chat_id)
    )
    if chat:
        chat.is_admin_channel = True
        await db.commit()


async def link_user(db: AsyncSession, chat_id: int, user_id: uuid.UUID) -> None:
    """Associate a Telegram chat with a MedFighter user account."""
    chat = await db.scalar(
        select(TelegramChat).where(TelegramChat.chat_id == chat_id)
    )
    if chat:
        chat.linked_user_id = user_id
        await db.commit()


async def broadcast_to_admins(
    db: AsyncSession,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Send a message to all registered admin groups (groups and supergroups only)."""
    rows = (
        await db.scalars(
            select(TelegramChat).where(
                TelegramChat.is_admin_channel.is_(True),
                TelegramChat.chat_type.in_(["group", "supergroup"]),
            )
        )
    ).all()
    for row in rows:
        await send_message(row.chat_id, text, reply_markup=reply_markup)


async def forward_message(
    to_chat_id: int,
    from_chat_id: int,
    message_id: int,
) -> int | None:
    """Forward a message natively — renders as 'Forwarded from <user>' in Telegram.

    Returns the new message_id in to_chat_id on success, or None on failure.
    """
    data = await _post(
        "forwardMessage",
        chat_id=to_chat_id,
        from_chat_id=from_chat_id,
        message_id=message_id,
    )
    if data and data.get("ok"):
        return (data.get("result") or {}).get("message_id")
    return None


async def broadcast_forward_to_admins(
    db: AsyncSession,
    from_chat_id: int,
    message_id: int,
) -> dict[int, int]:
    """Natively forward a student message to all admin groups.

    Admins see a clean 'Forwarded from <student>' in the group.
    Returns mapping: {group_chat_id: group_message_id}
    """
    rows = (
        await db.scalars(
            select(TelegramChat).where(
                TelegramChat.is_admin_channel.is_(True),
                TelegramChat.chat_type.in_(["group", "supergroup"]),
            )
        )
    ).all()
    result_map: dict[int, int] = {}
    for row in rows:
        fwd_id = await forward_message(row.chat_id, from_chat_id, message_id)
        if fwd_id:
            result_map[row.chat_id] = fwd_id
    return result_map


async def broadcast_photo_to_admins(
    db: AsyncSession,
    photo: bytes | str,
    caption: str = "",
    reply_markup: dict | None = None,
) -> None:
    """Send a photo with caption to all registered admin groups only."""
    rows = (
        await db.scalars(
            select(TelegramChat).where(
                TelegramChat.is_admin_channel.is_(True),
                TelegramChat.chat_type.in_(["group", "supergroup"]),
            )
        )
    ).all()
    for row in rows:
        success = await send_photo(
            row.chat_id, photo, caption=caption, reply_markup=reply_markup
        )
        if not success:
            await send_message(row.chat_id, caption, reply_markup=reply_markup)


async def notify_student(db: AsyncSession, user_id: uuid.UUID, text: str) -> None:
    """Send a message to the student's linked Telegram DM (if registered)."""
    chat = await db.scalar(
        select(TelegramChat).where(
            TelegramChat.linked_user_id == user_id,
            TelegramChat.chat_type == "private",
        )
    )
    if chat:
        await send_message(chat.chat_id, text)


# ── Admin & Group Control Helpers ─────────────────────────────────────────────


async def _load_persisted_admins(db: AsyncSession) -> set[str]:
    """Load additional admin usernames stored in PlatformSetting."""
    try:
        setting = await db.get(PlatformSetting, "telegram_bot_admins")
        if setting and setting.value:
            return {u.lstrip("@").lower() for u in setting.value.get("usernames", [])}
    except Exception:  # noqa: BLE001
        pass
    return set()


async def is_admin(
    db: AsyncSession,
    chat_id: int,
    username: str | None,
    chat_type: str | None = None,
) -> bool:
    """Check whether a given user or chat context has bot admin privileges.

    Rule 1: Any user in an admin group (group or supergroup) has full admin rights.
    Rule 2: Protected admins and configured admin usernames have admin rights.
    Rule 3: Chats marked as admin channels have admin rights.
    """
    if chat_type in ("group", "supergroup") or chat_id < 0:
        return True

    from app.modules.telegram.handlers import _ADMIN_USERNAMES

    if username:
        clean = username.lstrip("@").lower()
        if clean in _ADMIN_USERNAMES or clean in _PROTECTED_ADMINS:
            return True
        persisted = await _load_persisted_admins(db)
        if clean in persisted:
            _ADMIN_USERNAMES.add(clean)
            return True

    chat = await db.scalar(
        select(TelegramChat).where(TelegramChat.chat_id == chat_id)
    )
    if chat and chat.is_admin_channel:
        return True

    return False


async def add_bot_admin(db: AsyncSession, target: str) -> tuple[bool, str]:
    """Add a new Telegram username to bot administrators."""
    from app.modules.telegram.handlers import _ADMIN_USERNAMES

    clean_username = target.lstrip("@").strip().lower()
    if not clean_username:
        return False, "⚠️ Invalid username specified."

    _ADMIN_USERNAMES.add(clean_username)

    try:
        setting = await db.get(PlatformSetting, "telegram_bot_admins")
        if not setting:
            setting = PlatformSetting(
                key="telegram_bot_admins",
                value={"usernames": [clean_username]},
            )
            db.add(setting)
        else:
            current = list(setting.value.get("usernames", []))
            if clean_username not in [c.lower() for c in current]:
                current.append(clean_username)
                setting.value = {"usernames": current}

        # If chat already exists, grant admin channel status
        existing_chat = await db.scalar(
            select(TelegramChat).where(
                TelegramChat.username.ilike(clean_username),
                TelegramChat.chat_type == "private",
            )
        )
        if existing_chat:
            existing_chat.is_admin_channel = True

        await db.commit()
        return True, f"✅ <b>@{clean_username}</b> has been added as a MedFighter Bot Admin."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error adding bot admin: %s", exc)
        return False, f"⚠️ Database error: {exc}"


async def remove_bot_admin(db: AsyncSession, target: str) -> tuple[bool, str]:
    """Remove a Telegram username from bot administrators."""
    from app.modules.telegram.handlers import _ADMIN_USERNAMES

    clean_username = target.lstrip("@").strip().lower()
    if not clean_username:
        return False, "⚠️ Invalid username specified."

    if clean_username in _PROTECTED_ADMINS:
        return False, f"⛔ <b>@{clean_username}</b> is a primary system owner and cannot be removed."

    if clean_username in _ADMIN_USERNAMES:
        _ADMIN_USERNAMES.discard(clean_username)

    try:
        setting = await db.get(PlatformSetting, "telegram_bot_admins")
        if setting and setting.value:
            current = [u for u in setting.value.get("usernames", []) if u.lower() != clean_username]
            setting.value = {"usernames": current}

        existing_chat = await db.scalar(
            select(TelegramChat).where(
                TelegramChat.username.ilike(clean_username),
                TelegramChat.chat_type == "private",
            )
        )
        if existing_chat:
            existing_chat.is_admin_channel = False

        await db.commit()
        return True, f"✅ <b>@{clean_username}</b> has been removed from bot administrators."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error removing bot admin: %s", exc)
        return False, f"⚠️ Database error: {exc}"


async def list_bot_admins(db: AsyncSession) -> tuple[list[dict], list[dict]]:
    """Return registered admin users and groups."""
    from app.modules.telegram.handlers import _ADMIN_USERNAMES

    persisted = await _load_persisted_admins(db)
    all_admin_usernames = set(_ADMIN_USERNAMES).union(_PROTECTED_ADMINS).union(persisted)

    chats = (await db.scalars(select(TelegramChat))).all()

    admins: list[dict] = []
    groups: list[dict] = []
    seen = set()

    for c in chats:
        is_user_admin = c.is_admin_channel or (c.username and c.username.lower() in all_admin_usernames)
        if c.chat_type == "private" and is_user_admin:
            admins.append({
                "chat_id": c.chat_id,
                "username": f"@{c.username}" if c.username else "Private DM",
                "title": c.title or "Doctor",
                "is_active": c.is_admin_channel,
            })
            if c.username:
                seen.add(c.username.lower())
        elif c.chat_type in ("group", "supergroup"):
            groups.append({
                "chat_id": c.chat_id,
                "title": c.title or f"Group {c.chat_id}",
                "alerts_enabled": c.is_admin_channel,
                "type": c.chat_type,
            })

    for u in all_admin_usernames:
        if u not in seen:
            admins.append({
                "chat_id": None,
                "username": f"@{u}",
                "title": "Invited (Pending /start)",
                "is_active": False,
            })

    return admins, groups


async def set_group_alerts(db: AsyncSession, chat_id: int, enabled: bool) -> tuple[bool, str]:
    """Enable or disable payment alerts in a group or supergroup."""
    chat = await db.scalar(
        select(TelegramChat).where(TelegramChat.chat_id == chat_id)
    )
    if not chat or chat.chat_type not in ("group", "supergroup"):
        return False, "⚠️ This command can only be used inside a Telegram group or supergroup."

    chat.is_admin_channel = enabled
    await db.commit()
    status_label = "ENABLED ✅\nPayment receipts and alerts will be posted here." if enabled else "DISABLED / MUTED 🔕\nPayment receipts will no longer be posted to this group."
    return True, f"🔔 Group Payment Alerts: <b>{status_label}</b>"
