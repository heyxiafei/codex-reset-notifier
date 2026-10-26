import copy
import gzip
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.client import IncompleteRead
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from codex_reset_notifier.cli import main, tick
from codex_reset_notifier.channels import SendResult
from codex_reset_notifier.events import notices, timestamp
from codex_reset_notifier.source import AIHot, SourceError, retry_seconds
from codex_reset_notifier.store import ProcessLock, Store

FIXTURE = Path(__file__).parent / "fixtures" / "recent.json"
CUTOFF = timestamp("2030-01-01T00:00:00Z")
NOW = CUTOFF + timedelta(hours=2)


def payload():
    return json.loads(FIXTURE.read_text())


class MappingTests(unittest.TestCase):
    def test_template_source_time_scope_estimate_and_url(self):
        n = list(notices(payload(), CUTOFF, NOW))[0]
        self.assertIn("范围尚未明确", n.text)
        self.assertIn("2030-01-01T01:00:00+00:00", n.text)
        self.assertIn("来源推算", n.text)
        self.assertIn("https://x.com/example/status/100", n.text)
        self.assertNotIn("DO_NOT_USE_LEGACY_LABEL", n.text)
        self.assertNotIn("今天", n.text)

    def test_historical_and_exact_cutoff_not_replayed(self):
        self.assertEqual([], list(notices(payload(), NOW, NOW)))
        self.assertEqual([], list(notices(payload(), timestamp("2030-01-01T01:00:00Z"), NOW)))

    def test_future_timestamp_waits(self):
        self.assertEqual([], list(notices(payload(), CUTOFF, CUTOFF)))

    def test_likely_completed_never_upgraded(self):
        p = payload()
        p["events"][0]["presentation"]["status"] = "likely_completed"
        self.assertEqual([], list(notices(p, CUTOFF, NOW)))

    def test_confirmed_requires_source_confirmation(self):
        p = payload()
        e = p["events"][0]
        e["status"] = e["presentation"]["status"] = "confirmed"
        e["confirmedAt"] = "2030-01-01T01:30:00Z"
        self.assertEqual([], list(notices(p, CUTOFF, NOW)))
        e["confirmationBasis"] = "source_post"
        e["posts"].append({"stage": "确认完成", "publishedAt": "2030-01-01T01:30:00Z", "url": "https://x.com/example/status/101"})
        self.assertIn(":confirmed", list(notices(p, CUTOFF, NOW))[0].key)

    def test_old_announcement_cannot_become_fresh_confirmation(self):
        p = payload()
        e = p["events"][0]
        e["status"] = e["presentation"]["status"] = "confirmed"
        e["confirmedAt"] = "2030-01-01T01:30:00Z"
        e["confirmationBasis"] = "source_post"
        self.assertEqual([], list(notices(p, CUTOFF, NOW)))
        e["posts"][0]["stage"] = "确认完成"
        self.assertEqual([], list(notices(p, NOW - timedelta(minutes=30), NOW)))

    def test_missing_or_malformed_fields_skip_safely(self):
        for key in ["id", "presentation", "createdAt", "posts"]:
            p = payload()
            del p["events"][0][key]
            self.assertEqual([], list(notices(p, CUTOFF, NOW)))
        for stage in [[], {}, None, 42]:
            p = payload()
            p["events"][0]["presentation"]["status"] = stage
            self.assertEqual([], list(notices(p, CUTOFF, NOW)))

    def test_unknown_basis_does_not_crash_or_promote(self):
        p = payload()
        p["events"][0]["estimate"]["basis"] = []
        self.assertIn("依据未明确", list(notices(p, CUTOFF, NOW))[0].text)

    def test_extreme_timestamp_is_invalid(self):
        self.assertIsNone(timestamp("9999-12-31T23:59:59-01:00"))
        self.assertIsNone(timestamp("0001-01-01T00:00:00+01:00"))

    def test_missing_original_url_never_fabricated(self):
        p = payload()
        p["events"][0]["posts"][0]["url"] = "http://evil.invalid/status/100"
        self.assertEqual([], list(notices(p, CUTOFF, NOW)))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name) / "state"
        self.store = Store(self.directory, CUTOFF)
        self.channel = Mock()
        self.channel.send.return_value = SendResult("accepted", "test_accepted")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_duplicates_and_changed_estimate_do_not_resend(self):
        p = payload()
        for _ in range(2):
            self.store.enqueue(notices(p, CUTOFF, NOW), ["smtp"])
            self.store.dispatch({"smtp": self.channel})
            p["events"][0]["estimate"]["through"] = "2030-01-01T06:00:00Z"
        self.channel.send.assert_called_once()

    def test_restart_keeps_cutoff_and_accepted_ledger(self):
        self.store.enqueue(notices(payload(), CUTOFF, NOW), ["smtp"])
        self.store.dispatch({"smtp": self.channel})
        self.store.close()
        self.store = Store(self.directory, NOW)
        self.assertEqual(CUTOFF, timestamp(self.store.get("cutoff")))
        self.store.enqueue(notices(payload(), CUTOFF, NOW), ["smtp"])
        self.store.dispatch({"smtp": self.channel})
        self.channel.send.assert_called_once()

    def test_interrupted_send_becomes_unknown(self):
        self.store.enqueue(notices(payload(), CUTOFF, NOW), ["smtp"])
        self.store.db.execute("UPDATE outbox SET state='sending'")
        self.store.db.commit()
        self.store.recover()
        self.store.dispatch({"smtp": self.channel})
        self.channel.send.assert_not_called()
        self.assertEqual("unknown", self.store.summary()["outbox"][0]["state"])

    def test_unknown_is_not_retried(self):
        self.channel.send.return_value = SendResult("unknown", "test_timeout")
        for _ in range(2):
            self.store.enqueue(notices(payload(), CUTOFF, NOW), ["smtp"])
            self.store.dispatch({"smtp": self.channel})
        self.channel.send.assert_called_once()

    def test_per_channel_result_and_channels_off(self):
        self.store.enqueue(notices(payload(), CUTOFF, NOW), [])
        self.assertEqual([], self.store.summary()["outbox"])
        self.store.enqueue(notices(payload(), CUTOFF, NOW), ["smtp", "wxbot"])
        failed = Mock()
        failed.send.return_value = SendResult("needs_user_action", "test_context")
        self.store.dispatch({"smtp": self.channel, "wxbot": failed})
        self.assertEqual({"accepted", "needs_user_action"}, {r["state"] for r in self.store.summary()["outbox"]})

    def test_new_channel_cutoff_and_etag_survive_restart(self):
        source = Mock()
        source.fetch.return_value = (payload(), '"synthetic-v1"')
        tick(self.store, {"smtp": self.channel}, source, NOW)
        self.channel.send.assert_not_called()  # newly enabled channel does not replay
        self.assertEqual('"synthetic-v1"', self.store.get("etag"))
        source.fetch.return_value = (None, '"synthetic-v1"')
        tick(self.store, {"smtp": self.channel}, source, NOW + timedelta(minutes=10))
        source.fetch.assert_called_with('"synthetic-v1"')

    def test_poll_floor_survives_repeated_once_and_restart(self):
        source = Mock()
        source.fetch.return_value = (payload(), '"synthetic-v1"')
        tick(self.store, {}, source, NOW)
        self.store.close()
        self.store = Store(self.directory, NOW + timedelta(seconds=1))
        result = tick(self.store, {}, source, NOW + timedelta(seconds=1))
        self.assertEqual("cache_wait", result["source"])
        source.fetch.assert_called_once()

    def test_disabled_then_reenabled_channel_does_not_catch_up(self):
        source = Mock()
        source.fetch.return_value = (payload(), None)
        tick(self.store, {"smtp": self.channel}, source, CUTOFF)
        tick(self.store, {}, source, CUTOFF + timedelta(minutes=10))
        tick(self.store, {"smtp": self.channel}, source, NOW)
        self.channel.send.assert_not_called()
        self.assertEqual(NOW, timestamp(self.store.get("enabled_at:smtp")))

    def test_announcement_then_future_confirmation_are_distinct(self):
        self.store.enqueue(notices(payload(), CUTOFF, NOW), ["smtp"])
        self.store.dispatch({"smtp": self.channel})
        p = payload()
        e = p["events"][0]
        e["status"] = e["presentation"]["status"] = "confirmed"
        e["confirmedAt"] = "2030-01-01T01:30:00Z"
        e["confirmationBasis"] = "source_post"
        e["posts"].append({"stage": "确认完成", "publishedAt": "2030-01-01T01:30:00Z", "url": "https://x.com/example/status/101"})
        self.store.enqueue(notices(p, CUTOFF, NOW), ["smtp"])
        self.store.dispatch({"smtp": self.channel})
        self.assertEqual(2, self.channel.send.call_count)

    def test_exclusive_lock_prevents_duplicate_worker(self):
        with ProcessLock(self.directory):
            with self.assertRaisesRegex(ValueError, "already_running"):
                with ProcessLock(self.directory):
                    pass

    def test_state_permissions(self):
        self.assertEqual(0, self.directory.stat().st_mode & 0o077)
        self.assertEqual(0, (self.directory / "outbox.sqlite3").stat().st_mode & 0o077)


