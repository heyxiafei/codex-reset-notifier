"""Explicit opt-in transports; normal and fixture previews never send."""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .events import notices, timestamp
from .source import AIHot, MAX_BYTES, SourceError
from .store import ProcessLock, Store, readonly_status, private_file
from .preview import cached_preview


def load_env(path):
    if path is None:
        return
    config = private_file(Path(path))
    if config.stat().st_size > 32768:
        raise ValueError("env_file_too_large")
    allowed = {"NOTIFIER_CHANNELS", "NOTIFIER_STATE_DIR", "NOTIFIER_POLL_SECONDS",
               "SMTP_HOST", "SMTP_PORT", "SMTP_TLS", "SMTP_USERNAME", "SMTP_PASSWORD",
               "SMTP_SENDER", "SMTP_RECIPIENT", "WXBOT_BASE_URL", "WXBOT_TOKEN",
               "WXBOT_BINDING", "WXBOT_RECIPIENT"}
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key.strip() not in allowed:
            raise ValueError("unsupported_env_key")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("invalid_env_quotes")
            value = value[1:-1]
        # No shell evaluation, variable interpolation, or secret logging.
        os.environ.setdefault(key.strip(), value)


def configured_channels():
    names = [x.strip() for x in os.environ.get("NOTIFIER_CHANNELS", "").split(",") if x.strip()]
    if len(names) != len(set(names)) or set(names) - {"smtp", "wxbot"}:
        raise ValueError("invalid_channels")
    return names


def adapters(names):
    from .channels import SMTPChannel, WxBotChannel
    def required(name):
        value = os.environ.get(name)
        if not value or value == "replace-locally" or ".invalid" in value:
            raise ValueError("channel_configuration_incomplete")
        return value
    result = {}
    for name in names:
        if name == "smtp":
            result[name] = SMTPChannel(
                host=required("SMTP_HOST"), port=int(os.environ.get("SMTP_PORT", "465")),
                username=required("SMTP_USERNAME"), password=required("SMTP_PASSWORD"),
                sender=required("SMTP_SENDER"), recipient=required("SMTP_RECIPIENT"),
                tls_mode=os.environ.get("SMTP_TLS", "ssl"))
        if name == "wxbot":
            result[name] = WxBotChannel(base_url=os.environ.get("WXBOT_BASE_URL", "http://127.0.0.1:18790"),
                token=required("WXBOT_TOKEN"), binding=required("WXBOT_BINDING"),
                recipient=required("WXBOT_RECIPIENT"))
    return result


def tick(store, channels, source, now, minimum_interval=600):
    # A newly activated channel starts now even if this database was initialized earlier.
    previous = set((store.get("active_channels") or "").split(","))
    for name in channels:
        if name not in previous or store.get("enabled_at:" + name) is None:
            store.set("enabled_at:" + name, now.isoformat())
            with store.db:
                store.db.execute("UPDATE outbox SET state='failed',code='channel_reenabled_skip' WHERE channel=? AND state='pending'", (name,))
    store.set("active_channels", ",".join(sorted(channels)))
    due = timestamp(store.get("next_poll_at"))
    if due and now < due:
        return {"source": "cache_wait", "results": store.dispatch(channels)}
    # Persist before network I/O, including failed polls and rapid process restarts.
    store.set("next_poll_at", (now + timedelta(seconds=minimum_interval)).isoformat())
    try:
        payload, etag = source.fetch(store.get("etag"))
    except SourceError as exc:
        store.set("next_poll_at", (now + timedelta(seconds=max(minimum_interval, exc.retry_after))).isoformat())
        raise
    cache_minimum = getattr(source, "minimum_interval", 600)
    if type(cache_minimum) is not int:
        cache_minimum = 600
    store.set("next_poll_at", (now + timedelta(seconds=max(minimum_interval, cache_minimum))).isoformat())
    if payload is not None:
        for name in channels:
            cutoff = max(timestamp(store.get("cutoff")), timestamp(store.get("enabled_at:" + name)))
            store.enqueue(notices(payload, cutoff, now), [name])
        # Validate schema even with every channel off.
        if not channels:
            list(notices(payload, timestamp(store.get("cutoff")), now))
        store.set("etag", etag or "")
    results = store.dispatch(channels)
    return {"source": "not_modified" if payload is None else "ok", "results": results}


