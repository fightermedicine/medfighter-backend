"""Telegram update handlers — processes incoming messages from students and admin groups.

Dispatch tree:
  /start (group)       → activate admin channel
  /start (private)     → welcome student / link instructions
  email reply (priv)   → link student account
  photo (private)      → create payment submission, notify admin groups with [Approve/Decline/Request Proof]
  admin native reply   → admin in group replies to forwarded student message → delivered directly to student
  free text (private)  → natively forwarded to all admin groups without canned auto-replies
  /check               → reply to student message to view full profile & payment history
  /status <REF>        → return payment verification status
  /admins              → list registered admin groups and users
  /help                → usage info
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.modules.telegram.service as tg
from app.modules.catalog.models import Product
from app.modules.identity.models import User, UserRole
from app.modules.payments.models import Payment
from app.modules.payments.service import (
    _PROOF_DIR,
    _generate_reference,
    _utc_now,
    approve_payment,
    reject_payment,
    request_new_proof,
)
from app.modules.telegram.models import TelegramChat
from app.modules.wallet.service import (
    admin_manual_user_topup,
    calculate_wallet_balance,
    get_or_create_wallet,
)

logger = logging.getLogger(__name__)

# Admin usernames from settings (lowercase without @).
_ADMIN_USERNAMES: set[str] = set()

# Pending wallet top-up state: chat_id (group or private) → user_id (str)
_PENDING_TOPUP: dict[int, str] = {}

# Pending reply state: admin_chat_id → student_chat_id
_PENDING_REPLY: dict[int, int] = {}

# Relay map: (group_chat_id, group_message_id) → student_chat_id
# Allows any admin in any group to swipe/reply to a forwarded student message.
_FORWARD_MAP: dict[tuple[int, int], int] = {}

_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+\.[a-z]{2,}$", re.IGNORECASE)


def configure_admin_usernames(*usernames: str) -> None:
    """Called once at startup with admin usernames from contact_info settings."""
    for u in usernames:
        _ADMIN_USERNAMES.add(u.lstrip("@").lower())


def _remember_forward(group_chat_id: int, group_msg_id: int, student_chat_id: int) -> None:
    """Store mapping from group message to original student chat."""
    if len(_FORWARD_MAP) > 10000:
        for _ in range(2000):
            _FORWARD_MAP.pop(next(iter(_FORWARD_MAP)), None)
    _FORWARD_MAP[(group_chat_id, group_msg_id)] = student_chat_id


def _extract_message(update: dict) -> dict | None:
    return update.get("message") or update.get("channel_post")


def _is_admin_username(username: str | None) -> bool:
    if not username:
        return False
    return username.lower() in _ADMIN_USERNAMES


# ── Handlers ─────────────────────────────────────────────────────────────────


async def handle_start_private(
    db: AsyncSession,
    chat: TelegramChat,
    username: str | None,
) -> None:
    """Handle /start in a private chat (students and individual admins)."""
    if _is_admin_username(username):
        await tg.mark_admin_channel(db, chat.chat_id)
        await tg.send_message(
            chat.chat_id,
            "<b>Admin channel registered.</b>\n\n"
            "Admin controls and payment alerts are managed in the admin group.\n"
            "Add the bot to your group and send /start there to receive notifications.\n\n"
            "/status &lt;REF&gt; — Payment status\n"
            "/admins — List admin roster\n"
            "/help — Show commands",
        )
        return

    if chat.linked_user_id:
        await tg.send_message(
            chat.chat_id,
            "<b>MedFighter Support</b>\n\n"
            "Account linked.\n"
            "Send a Vodafone Cash transfer screenshot to submit proof of payment.\n"
            "/status &lt;REF&gt; — Check payment status",
        )
    else:
        await tg.send_message(
            chat.chat_id,
            "<b>MedFighter Support</b>\n\n"
            "Send your registered email address to link your account.\n"
            "You can also type your message directly to contact support.",
        )


async def handle_start_group(db: AsyncSession, chat: TelegramChat) -> None:
    """Handle /start in a group or supergroup."""
    await tg.mark_admin_channel(db, chat.chat_id)
    await tg.send_message(
        chat.chat_id,
        "<b>MedFighter Admin Channel activated</b>\n\n"
        "This group is now registered. Payment alerts and student messages will appear here.\n"
        "Any member in this group can approve payments and reply directly to student messages.",
    )
    logger.info("Group admin channel registered: chat_id=%s title=%s", chat.chat_id, chat.title)


async def handle_email_reply(
    db: AsyncSession,
    chat: TelegramChat,
    email: str,
) -> None:
    """Attempt to link a Telegram private chat to a MedFighter account by email."""
    user = await db.scalar(
        select(User).where(User.email == email.strip().lower(), User.is_active.is_(True))
    )
    if not user:
        await tg.send_message(
            chat.chat_id,
            "No active MedFighter account found for that email.\n"
            "Please verify your email address or create an account in the app.",
        )
        return

    await tg.link_user(db, chat.chat_id, user.id)
    name = user.full_name or "Student"

    user_role_ids = {r.role_id for r in getattr(user, "roles", [])}
    if user_role_ids.intersection({"ADMIN", "SUPER_ADMIN"}):
        await tg.send_message(
            chat.chat_id,
            f"Account linked. Welcome, <b>{name}</b>.\n\n"
            "To receive payment alerts, ensure the bot is added to your admin group.",
        )
        return

    await tg.send_message(
        chat.chat_id,
        f"Account linked successfully. Welcome, <b>{name}</b>.\n\n"
        "You can now send a Vodafone Cash receipt screenshot to submit proof of payment.",
    )
    logger.info("Account linked: user_id=%s chat_id=%s", user.id, chat.chat_id)


async def handle_photo(
    db: AsyncSession,
    chat: TelegramChat,
    message: dict,
    file_bytes: bytes,
    file_id: str | None = None,
) -> None:
    """Handle a receipt photo from a linked student — create or update pending payment record."""
    if not chat.linked_user_id:
        await tg.send_message(
            chat.chat_id,
            "Please link your account first by sending your registered email address.",
        )
        return

    caption = (message.get("caption") or "").strip()

    # 1. Match payment by caption reference if specified
    existing: Payment | None = None
    if caption:
        ref_match = re.search(r"FIGHTER-[A-Z0-9]{6}", caption.upper())
        if ref_match:
            existing = await db.scalar(
                select(Payment).where(
                    Payment.reference == ref_match.group(0),
                    Payment.status.in_(["CREATED", "UNDER_REVIEW", "MORE_PROOF_REQUIRED"]),
                )
            )

    # 2. Look for existing in-flight payment for this user
    if not existing:
        existing = await db.scalar(
            select(Payment)
            .where(
                Payment.user_id == chat.linked_user_id,
                Payment.status.in_(["CREATED", "UNDER_REVIEW", "MORE_PROOF_REQUIRED"]),
            )
            .order_by(Payment.created_at.desc())
        )

    # 3. If still none, auto-create a payment for the active product of their medical year
    if existing:
        proof_hash = hashlib.sha256(file_bytes).hexdigest()
        _PROOF_DIR.mkdir(parents=True, exist_ok=True)
        proof_path = _PROOF_DIR / f"{existing.id}.jpg"
        proof_path.write_bytes(file_bytes)
        existing.proof_path = str(proof_path)
        existing.proof_hash = proof_hash
        existing.proof_content_type = "image/jpeg"
        existing.status = "UNDER_REVIEW"
        existing.submitted_at = _utc_now()
        await db.commit()
        ref = existing.reference
    else:
        user = await db.get(User, chat.linked_user_id)
        med_year = getattr(user, "medical_year", 1) or 1

        product = await db.scalar(
            select(Product).where(Product.is_active.is_(True), Product.medical_year == med_year).limit(1)
        )
        if not product:
            product = await db.scalar(select(Product).where(Product.is_active.is_(True)).limit(1))

        if not product:
            await tg.send_message(
                chat.chat_id,
                "No active products available in catalog. Please try again later.",
            )
            return

        ref = _generate_reference()
        payment_id = uuid.uuid4()
        proof_hash = hashlib.sha256(file_bytes).hexdigest()
        _PROOF_DIR.mkdir(parents=True, exist_ok=True)
        proof_path = _PROOF_DIR / f"{payment_id}.jpg"
        proof_path.write_bytes(file_bytes)

        existing = Payment(
            id=payment_id,
            user_id=chat.linked_user_id,
            product_id=product.id,
            reference=ref,
            amount_piastres=product.price_piastres,
            status="UNDER_REVIEW",
            proof_path=str(proof_path),
            proof_hash=proof_hash,
            proof_content_type="image/jpeg",
            submitted_at=_utc_now(),
        )
        db.add(existing)
        await db.commit()

    # Notify student
    await tg.send_message(
        chat.chat_id,
        f"Receipt received. Reference: <code>{ref}</code>\n"
        f"Status: Under Review\n\n"
        "You will be notified once payment is verified.",
    )

    # Broadcast to admin groups
    user = await db.get(User, chat.linked_user_id)
    user_name = user.full_name if user else "Unknown"
    user_email = user.email if user else ""

    product = await db.get(Product, existing.product_id)
    product_title = product.title if product else "Product"
    amount_egp = existing.amount_piastres / 100

    admin_caption = (
        f"<b>Payment Submission</b>\n\n"
        f"Student: <b>{user_name}</b> ({user_email})\n"
        f"Product: {product_title}\n"
        f"Amount: <b>{amount_egp:.0f} EGP</b>\n"
        f"Reference: <code>{ref}</code>"
    )

    keyboard = tg.build_payment_keyboard(ref)
    photo_payload: bytes | str = file_id if file_id else file_bytes
    await tg.broadcast_photo_to_admins(
        db,
        photo=photo_payload,
        caption=admin_caption,
        reply_markup=keyboard,
    )


async def handle_status(db: AsyncSession, chat_id: int, reference: str) -> None:
    """Handle /status <REF> — return current payment status."""
    ref = reference.strip().upper()
    payment = await db.scalar(select(Payment).where(Payment.reference == ref))
    if not payment:
        await tg.send_message(chat_id, f"No payment found with reference <code>{ref}</code>.")
        return

    reason_part = f"\nReason: {payment.rejection_reason}" if payment.rejection_reason else ""
    await tg.send_message(
        chat_id,
        f"<b>Payment Status</b>\n\n"
        f"Reference: <code>{ref}</code>\n"
        f"Status: <b>{payment.status.replace('_', ' ').title()}</b>"
        f"{reason_part}",
    )


async def handle_help(chat_id: int, is_admin: bool = False) -> None:
    """Handle /help."""
    if is_admin:
        msg = (
            "<b>MedFighter Admin Commands</b>\n\n"
            "/check — Reply to a student message to view full profile\n"
            "/status &lt;REF&gt; — Check payment verification status\n"
            "/admins — List connected admin groups &amp; users\n"
            "/add_admin &lt;@username&gt; — Add a bot administrator\n"
            "/remove_admin &lt;@username&gt; — Remove a bot administrator\n"
            "/enable_alerts — Enable payment alerts in this group\n"
            "/disable_alerts — Mute payment alerts in this group\n"
            "/group_status — Show group alert status\n"
            "/reply &lt;chat_id&gt; &lt;msg&gt; — Manual fallback reply to student\n\n"
            "<i>To reply to a student, simply reply to their forwarded message directly.</i>"
        )
    else:
        msg = (
            "<b>MedFighter Support Commands</b>\n\n"
            "/status &lt;REF&gt; — Check payment verification status\n"
            "/help — Show this menu\n\n"
            "Send your registered email to link your account.\n"
            "Send a Vodafone Cash transfer screenshot to submit proof of payment."
        )
    await tg.send_message(chat_id, msg)


async def handle_add_admin(
    db: AsyncSession,
    chat_id: int,
    sender_id: int,
    sender_username: str | None,
    target: str,
    chat_type: str | None = None,
) -> None:
    """Handle /add_admin @username."""
    if not await tg.is_admin(db, sender_id, sender_username, chat_type=chat_type):
        await tg.send_message(chat_id, "Permission Denied. Only bot administrators can add new admins.")
        return

    if not target:
        await tg.send_message(chat_id, "Usage: <code>/add_admin @username</code>")
        return

    success, msg = await tg.add_bot_admin(db, target)
    await tg.send_message(chat_id, msg)


async def handle_remove_admin(
    db: AsyncSession,
    chat_id: int,
    sender_id: int,
    sender_username: str | None,
    target: str,
    chat_type: str | None = None,
) -> None:
    """Handle /remove_admin @username."""
    if not await tg.is_admin(db, sender_id, sender_username, chat_type=chat_type):
        await tg.send_message(chat_id, "Permission Denied. Only bot administrators can remove admins.")
        return

    if not target:
        await tg.send_message(chat_id, "Usage: <code>/remove_admin @username</code>")
        return

    success, msg = await tg.remove_bot_admin(db, target)
    await tg.send_message(chat_id, msg)


async def handle_list_admins(
    db: AsyncSession,
    chat_id: int,
    sender_id: int,
    sender_username: str | None,
    chat_type: str | None = None,
) -> None:
    """Handle /admins or /list_admins."""
    if not await tg.is_admin(db, sender_id, sender_username, chat_type=chat_type):
        await tg.send_message(chat_id, "Permission Denied.")
        return

    admins, groups = await tg.list_bot_admins(db)

    admin_lines = [f"• <b>{a['username']}</b> — {a['title']}" for a in admins]
    group_lines = [
        f"• <b>{g['title']}</b> (<code>{g['chat_id']}</code>) — {'Alerts Active' if g['alerts_enabled'] else 'Muted'}"
        for g in groups
    ]

    resp = "<b>MedFighter Administrators</b>\n\n"
    resp += "\n".join(admin_lines) if admin_lines else "No individual admin users registered."
    resp += "\n\n<b>Connected Admin Groups</b>\n\n"
    resp += "\n".join(group_lines) if group_lines else "No groups connected."

    await tg.send_message(chat_id, resp)


async def handle_group_alerts(
    db: AsyncSession,
    chat_id: int,
    sender_id: int,
    sender_username: str | None,
    enabled: bool,
    chat_type: str | None = None,
) -> None:
    """Handle /enable_alerts and /disable_alerts."""
    if not await tg.is_admin(db, sender_id, sender_username, chat_type=chat_type):
        await tg.send_message(chat_id, "Permission Denied.")
        return

    success, msg = await tg.set_group_alerts(db, chat_id, enabled)
    await tg.send_message(chat_id, msg)


async def handle_group_status(
    db: AsyncSession,
    chat_id: int,
    chat: TelegramChat,
) -> None:
    """Handle /group_status."""
    if chat.chat_type not in ("group", "supergroup"):
        await tg.send_message(chat_id, "This command is only available inside Telegram groups.")
        return

    status_str = "Active" if chat.is_admin_channel else "Muted"
    msg = (
        f"<b>Group Status</b>\n\n"
        f"Title: <b>{chat.title or 'Group'}</b>\n"
        f"Chat ID: <code>{chat.chat_id}</code>\n"
        f"Alerts: <b>{status_str}</b>\n\n"
        "/enable_alerts — Turn on payment alerts\n"
        "/disable_alerts — Mute payment alerts"
    )
    await tg.send_message(chat_id, msg)


async def handle_check(
    db: AsyncSession,
    chat_id: int,
    sender_id: int,
    sender_username: str | None,
    message: dict,
    chat_type: str | None = None,
) -> None:
    """Handle /check — admin replies to a student's message to fetch full database profile."""
    if not await tg.is_admin(db, sender_id, sender_username, chat_type=chat_type):
        await tg.send_message(chat_id, "Permission Denied. Only admins can use /check.")
        return

    replied = message.get("reply_to_message")
    if not replied:
        await tg.send_message(chat_id, "Please reply to a student message and type <code>/check</code>.")
        return

    replied_msg_id = replied.get("message_id")
    student_tg_id: int | None = None

    # Check relay map
    if (chat_id, replied_msg_id) in _FORWARD_MAP:
        student_tg_id = _FORWARD_MAP[(chat_id, replied_msg_id)]

    # Check forward_from
    if not student_tg_id:
        student_tg_id = (replied.get("forward_from") or {}).get("id")

    # Check forward_origin
    if not student_tg_id:
        student_tg_id = ((replied.get("forward_origin") or {}).get("sender_user") or {}).get("id")

    if not student_tg_id:
        await tg.send_message(chat_id, "Could not determine student identity from this message.")
        return

    student_chat = await db.scalar(
        select(TelegramChat).where(TelegramChat.chat_id == student_tg_id)
    )
    if not student_chat or not student_chat.linked_user_id:
        await tg.send_message(
            chat_id,
            f"No linked MedFighter account found for Telegram ID <code>{student_tg_id}</code>.",
        )
        return

    user = await db.get(User, student_chat.linked_user_id)
    if not user:
        await tg.send_message(chat_id, "User record not found in database.")
        return

    # Fetch last 5 payments
    payments = (await db.scalars(
        select(Payment)
        .where(Payment.user_id == user.id)
        .order_by(Payment.created_at.desc())
        .limit(5)
    )).all()

    # Calculate wallet balance
    wallet = await get_or_create_wallet(db, user.id)
    balance_piastres = await calculate_wallet_balance(db, wallet.id)
    balance_egp = balance_piastres / 100

    role_ids = {r.role_id for r in getattr(user, "roles", [])}
    role_str = ", ".join(role_ids) if role_ids else "Student"

    year_labels = {1: "1st Year", 2: "2nd Year", 3: "3rd Year", 4: "4th Year", 5: "5th Year"}
    med_year = getattr(user, "medical_year", None)
    year_en = year_labels.get(med_year, str(med_year)) if med_year else "—"

    pay_lines = [
        f"• <code>{p.reference}</code> — {p.amount_piastres / 100:.0f} EGP — {p.status.replace('_', ' ').title()}"
        for p in payments
    ]
    pay_block = "\n".join(pay_lines) if pay_lines else "  None"

    tg_link = f"@{student_chat.username}" if student_chat.username else f"<code>{student_tg_id}</code>"

    report = (
        f"<b>Student Profile</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Name: <b>{user.full_name or '—'}</b>\n"
        f"Email: <code>{user.email or '—'}</code>\n"
        f"Phone: <code>{getattr(user, 'phone', None) or '—'}</code>\n"
        f"Year: <b>{year_en}</b>\n"
        f"Role: <b>{role_str}</b>\n"
        f"Telegram: {tg_link}\n"
        f"User ID: <code>{user.id}</code>\n\n"
        f"Wallet Balance: <b>{balance_egp:.0f} EGP</b>\n\n"
        f"Recent Payments:\n"
        f"{pay_block}"
    )
    await tg.send_message(chat_id, report)


