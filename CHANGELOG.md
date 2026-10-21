# 更新记录

本文件记录各版本的变化，格式参照 Keep a Changelog，版本号遵循语义化版本。最新版本在前。

## [2.0.0] - 2026-10-21

### 新增
- Worker 模式按会话持续保持：开启后一直有效，直到用户关闭，或模型调用 `exit_worker_mode` 退出（模型会先问用户，同意后才退出）。
- Claude Code 插件 `worker`（市场 `general-worker-mcp`），提供 `/worker:on`、`/worker:off`、`/worker:status` 三个 skill，并支持 `@worker` 写法。
- MCP 服务器 `worker`（`plugins/worker/runtime/worker-mcp.mjs`），提供 `enter_worker_mode` 和 `exit_worker_mode` 两个工具。切换以工具调用的形式出现在会话中。
- hook 处理程序和按会话划分的状态文件（`worker-hook.mjs`、`state.mjs`）。UserPromptSubmit、SessionStart、PostToolUse 两家都用；PreToolUse 只用于 Claude。
- Codex 目标：管理员 skill（`/etc/codex/skills/worker/SKILL.md`）、托管 hooks（`/etc/codex/requirements.toml` 中的托管块）和 `worker` MCP 注册。
- 安装器重写：`install.sh` 负责参数和 Node 检查，`scripts/claude.sh` 和 `scripts/codex.sh` 分别处理两个目标，另有两个辅助脚本。支持 install、uninstall、status。
- 规则新增四条协作做法：共享背景先写入文件，之后的工作包只引用路径；工作者只汇报改动、自测结果和遗留问题；返工发回同一个工作者；重要改动由未实现它的工作者独立检查。
- 测试与 CI：Node 测试（hook、MCP）、Python 测试（元数据、发布一致性、安装器、探针）、两个只读 Codex 探针。CI 有 lint、tests、node-tests、claude-plugin、codex 五个任务，版本固定，不调用模型、不登录。
- 发布一致性测试：`plugin.json` 的版本、MCP 服务器 serverInfo 的版本和本文件最新条目必须一致；许可证必须是 MIT。
- MIT 许可证（`LICENSE`），`plugin.json` 声明 `"license": "MIT"`。
- 架构说明 `docs/ARCHITECTURE.md`。

### 变更
- 运行时改为 Node.js（>= 22）实现，零依赖，不再需要 jq。安装器拒绝 Node 22 之前的版本。
- Claude 的主线程只读约束由 PreToolUse 强制，子代理不受限。Codex 没有硬拦截，只靠规则。
- Codex 的 skill 从 v1 的用户目录（`$HOME/.agents/skills`）改为管理员目录 `/etc/codex/skills`，所有 codex web collection 共用。
- Codex 的 Worker 模式开启一次即可保持，不再需要每条消息写 `@worker`。
- 规则拆为通用部分（`rules/common.md`）和每个运行时一个文件。注入的总长度限制在 1800 字符以内，由测试检查。

### 移除
- v1 的 Claude skill（`claude/skills/worker/SKILL.md`），由插件替代。安装器会删除 v1 的 `$CLAUDE_SKILLS_DIR/worker/SKILL.md`（按内容识别）和 `SKILL.md.bak`。
- v1 的 jq hook 测试（`tests/test-hook.sh`）和旧安装器测试（`tests/test_install.py`）。

### 安全
- Claude 主线程调用 `Edit`、`Write`、`MultiEdit`、`NotebookEdit`、`Bash`、`Monitor`、`PowerShell` 以及除控制工具外的 `mcp__*` 工具时会被拒绝。子代理不受影响。
- `WORKER_HOME` 和 `NODE_BIN` 必须匹配 `^/[A-Za-z0-9._/+-]+$`，因为它们会写入托管 hook 和 MCP 命令。其他路径会被拒绝，并给出原因。
- 卸载只删除安装器写入的具体文件和空目录，不使用 `rm -rf`。

## [1.0.0] - 2026-10-20

### 新增
- 两个 skill：Claude 版（`claude/skills/worker/SKILL.md`）在输入 `/worker` 后，让整个会话只派工；Codex 版（`codex/skills/worker/SKILL.md`）每条消息写 `@worker`，只对当前一轮生效。
- 安装器 `install.sh`：把两个 skill 复制到 `$CLAUDE_SKILLS_DIR/worker/`（默认 `~/.claude/skills`）和 `$CODEX_SKILLS_DIR/worker/`（默认 `~/.agents/skills`），支持 install、uninstall、status。
- 测试：Claude hook 测试 `tests/test-hook.sh`（依赖 jq）；元数据、skill 发现和安装器的 Python 测试；`tests/check-codex-skills.py` 探针。
- GitHub Actions CI（`.github/workflows/ci.yml`）。
