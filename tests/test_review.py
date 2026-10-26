"""Offline regressions for the independent review; all identities are synthetic."""
import io
import json
import os
from pathlib import Path
from datetime import timedelta
import tempfile
import unittest
from unittest.mock import Mock, patch

from codex_reset_notifier.channels import WxBotChannel, SendResult
from codex_reset_notifier.cli import main, load_env
from codex_reset_notifier.events import notices, source_url
from codex_reset_notifier.store import ProcessLock, Store, readonly_status
from codex_reset_notifier.preview import cached_preview
from codex_reset_notifier.source import SourceError
from codex_reset_notifier.cli import tick
from test_worker import CUTOFF, NOW, payload


class ReviewRegressions(unittest.TestCase):
    def test_private_config_permissions_owner_and_errors_are_redacted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            config = Path(tmp) / ".env"
            config.write_text("SMTP_PASSWORD='literal-${EXAMPLE}'\n")
            config.chmod(0o600)
            load_env(config)
            self.assertEqual("literal-${EXAMPLE}", os.environ["SMTP_PASSWORD"])
            config.chmod(0o644)
            with patch("sys.stderr", new_callable=io.StringIO) as error:
                self.assertEqual(2, main(["--env-file", str(config), "status"]))
                self.assertNotIn("literal", error.getvalue())
                self.assertNotIn(tmp, error.getvalue())
            config.chmod(0o600)
            with patch("codex_reset_notifier.store.os.getuid", return_value=-1), self.assertRaises(ValueError):
                load_env(config)
            link = Path(tmp) / "link"
            link.symlink_to(config)
            with self.assertRaises(ValueError):
                load_env(link)
            config.write_bytes(b"SMTP_PASSWORD=\xffSYNTHETIC-PRIVATE")
            with patch("sys.stderr", new_callable=io.StringIO) as error:
                self.assertEqual(2, main(["--env-file", str(config), "status"]))
                self.assertNotIn("SYNTHETIC", error.getvalue())

    def test_only_announced_and_confirmed_are_subscribed(self):
        for stage in ["in_progress", "expired_unconfirmed", "likely_completed"]:
            p = payload()
            p["events"][0]["presentation"]["status"] = stage
            self.assertEqual([], list(notices(p, CUTOFF, NOW)))

    def test_status_while_worker_locked_is_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "state"
            store = Store(directory, CUTOFF)
            before = (directory / "outbox.sqlite3").read_bytes()
            with ProcessLock(directory), patch.dict(os.environ, {"NOTIFIER_STATE_DIR": str(directory)}, clear=True):
                with patch("sys.stdout", new_callable=io.StringIO) as output, patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(0, main(["status"]))
                    self.assertEqual(CUTOFF.isoformat(), json.loads(output.getvalue())["cutoff"])
            self.assertEqual(before, (directory / "outbox.sqlite3").read_bytes())
            store.close()

    def test_estimate_source_day_and_history_remain_estimates(self):
        for basis, expected in [("source_day", "来源日期推算"), ("history", "历史规律推算")]:
            p = payload()
            p["events"][0]["estimate"]["basis"] = basis
            text = list(notices(p, CUTOFF, NOW))[0].text
            self.assertIn(expected, text)
            self.assertIn("不是完成确认", text)
            self.assertNotIn("来源已确认", text)

    def test_source_url_injection_and_invalid_paths_are_rejected(self):
        for url in ["https://x.com/example/status/100\nhttps://evil.invalid",
                    "https://x.com/example/status/100\t", " https://x.com/example/status/100",
                    "https://x.com/example/status/100\x00", "https://x.com/example/status/100\u200b",
                    "https://x.com/anything/status/not-a-number", "https://x.com/example/status/100/extra",
                    "https://x.com/example/status/100%0aevil", "https://x.com/example/status/100?next=evil"]:
            with self.subTest(url=url):
                self.assertIsNone(source_url(url))

    def test_source_url_accepts_only_canonical_status_links(self):
        for url in ["https://x.com/example/status/100", "https://x.com/i/status/100",
                    "https://twitter.com/example/status/100"]:
            self.assertEqual(url, source_url(url))
        self.assertEqual("https://x.com/example/status/100", source_url("https://x.com/example/status/100/"))

    def test_status_absent_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "missing"
            self.assertEqual({"initialized": False}, readonly_status(directory))
            self.assertFalse(directory.exists())

    def test_status_rejects_public_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "state"
            store = Store(directory, CUTOFF)
            store.close()
            db = directory / "outbox.sqlite3"
            before = db.read_bytes()
            for target in [directory, db]:
                target.chmod(0o755 if target == directory else 0o644)
                with self.assertRaises(ValueError):
                    readonly_status(directory)
                target.chmod(0o700 if target == directory else 0o600)
            linked = Path(tmp) / "link"
            linked.symlink_to(directory)
            with self.assertRaises(ValueError):
                readonly_status(linked)
            self.assertEqual(before, db.read_bytes())

    def test_status_wrong_owner_and_corrupt_database_errors_are_redacted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"NOTIFIER_STATE_DIR": tmp + "/state"}, clear=True):
            store = Store(tmp + "/state", CUTOFF)
            store.close()
            with patch("codex_reset_notifier.store.os.getuid", return_value=-1), patch("sys.stderr", new_callable=io.StringIO) as error:
                self.assertEqual(2, main(["status"]))
                self.assertNotIn(tmp, error.getvalue())
            Path(tmp, "state", "outbox.sqlite3").write_bytes(b"SYNTHETIC-PRIVATE-ERROR")
            with patch("sys.stderr", new_callable=io.StringIO) as error:
                self.assertEqual(2, main(["status"]))
                self.assertNotIn("SYNTHETIC", error.getvalue())

    def test_stopped_worker_catches_only_new_unseen_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state", CUTOFF)
            channel, source = Mock(), Mock()
            channel.send.return_value = SendResult("accepted", "test_accepted")
            source.fetch.return_value = ({"schemaVersion": 1, "events": []}, None)
            tick(store, {"smtp": channel}, source, CUTOFF)
            store.close()
            store = Store(Path(tmp) / "state", NOW)
            source.fetch.return_value = (payload(), None)
            tick(store, {"smtp": channel}, source, NOW)
            tick(store, {"smtp": channel}, source, NOW + timedelta(minutes=10))
            channel.send.assert_called_once()
            store.close()

    def channel(self):
        return WxBotChannel("http://127.0.0.1:18790", "example-placeholder", "example", "example-owner")

    def status(self):
        return {"bindings": [{"name": "example", "logged_in": True, "expired": False,
                              "allowlist": ["example-owner"], "delivery": "pull"}]}

    def test_unverified_binding_stops_before_pull_or_send(self):
        cases = [{}, {"bindings": []}, {"bindings": [self.status()["bindings"][0]] * 2}]
        for field, value in [("name", "other"), ("logged_in", False), ("logged_in", "true"),
                             ("expired", True), ("expired", None), ("delivery", "webhook"),
                             ("allowlist", []), ("allowlist", ["other-owner"]),
                             ("allowlist", ["example-owner", "other-owner"])]:
            case = self.status()
            case["bindings"][0][field] = value
            cases.append(case)
        for body in cases:
            for operation in ["send", "drain"]:
                with self.subTest(body=body, operation=operation):
                    channel = self.channel()
                    channel._inbox_request = Mock(return_value=(200, body))
                    channel._opener = Mock()
                    result = channel.send("synthetic") if operation == "send" else channel.drain_inbox()
                    self.assertEqual("needs_user_action", result.state)
                    channel._opener.open.assert_not_called()
                    channel._inbox_request.assert_called_once_with("/v1/status")
                    self.assertNotIn("owner", repr(result))

    def test_status_request_failure_is_closed(self):
        for response in [(None, None), (401, None), (200, {"bindings": "bad"})]:
            channel = self.channel()
            channel._inbox_request = Mock(return_value=response)
            self.assertEqual("needs_user_action", channel.drain_inbox().state)
            channel._inbox_request.assert_called_once_with("/v1/status")

    def test_status_is_rechecked_before_ack(self):
        channel = self.channel()
        changed = self.status()
        changed["bindings"][0]["allowlist"] = ["changed-owner"]
        event = {"schema": "wxbot.event.v1", "binding": "example", "event_id": "example:100",
                 "kind": "message", "from_wxid": "example-owner"}
        channel._inbox_request = Mock(side_effect=[(200, self.status()), (200, {"events": [event]}), (200, changed)])
        result = channel.drain_inbox()
        self.assertEqual(("needs_user_action", 0), (result.state, result.acked))
        self.assertEqual(["/v1/status", "/v1/events?binding=example&limit=100&lease=5m&wait=0s", "/v1/status"],
                         [c.args[0] for c in channel._inbox_request.call_args_list])

    def test_verified_status_http_protocol_precedes_send(self):
        channel = self.channel()
        def response(body):
            result = Mock()
            result.status = 200
            result.read.return_value = json.dumps(body).encode()
            result.__enter__ = Mock(return_value=result)
            result.__exit__ = Mock(return_value=False)
            return result
        channel._opener = Mock()
        channel._opener.open.side_effect = [response(self.status()), response({"ok": True})]
        self.assertEqual("accepted", channel.send("synthetic").state)
        requests = [c.args[0] for c in channel._opener.open.call_args_list]
        self.assertEqual(["GET", "POST"], [r.method for r in requests])
        self.assertEqual(["http://127.0.0.1:18790/v1/status", "http://127.0.0.1:18790/v1/send"], [r.full_url for r in requests])
        self.assertIsNone(requests[0].data)
        self.assertEqual("Bearer example-placeholder", requests[0].get_header("Authorization"))

    def test_wrong_message_owner_never_acknowledged(self):
        channel = self.channel()
        event = {"schema": "wxbot.event.v1", "binding": "example", "event_id": "example:100",
                 "kind": "message", "from_wxid": "another-synthetic-owner", "text": "ignore me"}
        def request(path, body=None):
            if path == "/v1/status":
                return 200, self.status()
            if path == "/v1/events/ack":
                return 200, {"acked": 1}
            return 200, {"events": [event]}
        channel._inbox_request = Mock(side_effect=request)
        result = channel.drain_inbox(max_batches=1)
        self.assertEqual("needs_user_action", result.state)
        self.assertEqual(0, result.acked)
        self.assertFalse(any(c.args[0] == "/v1/events/ack" for c in channel._inbox_request.call_args_list))

    def test_session_alert_reports_safe_diagnosis(self):
        channel = self.channel()
        event = {"schema": "wxbot.event.v1", "binding": "example", "event_id": "example:alert:session_expired:100",
                 "kind": "alert", "from_wxid": "", "alert": {"code": "session_expired", "message": "PRIVATE-DIAGNOSTIC"}}
        def request(path, body=None):
            if path == "/v1/status":
                return 200, self.status()
            if path == "/v1/events/ack":
                return 200, {"acked": 1}
            return 200, {"events": [event]}
        channel._inbox_request = Mock(side_effect=request)
        result = channel.drain_inbox(max_batches=1)
        self.assertEqual(("needs_user_action", "wx_session_expired", 1), (result.state, result.code, result.acked))
        self.assertNotIn("PRIVATE", repr(result))

    def test_unknown_alert_is_not_destructively_acknowledged(self):
        channel = self.channel()
        event = {"schema": "wxbot.event.v1", "binding": "example", "event_id": "example:alert:unknown:100",
                 "kind": "alert", "from_wxid": "", "alert": {"code": "PRIVATE-NEW-CODE", "message": "PRIVATE-BODY"}}
        channel._inbox_request = Mock(side_effect=[(200, self.status()), (200, {"events": [event]})])
        result = channel.drain_inbox()
        self.assertEqual(("needs_user_action", "wx_inbox_unknown_alert", 0), (result.state, result.code, result.acked))
        self.assertNotIn("PRIVATE", repr(result))
        self.assertEqual(2, channel._inbox_request.call_count)

    def test_allowlisted_alerts_keep_only_fixed_diagnostics(self):
        for code, state in [("recovered", "accepted"), ("ilink_unreachable", "needs_user_action")]:
            channel = self.channel()
            event = {"schema": "wxbot.event.v1", "binding": "example", "event_id": f"example:alert:{code}:100",
                     "kind": "alert", "from_wxid": "", "alert": {"code": code, "message": "PRIVATE-BODY", "hint": "PRIVATE-HINT"}}
            channel._inbox_request = Mock(side_effect=[(200, self.status()), (200, {"events": [event]}),
                                                       (200, self.status()), (200, {"acked": 1})])
            result = channel.drain_inbox()
            self.assertEqual((state, "wx_" + code, 1), (result.state, result.code, result.acked))
            self.assertNotIn("PRIVATE", repr(result))

    def test_online_preview_reuses_cache_without_touching_outbox(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"NOTIFIER_STATE_DIR": tmp + "/state"}, clear=True):
            store = Store(tmp + "/state", CUTOFF)
            before = Path(tmp, "state", "outbox.sqlite3").read_bytes()
            with patch("codex_reset_notifier.cli.AIHot") as source, patch("sys.stdout", new_callable=io.StringIO):
                source.return_value.fetch.return_value = (payload(), '"synthetic"')
                for _ in range(2):
                    self.assertEqual(0, main(["run", "--once"]))
                source.return_value.fetch.assert_called_once()
            self.assertEqual(before, Path(tmp, "state", "outbox.sqlite3").read_bytes())
            store.close()

    def test_preview_cache_keeps_etag_retry_after_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Mock()
            source.minimum_interval = 600
            source.fetch.return_value = (payload(), '"synthetic"')
            directory = Path(tmp) / "state"
            cached_preview(directory, source, CUTOFF, NOW)
            self.assertFalse((directory / "outbox.sqlite3").exists())
            self.assertEqual(0, (directory / "preview-cache.sqlite3").stat().st_mode & 0o077)
            source.fetch.return_value = (None, '"synthetic"')
            data, state, _ = cached_preview(directory, source, CUTOFF, NOW + timedelta(minutes=10))
            self.assertEqual("not_modified", state)
            self.assertEqual(payload(), data)
            source.fetch.assert_called_with('"synthetic"')
            source.fetch.side_effect = SourceError(1800)
            with self.assertRaises(SourceError):
                cached_preview(directory, source, CUTOFF, NOW + timedelta(minutes=20))
            calls = source.fetch.call_count
            data, state, _ = cached_preview(directory, source, CUTOFF, NOW + timedelta(minutes=30))
            self.assertEqual("cached", state)
            self.assertEqual(calls, source.fetch.call_count)


if __name__ == "__main__":
    unittest.main()
