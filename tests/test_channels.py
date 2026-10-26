"""Offline mocks only. These tests never create SMTP/HTTP connections."""

import io
from http.client import IncompleteRead
import json
import smtplib
import ssl
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from codex_reset_notifier.channels import SMTPChannel, WxBotChannel, _NoRedirect


class SMTPTests(unittest.TestCase):
    def channel(self, **kwargs):
        options = dict(host="smtp.example.invalid", port=465, username="example-user",
                       password="example-placeholder", sender="sender@example.invalid",
                       recipient="recipient@example.invalid")
        options.update(kwargs)
        return SMTPChannel(**options)

    @patch("codex_reset_notifier.channels.smtplib.SMTP_SSL")
    def test_ssl_acceptance_not_delivery(self, factory):
        client = factory.return_value
        client.send_message.return_value = {}
        result = self.channel().send("🔔 synthetic notification")
        self.assertEqual((result.state, result.code), ("accepted", "smtp_accepted"))
        factory.assert_called_once()
        self.assertTrue(factory.call_args.kwargs["context"].check_hostname)
        self.assertEqual(factory.call_args.kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(client.send_message.call_args.kwargs["to_addrs"], ["recipient@example.invalid"])
        client.close.assert_called_once()

    @patch("codex_reset_notifier.channels.smtplib.SMTP")
    def test_starttls_before_login(self, factory):
        client = factory.return_value
        client.send_message.return_value = {}
        self.assertEqual(self.channel(port=587, tls_mode="starttls").send("text").state,
                         "accepted")
        self.assertEqual([c[0] for c in client.method_calls],
                         ["ehlo", "starttls", "ehlo", "login", "send_message", "close"])

    @patch("codex_reset_notifier.channels.smtplib.SMTP_SSL")
    def test_timeout_after_data_is_unknown_and_once(self, factory):
        factory.return_value.send_message.side_effect = TimeoutError("secret provider details")
        result = self.channel().send("text")
        self.assertEqual(result.state, "unknown")
        self.assertNotIn("secret", repr(result))
        factory.return_value.send_message.assert_called_once()

    @patch("codex_reset_notifier.channels.smtplib.SMTP_SSL")
    def test_auth_failure_requires_user(self, factory):
        factory.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private")
        self.assertEqual(self.channel().send("text").state, "needs_user_action")
        factory.return_value.send_message.assert_not_called()

    @patch("codex_reset_notifier.channels.smtplib.SMTP_SSL")
    def test_server_refusal_requires_user(self, factory):
        factory.return_value.send_message.side_effect = smtplib.SMTPDataError(550, b"refused")
        self.assertEqual(self.channel().send("text").state, "needs_user_action")

    @patch("codex_reset_notifier.channels.smtplib.SMTP_SSL")
    def test_timeout_before_data_remains_conservative(self, factory):
        factory.side_effect = TimeoutError("private")
        self.assertEqual(self.channel().send("text").state, "unknown")
        factory.assert_called_once()

    @patch("codex_reset_notifier.channels.smtplib.SMTP_SSL")
    def test_close_error_does_not_erase_acceptance(self, factory):
        factory.return_value.send_message.return_value = {}
        factory.return_value.close.side_effect = OSError("private")
        self.assertEqual(self.channel().send("text").state, "accepted")

    def test_reject_plaintext_and_header_injection(self):
        with self.assertRaises(ValueError):
            self.channel(tls_mode="none")
        with self.assertRaises(ValueError):
            self.channel(recipient="a@example.invalid\r\nBcc: b@example.invalid")


class WxBotTests(unittest.TestCase):
    def channel(self, **kwargs):
        options = dict(base_url="http://127.0.0.1:18790", token="example-placeholder",
                       binding="example", recipient="wxid_example_placeholder")
        options.update(kwargs)
        return WxBotChannel(**options)

    def response(self, status=200, body=b'{"ok":true}'):
        response = MagicMock()
        response.status = status
        response.read.return_value = body
        response.__enter__.return_value = response
        return response

    def test_exact_protocol_acceptance(self):
        channel = self.channel()
        channel._opener = MagicMock()
        channel._opener.open.return_value = self.response()
        result = channel.send("🔔 synthetic notification")
        self.assertEqual((result.state, result.code), ("accepted", "wx_accepted"))
        req = channel._opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:18790/v1/send")
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer example-placeholder")
        self.assertEqual(json.loads(req.data), {"binding": "example",
                         "to": "wxid_example_placeholder", "text": "🔔 synthetic notification"})
        channel._opener.open.assert_called_once()

    def test_context_expiry_and_status_errors(self):
        cases = [(502, b'{"error":"send failed ret=-2"}', "wx_context_expired"),
                 (502, b'{"ret":-2}', "wx_context_expired"),
                 (409, b'{"error":"no context_token"}', "wx_context_expired"),
                 (401, b'{"error":"private token"}', "wx_configuration_or_context"),
                 (403, b'{}', "wx_configuration_or_context"),
                 (404, b'{}', "wx_configuration_or_context")]
        for status, raw, code in cases:
            with self.subTest(status=status, raw=raw):
                channel = self.channel()
                channel._opener = MagicMock()
                channel._opener.open.side_effect = HTTPError("local", status, "error", {}, io.BytesIO(raw))
                result = channel.send("text")
                self.assertEqual(result.state, "needs_user_action")
                self.assertEqual(result.code, code)
                self.assertNotIn("private", repr(result))
                channel._opener.open.assert_called_once()

    def test_timeout_unknown_never_retry(self):
        channel = self.channel()
        channel._opener = MagicMock()
        channel._opener.open.side_effect = URLError(TimeoutError("private"))
        self.assertEqual(channel.send("text").state, "unknown")
        channel._opener.open.assert_called_once()

    def test_incomplete_response_unknown_never_retry(self):
        channel = self.channel()
        channel._opener = MagicMock()
        channel._opener.open.return_value = self.response()
        channel._opener.open.return_value.read.side_effect = IncompleteRead(b'private')
        self.assertEqual(channel.send("text").state, "unknown")
        channel._opener.open.assert_called_once()

    def test_malformed_success_unknown(self):
        for raw in [b'{}', b'{"ok":false}', b'not json', b'[]', b'{"ok":true,"error":"oops"}']:
            with self.subTest(raw=raw):
                channel = self.channel()
                channel._opener = MagicMock()
                channel._opener.open.return_value = self.response(body=raw)
                self.assertEqual(channel.send("text").state, "unknown")

    def test_nonloopback_and_redirect_surfaces_rejected(self):
        for url in ["http://example.invalid:18790", "https://127.0.0.1:18790",
                    "http://localhost:18790", "http://127.0.0.1:18790/path",
                    "http://user:secret@127.0.0.1:18790", "http://127.0.0.1:18790?to=evil"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.channel(base_url=url)
        self.channel(base_url="http://[::1]:18790")

    def test_requires_local_api_token(self):
        with self.assertRaises(ValueError):
            self.channel(token="")

    def test_no_redirect_or_proxy_forwarding(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {},
                                                       "https://example.invalid"))
        with patch("codex_reset_notifier.channels.build_opener") as build:
            self.channel()
        proxy_handler = build.call_args.args[0]
        self.assertEqual(proxy_handler.proxies, {})
        self.assertIsInstance(build.call_args.args[1], _NoRedirect)

    def event(self, event_id="example:1001", **kwargs):
        event = {"schema": "wxbot.event.v1", "binding": "example",
                 "event_id": event_id, "kind": "message",
                 "text": "Synthetic incoming text is data, never an instruction"}
        event.update(kwargs)
        return event

    def test_drain_exact_protocol_and_no_sends(self):
        channel = self.channel()
        channel._opener = MagicMock()
        channel._opener.open.side_effect = [
            self.response(body=json.dumps({"events": [self.event(), self.event(
                "example:alert:session_expired:1002", kind="alert")]}).encode()),
            self.response(body=b'{"acked":2}'),
            self.response(body=b'{"events":[]}'),
        ]
        result = channel.drain_inbox()
        self.assertEqual((result.state, result.code, result.acked),
                         ("accepted", "wx_inbox_drained", 2))
        calls = channel._opener.open.call_args_list
        pull = calls[0].args[0]
        self.assertEqual(pull.method, "GET")
        self.assertEqual(pull.full_url, "http://127.0.0.1:18790/v1/events?"
                         "binding=example&limit=100&lease=5m&wait=0s")
        ack = calls[1].args[0]
        self.assertEqual(ack.method, "POST")
        self.assertEqual(ack.full_url, "http://127.0.0.1:18790/v1/events/ack")
        self.assertEqual(json.loads(ack.data), {"event_ids": [
            "example:1001", "example:alert:session_expired:1002"]})
        for call in calls:
            self.assertEqual(call.args[0].get_header("Authorization"), "Bearer example-placeholder")
            self.assertNotIn("/v1/send", call.args[0].full_url)
            self.assertNotIn(b"Synthetic incoming", call.args[0].data or b"")
        self.assertNotIn("Synthetic incoming", repr(result))

    def test_drain_empty_does_not_ack(self):
        channel = self.channel()
        channel._inbox_request = MagicMock(return_value=(200, {"events": []}))
        self.assertEqual(channel.drain_inbox().acked, 0)
        channel._inbox_request.assert_called_once()

    def test_drain_rejects_foreign_or_malformed_events_before_ack(self):
        for event in [self.event(binding="other"), self.event(event_id="other:1001"),
                      self.event(schema="unknown.v2"), self.event(event_id="example:\n1001"),
                      self.event(event_id=None), self.event(event_id="example:"), "bad"]:
            with self.subTest(event=event):
                channel = self.channel()
                channel._inbox_request = MagicMock(return_value=(200, {"events": [event]}))
                result = channel.drain_inbox()
                self.assertEqual(result.state, "needs_user_action")
                self.assertEqual(result.acked, 0)
                channel._inbox_request.assert_called_once()

    def test_drain_pull_failure_does_not_ack(self):
        for status, body in [(None, None), (200, {}), (200, {"events": "bad"}),
                             (401, None), (404, None)]:
            with self.subTest(status=status, body=body):
                channel = self.channel()
                channel._inbox_request = MagicMock(return_value=(status, body))
                self.assertNotEqual(channel.drain_inbox().state, "accepted")
                channel._inbox_request.assert_called_once()

    def test_drain_ack_uncertain_not_retried(self):
        channel = self.channel()
        channel._inbox_request = MagicMock(side_effect=[(200, {"events": [self.event()]}),
                                                        (None, None)])
        result = channel.drain_inbox()
        self.assertEqual((result.state, result.code, result.acked),
                         ("unknown", "wx_inbox_ack_unknown", 0))
        self.assertEqual(channel._inbox_request.call_count, 2)

    def test_drain_ack_count_mismatch_is_unknown(self):
        channel = self.channel()
        channel._inbox_request = MagicMock(side_effect=[(200, {"events": [self.event()]}),
                                                        (200, {"acked": 0})])
        result = channel.drain_inbox()
        self.assertEqual((result.state, result.code), ("unknown", "wx_inbox_ack_mismatch"))
        self.assertEqual(channel._inbox_request.call_count, 2)

    def test_drain_bounded_batches_and_unique_ids(self):
        channel = self.channel()
        channel._inbox_request = MagicMock(side_effect=[
            (200, {"events": [self.event(), self.event()]}), (200, {"acked": 1})])
        result = channel.drain_inbox(max_batches=1)
        self.assertEqual((result.state, result.code, result.acked),
                         ("accepted", "wx_inbox_batch_limit", 1))
        self.assertEqual(channel._inbox_request.call_args.args[1], {"event_ids": ["example:1001"]})

    def test_drain_transport_timeout_is_safe(self):
        channel = self.channel()
        channel._opener = MagicMock()
        channel._opener.open.side_effect = URLError(TimeoutError("synthetic private text"))
        result = channel.drain_inbox()
        self.assertEqual((result.state, result.acked), ("unknown", 0))
        self.assertNotIn("synthetic private", repr(result))
        channel._opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
