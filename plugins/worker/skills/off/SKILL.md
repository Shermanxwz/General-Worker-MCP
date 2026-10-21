---
name: "off"
description: "关闭 Worker 模式，之后恢复普通模式：可直接读写文件、运行命令，需要时仍可派子代理。用户输入 /worker:off 时使用。"
disable-model-invocation: true
---

用户用 /worker:off 关闭了 Worker 模式。

1. 除非本轮上下文已经显示 `Worker 模式已关闭`，否则调用 MCP 工具 `exit_worker_mode`（完整名 `mcp__plugin_worker_worker__exit_worker_mode`），reason 填 `用户输入 /worker:off`。
2. 用一句话确认 Worker 模式已关闭。
3. 从现在起按普通模式工作：可以直接读写文件、运行命令，需要时仍可派子代理。
