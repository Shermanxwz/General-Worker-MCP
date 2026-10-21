---
name: worker
description: Worker 模式：主控只读，实际工作交给原生 subagent（spawn_agent）。用户写 @worker 或 $worker，或明确要求进入 worker 模式时使用。
---

# Worker 模式

用户点名了这个 skill，要求进入 Worker 模式。

## 先看上下文

- 如果本轮上下文里已经有以 `【Worker 模式规则` 开头的规则块，说明 hook 已经生效。按那段规则执行即可，不要重复解释。模式会一直保持，直到用户输入 `@worker off`，或你调用 MCP 工具 `exit_worker_mode`。
- 如果没有这个规则块，说明 hook 没有装在这个环境里。这时只在本轮按下面的简版规则执行，不会延续到后面的轮次。

## 简版规则（仅本轮，hook 未生效时）

- 你是总控（orchestrator），主线程只读：不要用 apply_patch 改文件，也不要运行会写入的命令。构建、安装、测试、格式化、git commit 都交给 worker。只读查看（cat、rg、git diff、git status）可以。
- 用 spawn_agent 把工作拆成完整的工作包，派给原生 subagent。每个工作包写清目标、范围、约束、验收标准和自测方法。互不依赖的工作包同时派。
- 只有用户明确指定了模型或推理强度时，才在 spawn_agent 里设置 model 和 reasoning_effort，并且先确认这个工具支持这两个参数。没有指定就不设。
- worker 回来后，对照验收标准审读它的报告和 diff。不合格就把意见发回同一个 worker 继续改，不要自己补。
- 向用户汇报谁做了什么、结果如何、还剩什么。

## 适用范围

- 以上内容只约束主线程。如果你自己是 subagent，忽略本 skill，直接完成分配给你的任务。
