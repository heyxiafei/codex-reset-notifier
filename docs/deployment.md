# 持续运行（由用户另行启用）

先按 README 在前台完成离线验证、私有配置、`init` 和 dry-run。下面仅提供待审阅模板，不会安装或启动任何服务。用你新安装的路径替换 `INSTALL_DIR`，状态目录、配置和程序使用同一普通用户；不要指向已有生产桥接目录。配置文件 0600、state 目录 0700，不能把秘密写在服务定义或命令行参数中。

首次启用只处理全局和通道 cutoff 之后的新事件。服务模式沿用已有 cutoff 和 SQLite outbox；普通停止后以原配置重启，可能追赶停机期间仍在 recent 接口窗口内、此前未处理的新事件，不补完整历史。worker 观察到通道关闭后再次启用则重新建立该通道起点。不要为调整文案或恢复服务而清空账本。启动前或运行中可用只读 `status` 检查 cutoff 和 `unknown`／`needs_user_action` 计数；该操作不争用 worker 进程锁，也不改变数据库。接受请求不代表已投递，服务管理器重启也不能让不确定记录安全重发。

## macOS launchd 模板

保存前将 `INSTALL_DIR` 换为实际绝对目录。`ProgramArguments` 必须逐个字符串填写；launchd 不展开 `~`、环境变量或 shell 表达式。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.codex-reset-notifier</string>
  <key>ProgramArguments</key><array>
    <string>INSTALL_DIR/.venv/bin/codex-reset-notifier</string>
    <string>--env-file</string><string>INSTALL_DIR/.env</string>
    <string>run</string><string>--live</string>
  </array>
  <key>WorkingDirectory</key><string>INSTALL_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>600</integer>
  <key>Umask</key><integer>63</integer>
</dict></plist>
```

在明确要后台启用后，将审阅好的 plist 放入本人 `~/Library/LaunchAgents`，用 `plutil -lint` 检查，再按本机 launchd 文档加载。卸载时先停止 job，再移除 plist；保留 state 供后续去重。模板中的 `--live` 会允许外发；第一次后台预览应去掉该参数。

## Linux systemd 用户服务模板

下面放入本人 systemd 用户服务目录前，同样替换绝对路径，先检查本机 systemd 对各安全选项的支持。

```ini
[Unit]
Description=Independent AIHot Codex reset text notifier
After=network-online.target

[Service]
Type=simple
WorkingDirectory=INSTALL_DIR
ExecStart=INSTALL_DIR/.venv/bin/codex-reset-notifier --env-file INSTALL_DIR/.env run --live
Restart=on-failure
RestartSec=600
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=default.target
```

先以用户身份检查 unit；仅在用户明确启用后加载与启动。不要使用 root 安装，不默认启用 linger，也不要修改系统级服务。检查及停用使用本机 `systemctl --user`；更新前先停止 worker，保留 `.env` 和 state，重新运行离线测试，再恢复。

## 微信与容器边界

wx-bot 必须另行安装和由用户扫码授权，API 保持 token 保护和数字 loopback 绑定。`127.0.0.1` 在不同容器里不指向同一个服务；不要用 `0.0.0.0` 暴露 API 作为快捷解决办法。本版没有 Docker 镜像、compose 或跨容器安全共享网络方案。这些属于后续部署工作，不能视为本版已验证能力。

无入站导致发送上下文失效时，由用户恢复／再发消息。不得配置机器人自动保活。live 微信模式包含无模型收件确认：每约 60 秒拉取并确认专用 pull binding 的入站事件，每轮最多 500 条。每次 pull／ACK／send 前通过只读 status 核对精确 binding、pull 模式、登录／过期状态及单元素 owner allowlist 与配置收件人一致，消息还要核对 `from_wxid`；无法核实即停止相应操作，不输出 owner ID。仅保留固定状态和确认计数，不执行、保留或输出聊天正文。允许名单内告警会输出固定恢复诊断，未知告警不 ACK。使用专用 wx-bot 实例，让本 worker 成为该 binding 的唯一消费者；确认会丢弃入站事件，不可与其他应用共用。日志的 `acked` 表示收件队列确认，不等于出站通知投递。

## 运行检查

服务日志仅保留固定状态码和必要诊断，不打印 `.env`、token、SMTP 原始响应、收件人或完整通知内容。`status` 提供聚合计数，足以区分接受、未知、失败和需用户恢复。未知结果应人工核对接收端；首版没有重放命令，不通过删库或改状态来自动补发。

本次发布没有安装或启动以上服务，也没有扫码、测试发信或微信实投。离线和 mock 测试证明本地行为；用户环境的依赖、授权、网络及最终接收仍须另行验收。