async def handle_admin_reply(
    db: AsyncSession,
    admin_chat_id: int,
    admin_username: str | None,
    target_chat_id: int,
    reply_text: str,
) -> None:
    """Send admin reply back to student — pure text, no bot prefix, no group echo."""
    if not reply_text.strip():
        return
    success = await tg.send_message(target_chat_id, reply_text, parse_mode="HTML")
    if not success:
        logger.warning(
            "Admin reply delivery failed: admin=%s target_chat_id=%s",
            admin_username, target_chat_id,
        )


async def handle_callback_query(
    db: AsyncSession,
    callback: dict,
    sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    """Handle inline button actions (Approve, Decline, Request Proof)."""
    callback_id: str = callback["id"]
    data: str = callback.get("data", "")
    from_user: dict = callback.get("from", {})
    admin_chat_id: int = from_user.get("id", 0)
    admin_username: str | None = from_user.get("username")

    message: dict = callback.get("message", {})
    msg_chat: dict = message.get("chat", {})
    msg_chat_id: int = msg_chat.get("id", admin_chat_id)
    msg_chat_type: str = msg_chat.get("type", "group")
    msg_id: int = message.get("message_id", 0)
    original_caption: str = message.get("caption", "")

    # Group actions = authorized
    if not await tg.is_admin(db, admin_chat_id, admin_username, chat_type=msg_chat_type):
        await tg.answer_callback_query(callback_id, text="Permission denied.", show_alert=True)
        return

    if ":" not in data:
        await tg.answer_callback_query(callback_id, text="Unknown action.", show_alert=True)
        return

    action, reference = data.split(":", 1)
    reference = reference.strip().upper()

    payment = await db.scalar(select(Payment).where(Payment.reference == reference))
    if not payment:
        await tg.answer_callback_query(callback_id, text=f"Payment {reference} not found.", show_alert=True)
        return

    # Resolve platform admin user_id for audit logging
    admin_chat_row = await db.scalar(
        select(TelegramChat).where(TelegramChat.chat_id == admin_chat_id)
    )
    admin_user_id: uuid.UUID | None = getattr(admin_chat_row, "linked_user_id", None) if admin_chat_row else None
    if admin_user_id is None:
        first_admin = await db.scalar(
            select(UserRole).where(UserRole.role_id.in_(["SUPER_ADMIN", "ADMIN"])).limit(1)
        )
        admin_user_id = first_admin.user_id if first_admin else uuid.UUID("00000000-0000-0000-0000-000000000000")

    # ── Approve ──────────────────────────────────────────────────────────────
    if action == "pay_app":
        if payment.status == "APPROVED":
            await tg.answer_callback_query(callback_id, text="Already approved.")
            return
        try:
            await approve_payment(db, payment.id, admin_user_id)
        except Exception as exc:
            await tg.answer_callback_query(callback_id, text=f"Error: {exc}", show_alert=True)
            return

        await tg.answer_callback_query(callback_id, text="Payment Approved.")

        new_caption = (
            f"{original_caption}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Status: Approved by @{admin_username or 'admin'}"
        )
        await tg.edit_message_caption(msg_chat_id, msg_id, new_caption, reply_markup={"inline_keyboard": []})

        await tg.notify_student(
            db,
            payment.user_id,
            f"Payment <code>{reference}</code> has been approved. Your content is now available.",
        )

        # Set pending topup on the group chat so any member can enter top-up amount
        _PENDING_TOPUP[msg_chat_id] = str(payment.user_id)
        await tg.send_message(
            msg_chat_id,
            f"Payment <code>{reference}</code> approved.\n"
            f"To credit the student's wallet balance, enter amount in EGP or /skip.",
        )

    # ── Decline ──────────────────────────────────────────────────────────────
    elif action == "pay_dec":
        if payment.status in ("REJECTED", "EXPIRED", "APPROVED"):
            await tg.answer_callback_query(callback_id, text=f"Payment is already {payment.status}.", show_alert=True)
            return
        try:
            await reject_payment(db, payment.id, admin_user_id, reason="Declined via Telegram.")
        except Exception as exc:
            await tg.answer_callback_query(callback_id, text=f"Error: {exc}", show_alert=True)
            return

        await tg.answer_callback_query(callback_id, text="Payment Declined.")

        new_caption = (
            f"{original_caption}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Status: Declined by @{admin_username or 'admin'}"
        )
        await tg.edit_message_caption(msg_chat_id, msg_id, new_caption, reply_markup={"inline_keyboard": []})

        await tg.notify_student(
            db,
            payment.user_id,
            f"Payment <code>{reference}</code> was declined. Please check your payment details or contact support.",
        )

    # ── Request Proof ─────────────────────────────────────────────────────────
    elif action == "pay_req":
        if payment.status != "UNDER_REVIEW":
            await tg.answer_callback_query(callback_id, text=f"Payment is in {payment.status} state.", show_alert=True)
            return
        try:
            await request_new_proof(db, payment.id, admin_user_id)
        except Exception as exc:
            await tg.answer_callback_query(callback_id, text=f"Error: {exc}", show_alert=True)
            return

        await tg.answer_callback_query(callback_id, text="Requested new proof.")

        new_caption = (
            f"{original_caption}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Status: New proof requested by @{admin_username or 'admin'}"
        )
        await tg.edit_message_caption(msg_chat_id, msg_id, new_caption, reply_markup={"inline_keyboard": []})

        await tg.notify_student(
            db,
            payment.user_id,
            f"Payment <code>{reference}</code>: the receipt could not be verified. Please submit a clearer transfer screenshot.",
        )

    else:
        await tg.answer_callback_query(callback_id, text="Unknown action.", show_alert=True)


# ── Dispatcher ────────────────────────────────────────────────────────────────


async def dispatch(update: dict, sessionmaker: async_sessionmaker) -> None:  # type: ignore[type-arg]
    """Route incoming Telegram updates to appropriate handlers."""
    # ── 1. Inline callback queries ──────────────────────────────────────────
    callback = update.get("callback_query")
    if callback:
        async with sessionmaker() as db:
            await handle_callback_query(db, callback, sessionmaker)
        return

    message = _extract_message(update)
    if not message:
        return

    raw_chat = message.get("chat", {})
    chat_id: int = raw_chat.get("id", 0)
    chat_type: str = raw_chat.get("type", "private")
    from_user = message.get("from", {})
    sender_id: int = from_user.get("id", chat_id)
    username: str | None = from_user.get("username")
    text: str = (message.get("text") or "").strip()

    async with sessionmaker() as db:
        # Ensure chat record exists
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        title = raw_chat.get("title") or f"{first_name} {last_name}".strip() or None

        chat: TelegramChat = await tg.get_or_create_chat(
            db,
            chat_id=chat_id,
            chat_type=chat_type,
            title=title,
            username=username,
        )

        # Groups/supergroups default to admin alert channels
        if chat_type in ("group", "supergroup") and not chat.is_admin_channel:
            await tg.mark_admin_channel(db, chat.chat_id)
            chat.is_admin_channel = True

        # Parse command and args
        parts = text.split(maxsplit=1) if text else []
        cmd_raw = parts[0] if parts else ""
        cmd = cmd_raw.split("@")[0].lower() if cmd_raw.startswith("/") else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if not arg and message.get("reply_to_message"):
            reply_user = message["reply_to_message"].get("from", {})
            arg = reply_user.get("username") or ""
            if arg and not arg.startswith("@"):
                arg = f"@{arg}"

        # ── Commands ──────────────────────────────────────────────────────────
        if cmd == "/start":
            if chat_type == "private":
                await handle_start_private(db, chat, username)
            else:
                await handle_start_group(db, chat)
            return

        if cmd == "/help":
            is_adm = await tg.is_admin(db, sender_id, username, chat_type=chat_type)
            await handle_help(chat_id, is_admin=is_adm)
            return

        if cmd == "/status":
            if arg:
                await handle_status(db, chat_id, arg)
            else:
                await tg.send_message(chat_id, "Usage: /status FIGHTER-XXXXXX")
            return

        if cmd in ("/admins", "/list_admins"):
            await handle_list_admins(db, chat_id, sender_id, username, chat_type=chat_type)
            return

        if cmd == "/add_admin":
            await handle_add_admin(db, chat_id, sender_id, username, arg, chat_type=chat_type)
            return

        if cmd == "/remove_admin":
            await handle_remove_admin(db, chat_id, sender_id, username, arg, chat_type=chat_type)
            return

        if cmd in ("/enable_alerts",) or (cmd == "/alerts" and arg.lower() in ("on", "enable", "true")):
            await handle_group_alerts(db, chat_id, sender_id, username, enabled=True, chat_type=chat_type)
            return

        if cmd in ("/disable_alerts",) or (cmd == "/alerts" and arg.lower() in ("off", "disable", "mute", "false")):
            await handle_group_alerts(db, chat_id, sender_id, username, enabled=False, chat_type=chat_type)
            return

        if cmd in ("/group_status", "/group_info"):
            await handle_group_status(db, chat_id, chat)
            return

        if cmd == "/check":
            await handle_check(db, chat_id, sender_id, username, message, chat_type=chat_type)
            return

        if cmd == "/reply":
            if not await tg.is_admin(db, sender_id, username, chat_type=chat_type):
                await tg.send_message(chat_id, "Permission Denied.")
                return
            reply_parts = arg.split(maxsplit=1)
            if len(reply_parts) < 2:
                await tg.send_message(chat_id, "Usage: /reply &lt;chat_id&gt; &lt;message&gt;")
                return
            try:
                target_chat_id = int(reply_parts[0])
            except ValueError:
                await tg.send_message(chat_id, "Invalid chat_id. Must be numeric.")
                return
            await handle_admin_reply(db, chat_id, username, target_chat_id, reply_parts[1].strip())
            return

        if cmd == "/skip":
            topup_key = chat_id if chat_id in _PENDING_TOPUP else sender_id
            if topup_key in _PENDING_TOPUP:
                _PENDING_TOPUP.pop(topup_key, None)
                await tg.send_message(chat_id, "Wallet top-up skipped.")
            return

        # ── 2. Admin Native Reply Routing (Works in Groups & DMs) ────────────
        # Admin swipes/replies to a forwarded student message in the group
        if message.get("reply_to_message") and text and not cmd:
            is_adm = await tg.is_admin(db, sender_id, username, chat_type=chat_type)
            if is_adm:
                replied = message["reply_to_message"]
                replied_msg_id = replied.get("message_id")
                target_id: int | None = None

                # 1. Primary: Look up in relay map (100% reliable)
                if (chat_id, replied_msg_id) in _FORWARD_MAP:
                    target_id = _FORWARD_MAP[(chat_id, replied_msg_id)]

                # 2. Secondary: forward_from
                if not target_id:
                    target_id = (replied.get("forward_from") or {}).get("id")

                # 3. Tertiary: forward_origin
                if not target_id:
                    target_id = ((replied.get("forward_origin") or {}).get("sender_user") or {}).get("id")

                if target_id and target_id != sender_id:
                    await handle_admin_reply(db, chat_id, username, target_id, text)
                    return

        # ── 3. Wallet Top-Up Input Check ──────────────────────────────────────
        topup_target_key = chat_id if chat_id in _PENDING_TOPUP else (sender_id if sender_id in _PENDING_TOPUP else None)
        if topup_target_key is not None and text and not cmd:
            amount_str = text.strip().replace(",", ".")
            try:
                amount_egp = float(amount_str)
                if amount_egp <= 0:
                    raise ValueError("non-positive")
            except ValueError:
                amount_egp = 0

            if amount_egp > 0:
                target_user_id_str = _PENDING_TOPUP.pop(topup_target_key)
                # Resolve admin user
                admin_user_id = None
                admin_row = await db.scalar(select(TelegramChat).where(TelegramChat.chat_id == sender_id))
                if admin_row:
                    admin_user_id = admin_row.linked_user_id
                if not admin_user_id:
                    first_adm = await db.scalar(
                        select(UserRole).where(UserRole.role_id.in_(["SUPER_ADMIN", "ADMIN"])).limit(1)
                    )
                    admin_user_id = first_adm.user_id if first_adm else None

                try:
                    result = await admin_manual_user_topup(
                        db,
                        admin_id=admin_user_id,
                        user_identifier=target_user_id_str,
                        amount_egp=amount_egp,
                        note="Manual top-up via Telegram bot",
                    )
                    await tg.send_message(
                        chat_id,
                        f"Wallet credited: <b>{amount_egp:.0f} EGP</b>.\n"
                        f"Student: <b>{result.email}</b>\n"
                        f"New Balance: <b>{result.new_balance_egp:.0f} EGP</b>",
                    )
                    # Notify student
                    await tg.notify_student(
                        db,
                        uuid.UUID(target_user_id_str),
                        f"Your wallet has been credited with <b>{amount_egp:.0f} EGP</b>.",
                    )
                except Exception as exc:
                    await tg.send_message(chat_id, f"Top-up failed: {exc}")
                return

        # ── 4. Student Private Chat Only ──────────────────────────────────────
        if chat_type != "private":
            return

        # Receipt photo submission
        if message.get("photo") and tg._client:  # noqa: SLF001
            largest = max(message["photo"], key=lambda p: p.get("file_size", 0))
            file_id = largest["file_id"]
            try:
                path_resp = await tg._client.get(  # noqa: SLF001
                    f"{tg._API_BASE}/getFile",  # noqa: SLF001
                    params={"file_id": file_id},
                )
                path_data = path_resp.json()
                if path_data.get("ok"):
                    file_path = path_data["result"]["file_path"]
                    token = tg._BOT_TOKEN  # noqa: SLF001
                    dl_resp = await tg._client.get(  # noqa: SLF001
                        f"https://api.telegram.org/file/bot{token}/{file_path}"
                    )
                    file_bytes = dl_resp.content
                    await handle_photo(db, chat, message, file_bytes, file_id=file_id)
                    return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to download photo: %s", exc)
                await tg.send_message(chat_id, "Could not process your photo. Please try again.")
            return

        # Email linking
        if _EMAIL_RE.match(text) and not chat.linked_user_id:
            await handle_email_reply(db, chat, text)
            return

        # Free text from student → forward natively to all admin groups
        if text:
            msg_id = message.get("message_id", 0)
            forwarded_ids = await tg.broadcast_forward_to_admins(db, chat_id, msg_id)
            for g_chat_id, g_msg_id in forwarded_ids.items():
                _remember_forward(g_chat_id, g_msg_id, chat_id)
            return

        # Fallback for unrecognised input
        if not chat.linked_user_id:
            await tg.send_message(
                chat_id,
                "<b>MedFighter Support</b>\n\n"
                "Send your registered email address to link your account, or send your message directly.",
            )
        else:
            await tg.send_message(
                chat_id,
                "<b>MedFighter Support</b>\n\n"
                "Send a receipt screenshot to submit proof of payment, or send your message directly.",
            )
