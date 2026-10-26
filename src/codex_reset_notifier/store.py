"""Private local outbox. Claim before I/O, quarantine ambiguous crash outcomes."""
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path


def private_directory(directory, create=False):
    path = Path(directory)
    if path.is_symlink():
        raise ValueError("state_symlink")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("state_directory_must_be_owned_private_0700")
    return path


def private_file(path, create=False):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("state_symlink")
    if create:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("state_file_must_be_owned_private_0600")
    return path


def readonly_status(directory):
    """Inspect a running worker without locks, creation, migration, or recovery."""
    directory = Path(directory)
    if not directory.exists() and not directory.is_symlink():
        return {"initialized": False}
    directory = private_directory(directory)
    path = directory / "outbox.sqlite3"
    if not path.exists() and not path.is_symlink():
        return {"initialized": False}
    private_file(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()
        result = {"initialized": True, "cutoff": row[0] if row else None,
                  "outbox": [{"channel": r[0], "state": r[1], "count": r[2]} for r in db.execute(
                      "SELECT channel,state,count(*) FROM outbox GROUP BY channel,state")]}
    return result


class Store:
    def __init__(self, directory, now):
        self.directory = private_directory(directory, create=True)
        db_path = private_file(self.directory / "outbox.sqlite3", create=True)
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
    def __init__(self, directory, filename="worker.lock"):
        self.path = Path(directory) / filename

    def __enter__(self):
        import fcntl
        private_file(self.path, create=True)
        self.handle = open(self.path, "a", encoding="utf-8")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise ValueError("worker_already_running") from None
        return self

    def __exit__(self, *_):
        self.handle.close()
