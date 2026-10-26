"""Map public API metadata; never infer personal quota or promote an estimate."""
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, OverflowError):
        return None


def clean(value, limit=240):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def source_url(value):
    if not isinstance(value, str) or len(value) > 1000:
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme == "https" and parts.hostname in {"x.com", "twitter.com"}
                and not parts.username and not parts.password and parts.port in {None, 443}
                and "/status/" in parts.path and not parts.query and not parts.fragment):
            return value
    except ValueError:
        pass
    return None


@dataclass(frozen=True)
class Notice:
    key: str
    event_id: str
    stage: str
    source_at: str
    text: str


def notices(payload, cutoff, now):
    """One notice per event/stage. Edits and estimate clock changes do not re-send."""
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("unsupported_schema")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("missing_events")
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id or len(event_id) > 256:
            continue
        p = event.get("presentation")
        if not isinstance(p, dict):
            # Missing presentation cannot establish scope/status safely.
            continue
        stage = p.get("status")
        if not isinstance(stage, str) or stage not in {"announced", "confirmed"} or event.get("status") != stage:
            continue
        field = "confirmedAt" if stage == "confirmed" else "createdAt"
        at = timestamp(event.get(field))
        if at is None or at <= cutoff or at > now:
            continue
        if stage == "confirmed" and event.get("confirmationBasis") != "source_post":
            continue
        posts = event.get("posts")
        if not isinstance(posts, list):
            continue
        candidates = []
        expected_stage = "确认完成" if stage == "confirmed" else "预告"
        for post in posts:
            if not isinstance(post, dict):
                continue
            published = timestamp(post.get("publishedAt"))
            url = source_url(post.get("url"))
            if (published is not None and cutoff < published <= at and url
                    and post.get("stage") == expected_stage):
                candidates.append((published, url))
        if not candidates:
            continue
        published, url = max(candidates)
        label = clean(event.get("displayLabel")) or "Codex 重置（形式未明确）"
        status_label = "📣 重置预告（尚未确认完成）" if stage == "announced" else "✅ 来源已确认重置"
        scope = (clean(p.get("scopeLabel")) or clean(event.get("scope"))
                 if p.get("scopeKnown") is True else "范围尚未明确")
        lines = [f"{status_label} · AIHot", f"📌 {label}", f"👥 {scope or '范围尚未明确'}",
                 f"🕒 来源时间：{published.isoformat()}"]
        estimate = event.get("estimate")
        if isinstance(estimate, dict):
            start, end = timestamp(estimate.get("from")), timestamp(estimate.get("through"))
            if start and end and start <= end:
                basis_value = estimate.get("basis")
                basis = {"source": "来源推算", "model": "模型推算"}.get(
                    basis_value if isinstance(basis_value, str) else "", "依据未明确")
                lines.append(f"⏳ 预计窗口：{start.isoformat()} 至 {end.isoformat()}（{basis}，不是完成确认）")
        lines += ["ℹ️ 这是公开消息状态，不代表你的个人额度已恢复。", f"🔗 原帖：{url}",
                  "📡 数据：AIHot https://aihot.news/codex-reset"]
        yield Notice(f"{event_id}:{stage}", event_id, stage, published.isoformat(), "\n".join(lines))
