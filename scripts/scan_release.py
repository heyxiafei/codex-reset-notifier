"""Scan staged text and paths before publishing; report rules, never secret values."""
import re
import subprocess
import sys
from pathlib import PurePosixPath


PATTERNS = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE" + r" KEY-----"),
    "github_token": re.compile(r"\b(?:gh[pousr]_" + r"[A-Za-z0-9]{20,}|github_pat_" + r"[A-Za-z0-9_]{20,})\b"),
    "cloud_key": re.compile(r"\bAKIA" + r"[A-Z0-9]{16}\b"),
    "machine_home_path": re.compile(r"/(?:Users|home)/" + r"[A-Za-z0-9._-]+/"),
    "private_wechat_id": re.compile(r"\bwxid_" + r"[A-Za-z0-9]{6,}\b"),
}
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def main():
    paths = subprocess.check_output(["git", "ls-files", "--cached", "-z"]).decode().split("\0")
    failures = []
    checked = 0
    for path in filter(None, paths):
        entry = PurePosixPath(path)
        if (entry.name.startswith(".env") and entry.name != ".env.example"
                or entry.suffix in {".db", ".sqlite", ".sqlite3", ".log", ".pyc"}
                or any(part in {"state", "proof", ".venv", "__pycache__"} for part in entry.parts)):
            failures.append((path, "private_runtime_path"))
        mode = subprocess.check_output(["git", "ls-files", "-s", "--", path]).decode().split()[0]
        if mode == "120000":
            failures.append((path, "symlink"))
        raw = subprocess.check_output(["git", "show", ":" + path])
        checked += 1
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            failures.append((path, "non_text"))
            continue
        for rule, pattern in PATTERNS.items():
            if pattern.search(content):
                failures.append((path, rule))
        if any(not m.group(0).endswith("@example.invalid") for m in EMAIL.finditer(content)):
            failures.append((path, "non_placeholder_email"))
    for path, rule in failures:
        print(f"FAIL {path}: {rule}")
    print(f"Scanned {checked} staged text files; {len(failures)} finding(s).")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
