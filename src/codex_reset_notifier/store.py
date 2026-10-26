"""Private local outbox. Claim before I/O, quarantine ambiguous crash outcomes."""
import os
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, directory, now):
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise ValueError("state_symlink")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.stat().st_mode & 0o077:
            raise ValueError("state_directory_must_be_private_0700")
        db_path = self.directory / "outbox.sqlite3"
        if db_path.is_symlink():
            raise ValueError("state_symlink")
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if db_path.stat().st_mode & 0o077:
            raise ValueError("state_database_must_be_private_0600")
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox (
            key TEXT NOT NULL, channel TEXT NOT NULL, text TEXT NOT NULL,
            state TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY(key,channel));
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('cutoff',?)", (now.isoformat(),))

    def get(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def recover(self):
        # Caller holds the process lock. A crash after provider acceptance is ambiguous.
        with self.db:
            self.db.execute("UPDATE outbox SET state='unknown',code='interrupted_send' WHERE state='sending'")

    def enqueue(self, notices, channels):
        with self.db:
            for notice in notices:
                for channel in channels:
                    self.db.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,?,'pending','queued')",
                                    (notice.key, channel, notice.text))

    def dispatch(self, channels):
        rows = self.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY rowid").fetchall()
        results = []
        for row in rows:
            if row["channel"] not in channels:
                continue
            with self.db:
                claimed = self.db.execute("UPDATE outbox SET state='sending',code='claimed' WHERE key=? AND channel=? AND state='pending'",
                                          (row["key"], row["channel"])).rowcount
            if not claimed:
                continue
            try:
                result = channels[row["channel"]].send(row["text"])
                state, code = result.state, result.code
                if state not in {"accepted", "unknown", "needs_user_action", "failed"}:
                    state, code = "unknown", "invalid_adapter_result"
            except Exception:
                state, code = "unknown", "adapter_exception"
            with self.db:
                self.db.execute("UPDATE outbox SET state=?,code=? WHERE key=? AND channel=?",
                                (state, code, row["key"], row["channel"]))
            results.append({"channel": row["channel"], "state": state, "code": code})
        return results

    def summary(self):
        return {"cutoff": self.get("cutoff"), "outbox": [dict(r) for r in self.db.execute(
            "SELECT channel,state,count(*) AS count FROM outbox GROUP BY channel,state")]}

    def close(self):
        self.db.close()


class ProcessLock:
    def __init__(self, directory):
        self.path = Path(directory) / "worker.lock"

    def __enter__(self):
        import fcntl
        if self.path.is_symlink():
            raise ValueError("lock_symlink")
        self.handle = open(self.path, "a", encoding="utf-8")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise ValueError("worker_already_running") from None
        return self

    def __exit__(self, *_):
        self.handle.close()
