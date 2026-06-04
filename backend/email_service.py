"""Email service via Resend."""
import os
import asyncio
import logging
import resend

logger = logging.getLogger(__name__)

resend.api_key = os.environ.get("RESEND_API_KEY", "")
SENDER_EMAIL = os.environ.get("FROM_EMAIL") or os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")
APP_NAME = os.environ.get("APP_NAME", "Scorelib")


def _send_resend_email(params: dict):
    """Call the current Resend client shape, with legacy SDK compatibility."""
    if hasattr(resend, "emails") and hasattr(resend.emails, "send"):
        return resend.emails.send(params)
    return resend.Emails.send(params)


async def send_password_reset_email(to_email: str, reset_link: str, user_name: str = "") -> bool:
    """Send password reset email. Returns True on success, False otherwise."""
    if not resend.api_key:
        logger.warning(f"[DEV MODE] No RESEND_API_KEY. Reset link for {to_email}: {reset_link}")
        return True

    greeting = f"Ciao {user_name}," if user_name else "Ciao,"
    html = f"""
    <table style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px;color:#0A0A0A;">
      <tr><td>
        <h1 style="font-size:24px;font-weight:800;margin:0 0 16px;letter-spacing:-0.02em;">{APP_NAME}</h1>
        <p style="font-size:16px;line-height:1.6;margin:0 0 16px;">{greeting}</p>
        <p style="font-size:16px;line-height:1.6;margin:0 0 24px;color:#525252;">
          Hai richiesto il reset della password. Clicca sul pulsante qui sotto per impostarne una nuova. Il link &egrave; valido per 60 minuti.
        </p>
        <a href="{reset_link}" style="display:inline-block;background:#0A0A0A;color:#FFFFFF;padding:14px 28px;text-decoration:none;font-weight:600;border-radius:4px;font-size:15px;">Reimposta password</a>
        <p style="font-size:13px;line-height:1.6;margin:32px 0 0;color:#A3A3A3;">
          Se non hai richiesto tu il reset, ignora questa email. Il link scadr&agrave; automaticamente.
        </p>
        <p style="font-size:13px;line-height:1.6;margin:16px 0 0;color:#A3A3A3;word-break:break-all;">
          Link diretto: {reset_link}
        </p>
      </td></tr>
    </table>
    """
    try:
        params = {
            "from": f"{APP_NAME} <{SENDER_EMAIL}>",
            "to": [to_email],
            "subject": f"Reimposta la tua password {APP_NAME}",
            "html": html,
        }
        result = await asyncio.to_thread(_send_resend_email, params)
        logger.info(f"Password reset email sent to {to_email}: {result.get('id') if isinstance(result, dict) else result}")
        return True
    except Exception as e:
        logger.error(f"Failed to send reset email to {to_email}: {e}")
        return False


async def send_access_notification(to_email: str, approved: bool, user_name: str = "") -> bool:
    """Send access request notification via Resend."""
    if not resend.api_key:
        logger.warning(f"[DEV MODE] No RESEND_API_KEY. Access {'approved' if approved else 'rejected'} for {to_email}")
        return True

    status_text = "approvata" if approved else "rifiutata"
    message = (
        "La tua richiesta &egrave; stata approvata! Ora puoi accedere all'account condiviso del gruppo."
        if approved else
        "La tua richiesta &egrave; stata purtroppo rifiutata. Per ulteriori informazioni contatta pianodrivehouse@gmail.com"
    )
    
    html = f"""
    <table style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px;color:#0A0A0A;">
      <tr><td>
        <h1 style="font-size:24px;font-weight:800;margin:0 0 16px;letter-spacing:-0.02em;">{APP_NAME}</h1>
        <p style="font-size:16px;line-height:1.6;margin:0 0 16px;">Ciao {user_name},</p>
        <p style="font-size:16px;line-height:1.6;margin:0 0 24px;color:#525252;">
          La tua richiesta di accesso a {APP_NAME} &egrave; stata <strong>{status_text}</strong>.
        </p>
        <p style="font-size:16px;line-height:1.6;margin:0 0 24px;color:#525252;">{message}</p>
        { '<a href="https://scorelib.vercel.app/login" style="display:inline-block;background:#0A0A0A;color:#FFFFFF;padding:14px 28px;text-decoration:none;font-weight:600;border-radius:4px;font-size:15px;">Vai al Login</a>' if approved else '' }
      </td></tr>
    </table>
    """
    try:
        params = {
            "from": f"{APP_NAME} <{SENDER_EMAIL}>",
            "to": [to_email],
            "subject": f"Richiesta di accesso {status_text} - {APP_NAME}",
            "html": html,
        }
        result = await asyncio.to_thread(_send_resend_email, params)
        return True
    except Exception as e:
        logger.error(f"Failed to send access notification to {to_email}: {e}")
        return False
