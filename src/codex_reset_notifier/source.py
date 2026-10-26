"""Read-only public API; persist ETag only after successful processing."""
import gzip
import io
import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import urllib.error
import urllib.request

API_URL = "https://aihot.news/api/v1/codex-resets/recent"
MAX_BYTES = 2_000_000


class SourceError(ValueError):
    def __init__(self, retry_after=600):
        super().__init__("source_http_error")
        self.retry_after = max(600, retry_after)


def retry_seconds(value):
    if value.isdigit():
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo:
            return max(600, math.ceil((date - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        pass
    return 600


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class AIHot:
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect())
        self.minimum_interval = 600

    def _cache_interval(self, headers):
        match = re.search(r"(?:^|,)\s*s-maxage=(\d+)", headers.get("Cache-Control", ""))
        self.minimum_interval = max(600, int(match[1])) if match else 600

    def fetch(self, etag=None):
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip",
                   "User-Agent": "codex-reset-notifier/0.1"}
        if etag and len(etag) < 512 and "\r" not in etag and "\n" not in etag:
            headers["If-None-Match"] = etag
        request = urllib.request.Request(API_URL, headers=headers)
        try:
            with self.opener.open(request, timeout=20) as response:
                self._cache_interval(response.headers)
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("source_too_large")
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                        raw = stream.read(MAX_BYTES + 1)
                    if len(raw) > MAX_BYTES:
                        raise ValueError("source_too_large")
                return json.loads(raw), response.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                self._cache_interval(exc.headers)
                return None, etag
            retry = exc.headers.get("Retry-After", "600") if exc.code in {429, 503} else "600"
            raise SourceError(retry_seconds(retry)) from None
        except (OSError, HTTPException, EOFError, UnicodeError, ValueError):
            raise SourceError() from None