def preview(args, now, directory, interval=600):
    cutoff = now
    source_state, checked_at = "fixture", None
    if args.fixture:
        path = Path(args.fixture)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("fixture_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if args.preview_fixture:
            cutoff, now = timestamp(payload.get("_testCutoff")), timestamp(payload.get("_testNow"))
            if cutoff is None or now is None or cutoff > now:
                raise ValueError("fixture_clock_missing")
    if not args.preview_fixture:
        summary = readonly_status(directory)
        if summary.get("initialized"):
            cutoff = timestamp(summary.get("cutoff"))
            if cutoff is None:
                raise ValueError("invalid_cutoff")
    if not args.fixture:
        payload, source_state, checked_at = cached_preview(directory, AIHot(), cutoff, now, interval)
    found = list(notices(payload, cutoff, now))
    print(json.dumps({"dry_run": True, "simulation": args.preview_fixture, "count": len(found),
                      "cutoff": cutoff.isoformat(), "source": source_state, "checked_at": checked_at,
                      "messages": [n.text for n in found]}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Future-only AIHot text notifier; dry-run by default.")
    parser.add_argument("--env-file", help="Private 0600 file; no shell evaluation; environment wins")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create private state and future-only cutoff; no network or send")
    commands.add_parser("status", help="Show only cutoff and aggregate delivery states")
    run = commands.add_parser("run", help="Preview once; --live explicitly enables configured transports")
    run.add_argument("--once", action="store_true", help="One poll only (also default for dry-run)")
    run.add_argument("--live", action="store_true", help="Allow configured channels to send; no test sends")
    run.add_argument("--fixture", help="Local synthetic JSON; always dry-run")
    run.add_argument("--preview-fixture", action="store_true", help="Use fixture simulation clock; never writes state")
    args = parser.parse_args(argv)
    if args.command == "run" and ((args.fixture and args.live) or (args.preview_fixture and not args.fixture)):
        parser.error("fixtures require dry-run; --preview-fixture requires --fixture")
    store = None
    try:
        load_env(args.env_file)
        names = configured_channels()
        interval = int(os.environ.get("NOTIFIER_POLL_SECONDS", "600"))
        if interval < 600:
            raise ValueError("poll_interval_minimum_600_seconds")
        now = datetime.now(timezone.utc)
        directory = Path(os.environ.get("NOTIFIER_STATE_DIR", "./state"))
        if args.command == "run" and not args.live:
            return preview(args, now, directory, interval)
        if args.command == "status":
            print(json.dumps(readonly_status(directory)))
            return 0
        channel_map = adapters(names) if args.command == "run" else {}
        store = Store(directory, now)
        with ProcessLock(directory):
            if args.command == "init":
                print(json.dumps(store.summary()))
                return 0
            store.recover()
            source = AIHot()
            next_poll = 0
            while True:
                if "wxbot" in channel_map:
                    drained = channel_map["wxbot"].drain_inbox()
                    print(json.dumps({"inbox": drained.state, "code": drained.code,
                                      "acked": drained.acked}), flush=True)
                if time.monotonic() >= next_poll:
                    delay = interval
                    try:
                        print(json.dumps(tick(store, channel_map, source, datetime.now(timezone.utc), interval)), flush=True)
                    except (ValueError, OSError, json.JSONDecodeError) as exc:
                        # Provider errors and secrets are deliberately not logged.
                        if isinstance(exc, SourceError):
                            delay = max(delay, exc.retry_after)
                        print(json.dumps({"source": "error", "code": "poll_failed"}), flush=True)
                        if args.once:
                            return 1
                    if args.once:
                        return 0
                    next_poll = time.monotonic() + max(delay, source.minimum_interval)
                # Drain leases regularly without increasing public API polling frequency.
                time.sleep(min(60, max(0.01, next_poll - time.monotonic())))
    except (ValueError, OSError, UnicodeError, sqlite3.Error, json.JSONDecodeError):
        print("Configuration or local I/O failed; check permissions and required fields. No secrets logged.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    finally:
        if store:
            store.close()
