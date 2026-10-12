import smtplib
import ssl
import email.utils
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Dict, Any
import logging

from app.services.email_transports.base import BaseEmailTransport, EmailDeliveryResult

logger = logging.getLogger(__name__)

class SMTPEmailTransport(BaseEmailTransport):
    """
    Real SMTP provider integration using standard Python smtplib with TLS/SSL.
    Supports production SMTP relays as well as sandbox test hosts (e.g. MailHog, Mailpit).
    """

    def send_email(
        self,
        from_email: str,
        from_name: str,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: Optional[str],
        headers: Dict[str, str],
        credentials: Dict[str, Any],
        idempotency_key: str
    ) -> EmailDeliveryResult:
        from app.core.config import settings
        host = credentials.get("smtp_host") or getattr(settings, "SMTP_HOST", None) or "localhost"
        port = int(credentials.get("smtp_port") or getattr(settings, "SMTP_PORT", None) or 587)
        username = credentials.get("smtp_user") or credentials.get("smtp_username") or getattr(settings, "SMTP_USERNAME", None)
        password = credentials.get("smtp_password") or getattr(settings, "SMTP_PASSWORD", None)
        use_tls = credentials.get("use_tls", getattr(settings, "SMTP_USE_TLS", True))
        timeout = float(credentials.get("timeout_seconds", 10.0))
        is_sandbox = credentials.get("is_sandbox", False)

        # Generate standard RFC 2822 Message-ID
        msg_id = email.utils.make_msgid(idempotency_key, domain=from_email.split("@")[-1])

        # Construct MIME Message
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Message-ID"] = msg_id
        msg["Date"] = email.utils.formatdate(localtime=True)

        for h_name, h_val in headers.items():
            msg[h_name] = h_val

        part1 = MIMEText(body_text, "plain", "utf-8")
        msg.attach(part1)

        if body_html:
            part2 = MIMEText(body_html, "html", "utf-8")
            msg.attach(part2)

        # Explicit Sandbox/Mock Handling:
        # In production, sandbox or mock mode is strictly forbidden.
        is_production = getattr(settings, "ENVIRONMENT", "development") == "production"

        if is_sandbox or host == "sandbox.local":
            if is_production:
                return EmailDeliveryResult(
                    success=False,
                    error_message="REAL_TRANSPORT_NOT_CONFIGURED: Sandbox or mock SMTP transport is strictly forbidden in production environment.",
                    retryable=False,
                    transport_mode="MOCK"
                )
            # Explicit sandbox allowed only in non-production environments
            return EmailDeliveryResult(
                success=True,
                provider_message_id=msg_id,
                transport_mode="MOCK",
                details={"transport": "SMTP_SANDBOX", "host": host, "port": port}
            )

        # Real Outbound Transmission Safety Invariant:
        # If performing a real transmission, host and credentials must be present.
        # Missing credentials must FAIL CLOSED and NEVER return simulated success.
        if not host or (username and not password) or (not password and host != "localhost"):
            return EmailDeliveryResult(
                success=False,
                error_message="REAL_TRANSPORT_NOT_CONFIGURED: SMTP host or credentials not configured for real provider transmission.",
                retryable=False,
                transport_mode="REAL"
            )

        try:
            if port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
                    if username and password:
                        server.login(username, password)
                    refused = server.sendmail(from_email, [to_email], msg.as_string())
                    if refused and to_email in refused:
                        return EmailDeliveryResult(
                            success=False,
                            error_message=f"SMTP Recipient Refused: {refused[to_email]}",
                            retryable=False,
                            transport_mode="REAL"
                        )
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as server:
                    if use_tls:
                        context = ssl.create_default_context()
                        server.starttls(context=context)
                    if username and password:
                        server.login(username, password)
                    refused = server.sendmail(from_email, [to_email], msg.as_string())
                    if refused and to_email in refused:
                        return EmailDeliveryResult(
                            success=False,
                            error_message=f"SMTP Recipient Refused: {refused[to_email]}",
                            retryable=False,
                            transport_mode="REAL"
                        )

                    try:
                        noop_code, noop_resp = server.noop()
                        smtp_code = int(noop_code) if isinstance(noop_code, (int, str)) and str(noop_code).isdigit() else 250
                        smtp_resp_str = noop_resp.decode("utf-8", errors="replace") if isinstance(noop_resp, bytes) else str(noop_resp)
                    except Exception:
                        smtp_code = 250
                        smtp_resp_str = "250 OK"

            logger.info("Real SMTP transmission succeeded via %s:%d (Code: %d, Response: %s)", host, port, smtp_code, smtp_resp_str)

            return EmailDeliveryResult(
                success=True,
                provider_message_id=msg_id,
                transport_mode="REAL",
                details={
                    "transport": "SMTP",
                    "host": host,
                    "port": port,
                    "smtp_response_code": smtp_code,
                    "smtp_response": smtp_resp_str
                }
            )

        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError) as net_err:
            logger.error("SMTP Network Connection Error during transmission: %s", type(net_err).__name__)
            return EmailDeliveryResult(
                success=False,
                error_message=f"SMTP Connection Error: {str(net_err)}",
                retryable=True,
                transport_mode="REAL"
            )
        except smtplib.SMTPAuthenticationError as auth_err:
            logger.error("SMTP Authentication Failed.")
            return EmailDeliveryResult(
                success=False,
                error_message=f"SMTP Auth Error: {str(auth_err)}",
                retryable=False,
                transport_mode="REAL"
            )
        except smtplib.SMTPResponseException as resp_err:
            is_5xx = 500 <= resp_err.smtp_code < 600
            return EmailDeliveryResult(
                success=False,
                error_message=f"SMTP Error [{resp_err.smtp_code}]: {resp_err.smtp_error}",
                retryable=not is_5xx,
                transport_mode="REAL"
            )
        except Exception as e:
            logger.error("Unexpected SMTP Transmission Failure: %s", type(e).__name__)
            return EmailDeliveryResult(
                success=False,
                error_message=f"Unexpected SMTP Exception: {str(e)}",
                retryable=False,
                transport_mode="REAL"
            )
