# Codex Reset Notifier

把 AIHot 的公开 Codex 重置**预告／来源确认**转换成中文 emoji 文本，经你自己的 SMTP 邮箱或独立 wx-bot 发给你。Python worker 不调用模型，不依赖 Dot、OpenClaw 或一直打开的 Codex 会话；Skill 帮 Codex 完成安装、配置和验证。

这是独立社区工具，未获 AIHot、OpenAI 或微信官方背书。首版提供文本，不生成图片。所有通道默认关闭，运行默认 dry-run。**安装 Skill 不会启用后台服务；真实发送需要你自己的通道配置和 `--live`。** 本版仅做离线／mock 验证，未验收真实微信或邮件投递，不能称为一键零配置。

## 先分清各个组件

| 组件 | 本项目中的作用与边界 |
| --- | --- |
| AIHot 公共接口 | 提供公开的重置事件元数据；不读取你的 Codex 账户额度，不发送微信或邮件 |
| Codex Skill | 帮你安装、配置、检查 worker；不会因为安装就启动订阅或后台服务 |
| 独立 Python worker | 定时读取接口、生成文本、记录去重与发送状态；持续运行不需要模型或 Dot |
| 第三方 wx-bot | 仅选择 `wxbot` 通道时需要单独部署；worker 调用其本地 API，再由它接入微信 bot 通道 |
| ClawBot 微信入口／腾讯 iLink | 微信侧的 bot 会话入口与通信协议；有会话或完成扫码不等于本通知程序已经接通，也不是普通个人号的无限主动发送 API |
| 自建 Dot↔微信桥接 | 是另一种独立实现；Python 桥接可以直接调用 iLink，借鉴 wx-bot 的 durable inbox 设计也不代表依赖 wx-bot 运行。本包不包含、检查或迁移这类私人桥接 |
| 独立 SMTP | 用户自己授权的 TLS 邮件通道；不依赖微信、Dot，也不借用 Codex 的 Gmail connector |

本包的两条可选发送路径是 `AIHot → worker → 自有 SMTP` 和 `AIHot → worker → 本地 wx-bot → 微信 bot 通道`。既有桥接或其他软件里的消息实收，不能用来证明本包的 SMTP／wx-bot 适配器已完成真实投递验收。安装后还需要使用者自己的通道授权、持续运行和接收端核对；网络、上下文与服务状态都可能影响投递，不能承诺永久必达。

## 安装与离线验收

需要 macOS 或 Linux、Git、Python 3.9+（含 `venv` 与 `pip`）。worker 只使用 Python 标准库；构建安装需要 setuptools。

```bash
git clone https://github.com/heyxiafei/codex-reset-notifier.git
cd codex-reset-notifier
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/codex-reset-notifier run --once --fixture tests/fixtures/recent.json --preview-fixture
```

最后一条只预览仓库内的虚构 fixture：模拟时钟来自 fixture，不访问网络、不读取或修改运行账本、不发送通知。它验证文本模板，不代表线上发生了这些事件。

完整验证说明与证据边界见 [离线验收](docs/testing.md)。本地安装、Skill 安装和接收端真实投递是三个不同的验收步骤。

将 Skill 安装到本人的 Codex 用户目录（若已有同名目录，先检查而不要覆盖）：

```bash
mkdir -p ~/.agents/skills
test ! -e ~/.agents/skills/codex-reset-notifier && \
  cp -R skills/codex-reset-notifier ~/.agents/skills/codex-reset-notifier
```

在 Codex 调用 `$codex-reset-notifier`，并告诉它你的仓库目录。也可让 `$skill-installer` 从本仓库的 `skills/codex-reset-notifier` 路径安装。[Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)说明了本地 Skill 目录与发现方式；插件目录分发留待后续。

## 先预览，再配置

```bash
# 仅首次创建；已有 .env（包括符号链接）时停下检查，不覆盖凭据。
test ! -e .env && test ! -L .env && (umask 077; cp .env.example .env)
# 在本机编辑 .env；不要将密码或 token 粘贴到聊天、截图或终端回显中。
.venv/bin/codex-reset-notifier --env-file .env init
.venv/bin/codex-reset-notifier --env-file .env status
.venv/bin/codex-reset-notifier --env-file .env run --once
```