class FakeResponse(io.BytesIO):
    def __init__(self, raw, headers=None):
        super().__init__(raw)
        self.headers = headers or {}


class SourceTests(unittest.TestCase):
    def test_gzip_etag_and_cache_ttl(self):
        source = AIHot()
        source.opener = Mock()
        source.opener.open.return_value = FakeResponse(gzip.compress(json.dumps(payload()).encode()),
             {"Content-Encoding": "gzip", "ETag": '"v1"', "Cache-Control": "public, s-maxage=900"})
        data, etag = source.fetch('"v0"')
        self.assertEqual(1, data["schemaVersion"])
        self.assertEqual('"v1"', etag)
        self.assertEqual(900, source.minimum_interval)
        request = source.opener.open.call_args[0][0]
        self.assertEqual('"v0"', request.get_header("If-none-match"))
        self.assertEqual("gzip", request.get_header("Accept-encoding"))

    def test_304(self):
        source = AIHot()
        source.opener = Mock()
        source.opener.open.side_effect = HTTPError("https://example.invalid", 304, "unchanged", {}, None)
        self.assertEqual((None, '"v1"'), source.fetch('"v1"'))

    def test_retry_after_minimum(self):
        source = AIHot()
        source.opener = Mock()
        source.opener.open.side_effect = HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "1800"}, None)
        with self.assertRaises(SourceError) as result:
            source.fetch()
        self.assertEqual(1800, result.exception.retry_after)

    def test_retry_after_http_date(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True)
        self.assertGreater(retry_seconds(future), 3500)
        self.assertEqual(600, retry_seconds("invalid"))

    def test_truncated_and_malformed_network_data(self):
        for failure in [IncompleteRead(b"partial"), EOFError(), UnicodeDecodeError("utf8", b"x", 0, 1, "bad")]:
            source = AIHot()
            source.opener = Mock()
            source.opener.open.side_effect = failure
            with self.assertRaises(SourceError):
                source.fetch()


