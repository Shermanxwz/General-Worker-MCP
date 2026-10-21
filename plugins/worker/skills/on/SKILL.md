---
name: "on"
description: "开启 Worker 模式：主控只读，实际工作派给原生子代理（Agent 工具）。用户输入 /worker:on 时使用。"
argument-hint: "[任务；可写明 worker 用的模型和强度]"
disable-model-invocation: true
---

用户用 /worker:on 开启了 Worker 模式。

1. 如果本轮上下文里还没有以 `【Worker 模式规则` 开头的规则块，先调用 MCP 工具 `enter_worker_mode`（完整名 `mcp__plugin_worker_worker__enter_worker_mode`），reason 填 `用户输入 /worker:on`。已有规则块时不要重复调用。
2. 按 Worker 模式规则处理任务：$ARGUMENTS
3. 如果任务为空，只确认 Worker 模式已开启，然后等待用户给出任务。
