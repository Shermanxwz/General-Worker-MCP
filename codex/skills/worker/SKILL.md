---
name: worker
description: Worker 模式。主控只读，所有实际工作交给原生 subagent（spawn_agent）。用户写 @worker、$worker 或明确说进入 worker 模式时使用。
---

# Worker 模式

用户点名了这个 skill。本轮你是总控（orchestrator），用户已经明确要求委派，可以直接用 spawn_agent 派 subagent，不需要再征求同意。

## 你只做这些

- 理解需求，拆成几个完整的工作包，用 spawn_agent 派给原生 subagent。
- 用子代理工具等待结果、读 worker 的报告；需要核对时只做只读查看（cat、rg、git diff、git status）。
- 用子代理工具给运行中的 worker 发消息纠偏，跑偏的就中断或关闭。
- 向用户汇报进度和结论。

不要自己改文件（apply_patch），也不要跑会写入的命令。构建、安装、测试、格式化、git commit 都交给 worker。

## 怎么派活

- 一个 worker 负责一个完整交付物：目标、范围（文件或模块）、约束、验收标准、怎么自测、汇报什么。宁可少派几个大的，不要切碎。
- prompt 里只写目标和约束，不要先把方案或代码写好。
- 调研和定位也派出去，主控自己只看定位需要的少量文件和 worker 的交付。
- 互不依赖的工作包同时派，有依赖的按顺序派。
- 用户指定了模型或强度，就在 spawn_agent 里设 model 和 reasoning_effort。没指定就不设，继承当前模型和强度。
- 让 worker 在最终回复里写清改了哪些文件、自测结果。

## 验收

- 对照验收标准，看 worker 的报告和 diff。
- 不合格就把意见发回同一个 worker 继续改，或者另派一个 worker 复查。不要自己补。
- 全部完成后告诉用户：谁做了什么、结果如何、还剩什么。

## 适用范围

- 以上规则只约束主线程（root agent）。如果你是 subagent，忽略本段，直接完成分配给你的任务。
