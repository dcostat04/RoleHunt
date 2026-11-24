from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from job_scout.config import Secrets


class EmailConfigError(Exception):
    pass


def build_message(secrets: Secrets, subject: str, text: str, html: str) -> EmailMessage:
    if not (secrets.email_from and secrets.email_to):
        raise EmailConfigError("EMAIL_TO and SMTP_USER/EMAIL_FROM must be set")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Job Scout <{secrets.email_from}>"
    msg["To"] = ", ".join(secrets.email_to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def _send_sync(secrets: Secrets, msg: EmailMessage) -> None:
    if not (secrets.smtp_user and secrets.smtp_password):
        raise EmailConfigError("SMTP_USER and SMTP_PASSWORD must be set")
    context = ssl.create_default_context()
    if secrets.smtp_port == 465:
        with smtplib.SMTP_SSL(secrets.smtp_host, secrets.smtp_port, context=context, timeout=30) as smtp:
            smtp.login(secrets.smtp_user, secrets.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(secrets.smtp_host, secrets.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(secrets.smtp_user, secrets.smtp_password)
            smtp.send_message(msg)


async def send_email(secrets: Secrets, subject: str, text: str, html: str) -> None:
    msg = build_message(secrets, subject, text, html)
    await asyncio.to_thread(_send_sync, secrets, msg)
