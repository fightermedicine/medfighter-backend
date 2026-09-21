"""Email delivery service.

Sends transactional emails (OTP verification, password reset, etc.) via
async SMTP using aiosmtplib.

Configuration (all via .env with FIGHTERS_ prefix):
  FIGHTERS_SMTP_HOST       default: smtp.supabase.io
  FIGHTERS_SMTP_PORT       default: 465
  FIGHTERS_SMTP_USER       e.g. your Supabase project ref
  FIGHTERS_SMTP_PASSWORD   e.g. your Supabase service role key
  FIGHTERS_SMTP_FROM_EMAIL default: noreply@medfighter.app
  FIGHTERS_SMTP_FROM_NAME  default: MedFighter
  FIGHTERS_SMTP_USE_TLS    default: true

If SMTP credentials are not set, emails are logged to stdout (dev/testing mode).
"""

from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ── HTML email templates ───────────────────────────────────────────────────────

_OTP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Verify your MedFighter account</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
    <tr>
      <td align="center">
        <table width="480" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(0,0,0,0.08);">
          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#0f172a 0%,#1e40af 100%);
                       padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;
                         letter-spacing:-0.5px;">🩺 MedFighter</h1>
              <p style="margin:8px 0 0;color:rgba(255,255,255,0.7);font-size:14px;">
                Medical Education Platform
              </p>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:40px;">
              <h2 style="margin:0 0 8px;color:#0f172a;font-size:20px;font-weight:600;">
                Verify your email address
              </h2>
              <p style="margin:0 0 24px;color:#64748b;font-size:15px;line-height:1.6;">
                Hello <strong>{full_name}</strong>,<br/>
                Use the 6-digit code below to verify your MedFighter account.
                This code expires in <strong>15 minutes</strong>.
              </p>
              <!-- OTP Code -->
              <div style="text-align:center;margin:0 0 32px;">
                <div style="display:inline-block;background:#f8fafc;
                            border:2px solid #e2e8f0;border-radius:12px;
                            padding:20px 40px;">
                  <span style="font-size:40px;font-weight:800;letter-spacing:10px;
                               color:#1e40af;font-family:'Courier New',monospace;">
                    {code}
                  </span>
                </div>
              </div>
              <p style="margin:0 0 8px;color:#94a3b8;font-size:13px;text-align:center;">
                If you did not create a MedFighter account, you can safely ignore this email.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#f8fafc;padding:20px 40px;border-top:1px solid #e2e8f0;">
              <p style="margin:0;color:#94a3b8;font-size:12px;text-align:center;">
                © 2026 MedFighter · Medical Education Platform<br/>
                This is an automated message. Please do not reply.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

_OTP_TEXT = """\
MedFighter — Email Verification

Hello {full_name},

Your verification code is: {code}

This code expires in 15 minutes.

If you did not create a MedFighter account, ignore this email.

— MedFighter Team
"""


# ── Core send function ─────────────────────────────────────────────────────────

async def _send_email(to_email: str, subject: str, html: str, text: str) -> None:
    """Send an email via SMTP. Falls back to console logging if not configured."""
    settings = get_settings()

    if not settings.email_enabled:
        # Dev/test mode: just log the email content
        logger.warning(
            "📧 [EMAIL NOT SENT — SMTP not configured]\n"
            "To: %s\nSubject: %s\nBody:\n%s",
            to_email, subject, text,
        )
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            timeout=15,
        )
        logger.info("📧 Email sent to %s: %s", to_email, subject)
    except Exception as exc:
        # Log the error but never crash the registration flow
        logger.error("📧 Failed to send email to %s: %s", to_email, exc)
        raise


# ── Public helpers ─────────────────────────────────────────────────────────────

async def send_verification_otp(
    to_email: str,
    full_name: str,
    code: str,
) -> None:
    """Send the 6-digit OTP to verify a new account."""
    subject = "Your MedFighter verification code"
    html = _OTP_HTML.format(full_name=full_name, code=code)
    text = _OTP_TEXT.format(full_name=full_name, code=code)
    await _send_email(to_email, subject, html, text)


async def send_resend_otp(
    to_email: str,
    full_name: str,
    code: str,
) -> None:
    """Resend the 6-digit OTP (same template, slightly different subject)."""
    subject = "Your new MedFighter verification code"
    html = _OTP_HTML.format(full_name=full_name, code=code)
    text = _OTP_TEXT.format(full_name=full_name, code=code)
    await _send_email(to_email, subject, html, text)