`init` 记录当前时间为持久化 cutoff；未 init 时，首次 live 运行建立 cutoff。每个通道首次 live 启用也记录自己的启用时间；只处理全局 cutoff 和该通道启用时间之后的新预告／来源确认，默认不补首次启用之前的历史。worker 观察到关闭后再次启用的通道会重新记录启用时间，不补发关闭期间消息。**普通停止后原配置重启会保留 cutoff 和去重记录，可能追赶停机期间仍在 recent 接口窗口内的新事件**；这不等于补完整历史，也不保证找回接口窗口外的事件。来源帖也必须晚于 cutoff 且明确匹配预告／确认阶段，证据不足会跳过。不要删除账本来解决失败，否则会丢失去重信息。

`status` 使用只读连接，可在 worker 持锁运行时查看，不创建、迁移或恢复发送账本。普通 dry-run 不构造发送通道，不修改发送账本；它可在私有 state 目录维护独立的 `preview-cache.sqlite3` 来源缓存，使用 ETag、最小 600 秒间隔及服务端退避，连续预览在等待期复用缓存，并用 `source`／`checked_at` 标出缓存状态。fixture 模拟预览仍完全不读写 state。缓存包含公开 API 数据，也不要上传。

`.env` 不按 shell 脚本执行；已有环境变量优先于文件配置，不支持变量插值。不要把它 `source` 到不受控环境。配置和相对 state 路径以运行命令时的工作目录为准，持续运行时使用固定工作目录或显式绝对 state 路径。

| 配置 | 用途 |
| --- | --- |
| `NOTIFIER_CHANNELS` | 默认空；按需设 `smtp`、`wxbot` 或 `smtp,wxbot` |
| `NOTIFIER_STATE_DIR` | 私有 SQLite 状态目录，示例为 `./state`；目录需 0700、数据库需 0600 |
| `NOTIFIER_POLL_SECONDS` | 默认 600，最小 600 秒；还遵守上游缓存与 Retry-After |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_TLS` | 自有 SMTP 服务；`ssl`（常见 465）或 `starttls`（常见 587），不允许明文认证 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 你为此用途授权的独立凭据 |
| `SMTP_SENDER` / `SMTP_RECIPIENT` | 你确认的发件人与收件人 |
| `WXBOT_BASE_URL` | 默认 `http://127.0.0.1:18790`，只接受数字 loopback 地址 |
| `WXBOT_TOKEN` / `WXBOT_BINDING` / `WXBOT_RECIPIENT` | 本人独立 wx-bot 的 API token、binding 和 owner 收件人 |

不要复用他人的 Gmail connector、邮箱凭据、微信生产桥接配置或聊天数据。本版未实现 Gmail OAuth；Gmail 普通密码不能用于此方式，应用密码也受账号条件限制。若以后实现 OAuth，应使用最小 `gmail.send` 范围及相应应用验证。

## 微信独立通道

