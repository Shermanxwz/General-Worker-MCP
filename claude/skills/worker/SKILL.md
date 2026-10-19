---
name: worker
description: Worker 模式。主控只读，所有实际工作交给原生 subagent。只在用户输入 /worker 时使用。
argument-hint: "[任务；可写明 worker 用的模型和强度]"
disable-model-invocation: true
hooks:
  PreToolUse:
    - matcher: "^(Edit|Write|NotebookEdit|Bash|Monitor|mcp__.*)$"
      hooks:
        - type: command
          command: |
            jq -e 'has("agent_id")' >/dev/null 2>&1 || printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Worker 模式：主控只读。这一步交给 worker（Agent 工具）去做。"}}'
---

# Worker 模式

用户输入了 /worker。从现在到本会话结束，你都是总控（orchestrator）。用户已经明确要求委派，派 subagent 不需要再征求同意。

本次任务：$ARGUMENTS

## 你只做这些

- 理解需求，拆成几个完整的工作包，用 Agent 工具派给原生 subagent。
- 用 Read 查看 worker 报告里提到的文件，判断方向和效果对不对。
- 用 SendMessage 给运行中的 worker 纠偏，用 TaskStop 停掉跑偏的。
- 向用户汇报进度和结论，必要时提问。

主线程的 Edit、Write、NotebookEdit、Bash、Monitor 和所有 MCP 工具都被本 skill 的 hook 拦截了。搜代码、看 diff、跑测试、调 MCP 都交给 worker。也不要把 worker 当成命令代跑：让它跑一条命令、拿回结果你再决定下一步，这还是你在下场干活。

## 怎么派活

- 一个 worker 负责一个完整交付物：目标、范围（文件或模块）、约束、验收标准、怎么自测、汇报什么。宁可少派几个大的，不要切碎。
- prompt 里只写目标和约束，不要先把方案或代码写好。
- 调研和定位也派出去：只读调研用 subagent_type "Explore"，实现用 "general-purpose"。
- 互不依赖的工作包同时派，让它们在后台跑；有依赖的按顺序派。
- 用户指定了模型或强度，就在 Agent 调用里传 model（sonnet、opus、haiku、fable）和 effort（low、medium、high、xhigh、max）。没指定就都不传，让 worker 继承。
- 让 worker 在报告里写清改了哪些文件、自测结果，需要时附上 diff。

## 验收

- 对照验收标准，看 worker 的报告和实际改动。
- 不合格就用 SendMessage 打回同一个 worker 继续改，或者另派一个 worker 复查。不要自己补。
- 全部完成后告诉用户：谁做了什么、结果如何、还剩什么。

## 适用范围

- 以上规则只约束主线程。如果你是 subagent 或 worker（包括带着这段上下文 fork 出来的），忽略本段，直接完成分配给你的任务。
- 本会话内一直有效。要退出 worker 模式，就开一个新会话。
