"""Separate private HTTP cache for dry-runs; never opens the sending outbox."""
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

from .events import notices, timestamp
from .source import SourceError
from .store import ProcessLock, private_directory, private_file


def cached_preview(directory, source, cutoff, now, interval=600):
    directory = private_directory(directory, create=True)
    path = private_file(directory / "preview-cache.sqlite3", create=True)
    with ProcessLock(directory, "preview.lock"), closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        def get(key):
            row = db.execute("SELECT value FROM cache WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        def put(key, value):
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (key, value))
        due, raw = timestamp(get("next_poll_at")), get("payload")
        if due and now < due:
            if raw is None:
                raise SourceError()
            return json.loads(raw), "cached", get("checked_at")
        # Commit the backoff before I/O so failures and restarts obey the floor.
        put("next_poll_at", (now + timedelta(seconds=interval)).isoformat())
        db.commit()
        try:
            payload, etag = source.fetch(get("etag"))
        except SourceError as exc:
            put("next_poll_at", (now + timedelta(seconds=max(interval, exc.retry_after))).isoformat())
            db.commit()
            raise
        minimum = getattr(source, "minimum_interval", 600)
        minimum = minimum if type(minimum) is int else 600
        put("next_poll_at", (now + timedelta(seconds=max(interval, minimum))).isoformat())
        if payload is not None:
            list(notices(payload, cutoff, now))  # validate before retaining ETag
            put("payload", json.dumps(payload, ensure_ascii=False))
            put("etag", etag or "")
        elif raw is None:
            raise SourceError()
        put("checked_at", now.isoformat())
        db.commit()
        return payload if payload is not None else json.loads(raw), "ok" if payload is not None else "not_modified", now.isoformat()
