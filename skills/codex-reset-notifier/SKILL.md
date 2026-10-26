---
name: codex-reset-notifier
description: Install, configure, inspect, or troubleshoot the independent AIHot Codex reset text notification worker for a user's own SMTP email or wx-bot WeChat channel. Use when the user wants recurring Codex reset announcements without Dot or a continuously running model. This is a community integration, not the official aihot query skill.
---

# Codex Reset Notifier

Use the standalone Python worker from `https://github.com/heyxiafei/codex-reset-notifier`. It polls public AIHot metadata and sends deterministic text; Codex helps configure it but need not remain running. Read the repository README and the relevant implementation before configuring a channel. Do not replace another `aihot` skill.

## Identify the actual route

Keep AIHot's public data endpoint, this worker, the optional third-party wx-bot service, the WeChat/ClawBot conversation entry, Tencent iLink, and independent SMTP separate. A custom Python Dot↔WeChat bridge may call iLink directly; borrowing a durable inbox design does not establish a wx-bot runtime dependency. Do not inspect or migrate an existing private bridge for this installation. The public worker implements SMTP TLS and the wx-bot local API; it does not include a direct iLink adapter or a Dot bridge. Receiving a message through another program is not real-delivery evidence for this package.

## Install and prove the safe baseline

1. Work in a new user-selected directory. Check existing installations before cloning or copying; do not overwrite an unrelated repository, production bridge, credentials, ledger, or chat history.
2. Clone the repository, create `.venv`, and run `.venv/bin/python -m pip install .`. Requires macOS/Linux and Python 3.9+; no model API key is used.
3. Run `.venv/bin/python -m unittest discover -s tests -v` and `.venv/bin/codex-reset-notifier run --once --fixture tests/fixtures/recent.json --preview-fixture`. Fixture preview is offline, dry-run, uses its synthetic clock, and does not touch the live ledger. Report actual results, not assumed success.
4. Create a new `.env` from `.env.example` with a private umask (0600); stop if `.env` already exists or is a symlink rather than overwriting it. Keep all channels off initially. Use `--env-file .env init`, `status`, then `run --once` on the CLI. These commands do not enable background services or send test notifications. Explain the persistent future-only cutoff before activation. Normal dry-run can maintain a separate private HTTP preview cache with ETag and backoff but never changes the sending outbox; fixture simulation remains fully offline and stateless. Use a fixed working directory or explicit absolute state path when later running continuously.

If only this Skill folder was installed, clone the worker repository separately; the skill is not the executable. The canonical instructions are at `https://github.com/heyxiafei/codex-reset-notifier/blob/main/README.md`. Do not run a remote shell installer.

## Configure only the authorized channel

- **SMTP:** Let the user enter their own TLS SMTP credentials directly into the private local configuration or approved secret store. Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS` (`ssl` or `starttls`), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER`, and `SMTP_RECIPIENT`. Do not request secrets in chat or echo them in commands/logs. Gmail OAuth is not implemented; do not borrow an existing connector or request whole-mailbox permissions.
- **WeChat:** The separately installed dependency is `lawrencewzen/wx-bot` v0.5.1, commit `4eb9fabcf994b4e491c6b7284d73528652266849`. Its owner must authorize pairing and first send an inbound message to establish context. Set `WXBOT_BASE_URL` to the protected numeric loopback endpoint, plus `WXBOT_TOKEN`, `WXBOT_BINDING`, and `WXBOT_RECIPIENT`. Do not copy another bridge's private data. Do not install, pair, expose the API, or send test messages without scope authorization. This route needs neither Dot nor OpenClaw.
- Set `NOTIFIER_CHANNELS=smtp`, `wxbot`, or `smtp,wxbot` only after the user selects and configures those destinations. Empty means off. Keep runtime `NOTIFIER_STATE_DIR` private and persistent, out of version control. Do not reset it to solve an error.

Use the user's existing authorization; do not invent repeated approvals. Live delivery and background-service installation must be within the requested scope. Provide a concrete command or service definition for review before any still-unapproved activation. `run --live` explicitly permits selected channels to send; default `run` is dry-run. Installing this Skill is not permission to install or start launchd, systemd, Docker, wx-bot, or another daemon.

## Preserve event and delivery meaning

- Poll `https://aihot.news/api/v1/codex-resets/recent` at least 600 seconds apart, using ETag and server cache/backoff requirements. No AIHot API key or general webhook is needed.
- The global cutoff is established by `init` or first live start; each newly enabled channel gets its own cutoff. Do not backfill before activation. Ordinary restart retains those cutoffs and may catch up on unseen events from downtime still available in the recent API window. A channel observed disabled and then re-enabled starts again at the new activation time. Preserve the ledger; edits to estimate windows do not create another notice. `status` is read-only and usable while the worker holds its process lock.
- Trust `displayLabel` and `presentation.scopeKnown`, not the old `label`. Only send `announced`/`confirmed`; do not expand subscriptions to `in_progress`, `expired_unconfirmed`, or `likely_completed`. Never promote a prediction to confirmation, infer universal scope, or say personal quota is restored. Preserve source time, the source/model/source_day/history estimate basis/window, and validated original-post links. Missing evidence is not permission to infer it.
- `accepted` is provider acceptance, not delivery. `unknown` includes sending-time interruption or timeout; never blindly retry it. Restart quarantines `sending` as `unknown`. First version has no automatic replay command.
- For `needs_user_action`, including `ret=-2`, explain the required user recovery. Do not generate self-messages, fake activity, or keepalive traffic. Context expiration is not a fixed guaranteed 24-hour rule.
- Use a dedicated wx-bot instance and pull binding, with this worker as its exclusive consumer. Before each pull, ack or send, verify `GET /v1/status` with the exact unique binding, logged_in=true, expired=false, delivery=pull and allowlist exactly equal to the configured recipient; fail closed otherwise. Also validate message `from_wxid` before ack. Never log owner identifiers. Live mode drains up to 500 events about every 60 seconds, discards chat content, and reports only fixed allowlisted alert codes, never alert message/hint text. Unknown alerts are not acknowledged. Tell the owner that acknowledgement discards inbound events; do not attach a shared binding. An `acked` count is inbox maintenance, not proof of outbound delivery.

## Finish with evidence

Report installed version/path, offline tests, configured channel names (no recipients/secrets), cutoff, dry-run or live mode, and whether a service was actually installed. State remaining real-delivery validation explicitly. Text only; do not claim image support, zero configuration, installation-implies-delivery, permanent delivery, exactly-once, or successful WeChat/email delivery based on mocks, a paired ClawBot entry, or receipts from another bridge.

The code's MIT license does not cover AIHot data or services. Read `https://aihot.news/terms`: personal non-commercial, public-interest non-commercial, and organization-internal uses are free; external commercial products/services and redistribution may require prior written permission. Preserve source links, never imply official affiliation, and never upload local state or real test receipts.