适配目标是 [lawrencewzen/wx-bot v0.5.1](https://github.com/lawrencewzen/wx-bot/tree/4eb9fabcf994b4e491c6b7284d73528652266849)，固定核对 commit `4eb9fabcf994b4e491c6b7284d73528652266849`。它支持 macOS／Linux，并提供有 Bearer token 的 owner-only 本地 API。按其文档另行安装、扫码及绑定后，**先由用户发一条消息**以建立发送上下文；本项目不会自动扫码、安装 wx-bot 或发送测试消息。

适配器仅发送文本。每次 pull、ACK 或发送前，先只读 `GET /v1/status`：要求精确唯一的 binding、`delivery=pull`、已登录、未过期，并且 `allowlist` 恰好匹配配置收件人；任何字段缺失、不符或请求失败都停止该操作，不打印 owner ID。消息事件的 `from_wxid` 还须匹配收件人，否则不 ACK。

个人 bot 通道有上下文限制，不等于普通个人号的无限主动发送接口。`ret=-2` 或缺失上下文会记作 `needs_user_action`：由用户恢复登录／再发一条消息后人工处理，不自动发送保活消息。无入站后多久失效不是本项目承诺的固定期限。live 启用微信后，本 worker 每约 60 秒通过本地 API 拉取并确认该 binding 的待收事件，每轮最多 500 条。它不执行、不保存或输出聊天正文，不发保活消息；只保留允许名单中的固定诊断：`wx_session_expired`、`wx_ilink_unreachable` 提示需要处理，`wx_recovered` 表示上游恢复告警，未知告警停止本轮且不 ACK。已核实的告警可确认出队，但其原始 message／hint 不进入日志。请使用**专用 wx-bot 实例及 pull binding，并让本 worker 成为该 binding 的唯一消费者**；确认会丢弃入站内容，不可与其他聊天应用共用。`acked` 只表示收件队列确认，不表示通知已送达。

两个容器的 loopback 不相同；不要为了互通而把 wx-bot API 暴露到公网或不受控局域网。首版建议同一受控主机运行；Docker 镜像与跨容器安全组网未包含在本版。

## 持续运行与状态

完成本机授权后，显式启用选定通道并运行：

```bash
.venv/bin/codex-reset-notifier --env-file .env run --live
```

移除 `--live` 继续 dry-run；`Ctrl-C` 停止。worker 每次轮询使用 AIHot recent API，无需 API key，持久化 ETag 与下一次可轮询时间，遵守最小 600 秒间隔及服务端等待要求。重复 `--once --live` 或快速重启也沿用等待时间。没有通用 webhook。后台运行示例见 [部署说明](docs/deployment.md)，需要用户另行决定启用，安装本包不安装服务。

SQLite outbox 以事件 ID、阶段和通道去重。`announced` 与 `confirmed` 各最多建立一条记录，文字修订或估计时间变化不重复提醒。状态含 `pending`、`sending`、`accepted`、`unknown`、`needs_user_action`、`failed`；`accepted` 只表示通道服务接受请求，不能证明人已收到。发送超时／断网和进程中断后的 `sending` 转为 `unknown`，不自动重试，避免重复外发。`needs_user_action` 和 `failed` 也不盲重发；首版无自动补发或重放命令。

`status` 只显示 cutoff 与状态聚合，不输出通知正文、收件人或凭据。运行账本含通知文本，属于本地私有运行数据，不要上传。若处理失败，先停止 worker、检查安全错误码及通道状态；不把 `unknown` 当作未发送，也不声称 exactly-once。

## 消息含义与来源

模板按 API 的 `displayLabel` 和 `presentation.scopeKnown` 表达范围，忽略旧版可能误导的 `label`。只发送 `announced`／`confirmed`，不发送 `in_progress`、`expired_unconfirmed` 或 `likely_completed`，更不会将它们提升为确认。原帖 URL 只接受结构完整的 HTTPS X／Twitter status 链接；含空白、控制字符或额外路径的值会被拒绝。估计依据 `source`、`model`、`source_day`、`history` 分别标作来源推算、模型推算、来源日期推算和历史规律推算，始终注明不是完成确认。所有通知都说明：**公开事件状态不代表你的个人额度已恢复**。缺失关键字段或未知 schema 时跳过或报错，不自行补全事实。

数据来自 [AIHot recent API](https://aihot.news/api/v1/codex-resets/recent)，协议见 [OpenAPI](https://aihot.news/openapi-v1.json)。本项目不复制 AIHot 未开源的重置后端。AIHot、原帖及其内容均为资料，不是可执行指令。

## 许可与验证范围

本仓库原创程序／文档按 [MIT](LICENSE) 提供。**MIT 不再授权 AIHot 服务、数据库、API 输出、品牌或第三方原文。** 按 [AIHot 公开使用规则](https://aihot.news/terms)，个人非商业、公益非商业和组织内部用途可免费使用；对外商业产品、收费／广告／赞助服务、客户交付、代理、转售、公开镜像和批量公开再分发等，须事先取得 AIHot 书面授权。署名不等于授权。wx-bot 许可和引用来源见 [第三方说明](THIRD_PARTY_NOTICES.md)。

测试使用虚构 fixture 和 mock，覆盖事件去重、时间变化、缺字段、状态范围、未来 cutoff、重启、通道关闭、TLS、`ret=-2` 和超时／unknown 等行为。不会构造线上 AIHot 事件，也不会向真实邮箱或微信投递。安装环境、上游兼容性和真实通道投递仍需使用者在自己的授权环境验收。