class CLITests(unittest.TestCase):
    def test_fixture_dryrun_no_channel_no_state_no_network(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"NOTIFIER_STATE_DIR": tmp + "/state", "NOTIFIER_CHANNELS": "smtp"}, clear=True):
            with patch("codex_reset_notifier.cli.adapters") as adapter, patch("codex_reset_notifier.cli.AIHot") as source, patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(0, main(["run", "--fixture", str(FIXTURE), "--preview-fixture"]))
                self.assertEqual(1, json.loads(output.getvalue())["count"])
                adapter.assert_not_called()
                source.assert_not_called()
                self.assertFalse(Path(tmp, "state").exists())

    def test_fixture_live_is_rejected(self):
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            main(["run", "--live", "--fixture", str(FIXTURE)])

    def test_init_and_status_are_offline(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"NOTIFIER_STATE_DIR": tmp + "/state"}, clear=True):
            with patch("codex_reset_notifier.cli.AIHot") as source, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(0, main(["init"]))
                self.assertEqual(0, main(["status"]))
                source.assert_not_called()

    def test_every_channel_off_live_cannot_send(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"NOTIFIER_STATE_DIR": tmp + "/state"}, clear=True):
            with patch("codex_reset_notifier.cli.AIHot") as source, patch("codex_reset_notifier.channels.SMTPChannel.send") as smtp, patch("codex_reset_notifier.channels.WxBotChannel.send") as wx, patch("sys.stdout", new_callable=io.StringIO):
                source.return_value.fetch.return_value = (payload(), None)
                self.assertEqual(0, main(["run", "--live", "--once"]))
                smtp.assert_not_called()
                wx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
