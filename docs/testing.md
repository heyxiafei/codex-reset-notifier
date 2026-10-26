# 离线验证与验收边界

在仓库根目录创建新的虚拟环境并安装候选包后运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/codex-reset-notifier run --once --fixture tests/fixtures/recent.json --preview-fixture
python3 scripts/scan_release.py
git diff --check
```

测试夹具为虚构数据，单元测试用 mock 替代 AIHot、SMTP 和 wx-bot 请求。fixture 预览不请求网络，不创建运行账本，不构造通道或发送通知。安装测试允许下载公开构建依赖；worker 没有模型或第三方 Python 运行依赖。

发布前应从候选提交导出全新目录，在独立虚拟环境重新安装，并从安装后的包运行相同测试和预览。不要只在带有旧版已安装包或 `PYTHONPATH=src` 的开发环境里验证。核对候选提交、文件清单、暂存／提交内容与扫描对象一致；扫描是辅助检查，不能替代人工审查 diff。运行日志和真实接收回执不放入公开仓库。

可以证明的内容：事件语义映射、未来 cutoff、重复与重启状态、默认关闭、干运行不外发，以及 mock 下的通道协议和错误处理。首版公开时的 54 项离线测试不是微信或邮件实投证明；测试数量变化也不改变这个边界。

不能据此证明：使用者已授权通道、已启动持续进程、微信上下文未过期、外部服务接受之后已到达接收端、安装即收消息或永久必达。通过另一套自建桥接收到消息，也不能替代本包的通道验收。真实投递只可在用户明确授权的独立环境另行核对，不为跑测试而扫码、外发或连接现有私人生产实例。
