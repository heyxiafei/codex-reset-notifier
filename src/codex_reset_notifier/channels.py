"""Text-only transports. An accepted request is not a delivery receipt.

wx-bot protocol reference: lawrencewzen/wx-bot v0.5.1, commit
4eb9fabcf994b4e491c6b7284d73528652266849, docs/protocol.md section 5.
No transport retries a request; the worker must persist the result before exit.
"""

from dataclasses import dataclass
from email.message import EmailMessage
from http.client import HTTPException
import json
import re
import smtplib
import ssl
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


@dataclass(frozen=True)
class SendResult:
    """Safe to log: code contains neither provider responses nor configuration."""

    state: str
    code: str


@dataclass(frozen=True)
class DrainResult:
    """Only fixed status and an acknowledgement count; never contains event data."""

    state: str
    code: str
    acked: int


def _single_line(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("invalid " + field)
    return value


class SMTPChannel:
    """Authenticated SMTP using certificate-verified TLS (implicit or STARTTLS)."""

    def __init__(self, host: str, port: int, username: str, password: str,
                 sender: str, recipient: str, tls_mode: str = "ssl",
                 timeout: float = 20):
        self.host = _single_line(host, "SMTP host")
        self.port = int(port)
        self.username = _single_line(username, "SMTP username")
        self._password = _single_line(password, "SMTP password")
        self.sender = _single_line(sender, "SMTP sender")
        self.recipient = _single_line(recipient, "SMTP recipient")
        if tls_mode not in {"ssl", "starttls"}:
            raise ValueError("SMTP TLS mode must be ssl or starttls")
        if not 1 <= self.port <= 65535 or not 0 < timeout <= 120:
            raise ValueError("invalid SMTP port or timeout")
        self.tls_mode = tls_mode
        self.timeout = timeout

    def send(self, text: str, subject: str = "AIHot Codex 重置提醒") -> SendResult:
        _single_line(subject, "email subject")
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Subject"] = subject
        msg.set_content(text)
        client: Optional[smtplib.SMTP] = None
        submitted = False
        result = SendResult("unknown", "smtp_outcome_unknown")
        try:
            context = ssl.create_default_context()
            if self.tls_mode == "ssl":
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout,
                                          context=context)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(self.username, self._password)
            # A disconnect here may occur after the server accepted DATA.
            submitted = True
            refused = client.send_message(msg, from_addr=self.sender,
                                           to_addrs=[self.recipient])
            result = (SendResult("needs_user_action", "smtp_recipient_refused")
                      if refused else SendResult("accepted", "smtp_accepted"))
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPNotSupportedError,
                smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused,
                smtplib.SMTPDataError, ssl.SSLCertVerificationError):
            result = SendResult("needs_user_action", "smtp_configuration_or_refusal")
        except TimeoutError:
            result = SendResult("unknown", "smtp_outcome_unknown")
        except (OSError, smtplib.SMTPException):
            result = (SendResult("unknown", "smtp_outcome_unknown") if submitted
                      else SendResult("needs_user_action", "smtp_connection_or_tls"))
        finally:
            if client is not None:
                # Closing is deliberately separate: QUIT failure cannot undo DATA acceptance.
                try:
                    client.close()
                except (OSError, smtplib.SMTPException):
                    pass
        return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WxBotChannel:
    """Use an independently configured wx-bot owner-only local HTTP API.

    Never opens wx-bot state files, credentials or chat logs. drain_inbox only
    consumes and discards events from a dedicated pull binding through the API.
    IP literals prevent hostname rebinding; proxies and redirects are disabled.
    """

    def __init__(self, base_url: str, token: str, binding: str, recipient: str,
                 timeout: float = 20):
        parsed = urlsplit(base_url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("invalid wx-bot loopback URL") from None
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or port is None or not 1 <= port <= 65535):
            raise ValueError("wx-bot URL must be an HTTP loopback IP with a port")
        self.base_url = base_url.rstrip("/")
        self._token = _single_line(token, "wx-bot API token")
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", binding):
            raise ValueError("invalid wx-bot binding")
        self.binding = binding
        self.recipient = _single_line(recipient, "wx-bot recipient")
        if not 0 < timeout <= 120:
            raise ValueError("invalid wx-bot timeout")
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def verify_binding(self) -> SendResult:
        """Read owner-only status before every pull, destructive ack, or send."""
        status, body = self._inbox_request("/v1/status")
        if status != 200 or body is None or not isinstance(body.get("bindings"), list):
            return SendResult("needs_user_action", "wx_binding_unverified")
        matches = [b for b in body["bindings"] if isinstance(b, dict) and b.get("name") == self.binding]
        if len(matches) != 1:
            return SendResult("needs_user_action", "wx_binding_unverified")
        binding = matches[0]
        if (binding.get("logged_in") is not True or binding.get("expired") is not False
                or binding.get("delivery") != "pull" or binding.get("allowlist") != [self.recipient]):
            return SendResult("needs_user_action", "wx_binding_unverified")
        return SendResult("accepted", "wx_binding_verified")

    @staticmethod
    def _result(status: int, raw: bytes) -> SendResult:
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        error = str(body.get("error", ""))
        # iLink context expiry can be exposed inside a wx-bot HTTP 502 error.
        if (body.get("ret") == -2 or re.search(r"(?:ret\s*[:=]\s*|\bcode\s*[:=]\s*)-2\b", error)
                or "context_token" in error or "context token" in error.lower()):
            return SendResult("needs_user_action", "wx_context_expired")
        if status == 200 and body.get("ok") is True and "error" not in body:
            return SendResult("accepted", "wx_accepted")
        if status in {400, 401, 403, 404, 409, 413}:
            return SendResult("needs_user_action", "wx_configuration_or_context")
        return SendResult("unknown", "wx_outcome_unknown")

    def send(self, text: str, subject: str = "AIHot Codex 重置提醒") -> SendResult:
        del subject  # Shared channel interface; WeChat sends plain text only.
        verified = self.verify_binding()
        if verified.state != "accepted":
            return verified
        payload = json.dumps({"binding": self.binding, "to": self.recipient,
                              "text": text}, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + "/v1/send", data=payload, method="POST",
                          headers={"Content-Type": "application/json",
                                   "Authorization": "Bearer " + self._token})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return self._result(response.status, response.read(65537))
        except HTTPError as error:
            try:
                raw = error.read(65537)
            except (OSError, HTTPException):
                raw = b""
            return self._result(error.code, raw)
        except (OSError, URLError, ValueError, HTTPException):
            return SendResult("unknown", "wx_outcome_unknown")

    def _inbox_request(self, path: str, payload=None):
        """Read a bounded JSON envelope without exposing provider data to callers."""
        headers = {"Authorization": "Bearer " + self._token}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=data,
                          method="POST" if data is not None else "GET", headers=headers)
        max_bytes = 8 * 1024 * 1024
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read(max_bytes + 1)
        except HTTPError as error:
            # Do not propagate/log the body: it could include user content.
            error.close()
            return error.code, None
        except (OSError, URLError, ValueError, HTTPException):
            return None, None
        if len(raw) > max_bytes:
            return status, None
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            return status, None
        return status, body if isinstance(body, dict) else None

    def drain_inbox(self, max_batches: int = 5) -> DrainResult:
        """Discard inbound messages/alerts, acknowledging only this pull binding.

        This worker must be the binding's exclusive consumer in a dedicated
        wx-bot instance. Inbound text is never treated as an instruction, sent
        anywhere, retained, or returned. No send/keepalive endpoint is called.
        A failed pull leases events for five minutes; wx-bot makes them available
        again after the lease. Each HTTP operation is attempted exactly once.
        """
        if type(max_batches) is not int or not 1 <= max_batches <= 10:
            raise ValueError("invalid wx-bot drain batch limit")
        acked = 0
        diagnostic = None
        query = urlencode({"binding": self.binding, "limit": 100,
                           "lease": "5m", "wait": "0s"})
        for _ in range(max_batches):
            verified = self.verify_binding()
            if verified.state != "accepted":
                return DrainResult(verified.state, verified.code, acked)
            status, body = self._inbox_request("/v1/events?" + query)
            if status in {400, 401, 403, 404, 409, 413}:
                return DrainResult("needs_user_action", "wx_inbox_configuration", acked)
            if status != 200 or body is None or not isinstance(body.get("events"), list):
                return DrainResult("unknown", "wx_inbox_pull_unknown", acked)
            events = body["events"]
            if not events:
                return diagnostic or DrainResult("accepted", "wx_inbox_drained", acked)
            if len(events) > 100:
                return DrainResult("needs_user_action", "wx_inbox_invalid_envelope", acked)
            ids = []
            for event in events:
                if not isinstance(event, dict):
                    return DrainResult("needs_user_action", "wx_inbox_invalid_envelope", acked)
                event_id = event.get("event_id")
                if (event.get("schema") != "wxbot.event.v1"
                        or event.get("binding") != self.binding
                        or not isinstance(event_id, str)
                        or not event_id.startswith(self.binding + ":")
                        or not len(self.binding) + 1 < len(event_id) <= 256
                        or any(ord(c) < 32 or ord(c) == 127 for c in event_id)):
                    return DrainResult("needs_user_action", "wx_inbox_scope_mismatch", acked)
                if event_id not in ids:
                    ids.append(event_id)
                kind = event.get("kind")
                if kind == "message":
                    if event.get("from_wxid") != self.recipient:
                        return DrainResult("needs_user_action", "wx_inbox_owner_mismatch", acked)
                elif kind == "alert":
                    alert = event.get("alert")
                    code = alert.get("code") if isinstance(alert, dict) else None
                    if (not isinstance(code, str) or code not in {"session_expired", "ilink_unreachable", "recovered"}
                            or event.get("from_wxid") != "" or not event_id.startswith(self.binding + ":alert:" + code + ":")):
                        return DrainResult("needs_user_action", "wx_inbox_unknown_alert", acked)
                    if diagnostic is None or code == "session_expired" or diagnostic.code == "wx_recovered":
                        diagnostic = DrainResult("accepted" if code == "recovered" else "needs_user_action", "wx_" + code, acked)
                else:
                    return DrainResult("needs_user_action", "wx_inbox_invalid_kind", acked)
            # Drop references to content before constructing the ack request.
            del event, events, body
            verified = self.verify_binding()
            if verified.state != "accepted":
                return DrainResult(verified.state, verified.code, acked)
            status, body = self._inbox_request("/v1/events/ack", {"event_ids": ids})
            if status in {400, 401, 403, 404, 409, 413}:
                return DrainResult("needs_user_action", "wx_inbox_configuration", acked)
            if (status != 200 or body is None or type(body.get("acked")) is not int
                    or not 0 <= body["acked"] <= len(ids)):
                return DrainResult("unknown", "wx_inbox_ack_unknown", acked)
            acked += body["acked"]
            if body["acked"] != len(ids):
                return DrainResult("unknown", "wx_inbox_ack_mismatch", acked)
            if diagnostic:
                # Retain only the fixed diagnostic; message/hint bodies are discarded.
                return DrainResult(diagnostic.state, diagnostic.code, acked)
        return DrainResult("accepted", "wx_inbox_batch_limit", acked)
