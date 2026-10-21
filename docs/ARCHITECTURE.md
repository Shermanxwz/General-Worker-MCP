# 架构说明

本文记录 General-Worker-MCP 2.0.0 的组件、数据流、状态模型、事件、安全模型、设计决策、验证范围和约定。使用方法见 [README.md](../README.md)，版本记录见 [CHANGELOG.md](../CHANGELOG.md)。

## 1. 目标和边界

- 开启 Worker 模式后，主会话成为只读总控：拆分任务，派给运行时原生子代理（Claude 的 `Agent` 工具，Codex 的 `spawn_agent`），验收结果，向用户汇报。
- 模式按会话保持。用户可以随时用命令开关；模型可以通过结构化的 MCP 工具调用退出。
- 只使用官方扩展点：Claude Code 插件（skills、hooks、MCP）；Codex 的管理员 skill、托管 hooks（`requirements.toml`）和 `config.toml` 里的 MCP 条目。不修改 claude web 或 codex web 的代码、配置或服务。
- 运行时代码是 Node.js ESM（`.mjs`），只使用 `node:` 内置模块，不依赖 jq，最低 Node.js 22。

## 2. 组件和文件

| 路径 | 作用 |
|---|---|
| `.claude-plugin/marketplace.json` | 仓库根目录的 Claude Code 插件市场，只列出 `worker` 插件 |
| `plugins/worker/.claude-plugin/plugin.json` | 插件清单：名称、版本、描述、许可证 |
| `plugins/worker/.mcp.json` | 注册 MCP 服务器 `worker`，运行 `${CLAUDE_PLUGIN_ROOT}/runtime/worker-mcp.mjs` |
| `plugins/worker/hooks/hooks.json` | Claude 的 hook 注册：UserPromptSubmit、SessionStart、PreToolUse、PostToolUse |
| `plugins/worker/skills/on`、`off`、`status` | 插件的三个 skill，对应 `/worker:on`、`/worker:off`、`/worker:status` |
| `plugins/worker/runtime/state.mjs` | 会话键、状态目录、状态文件读写（原子写入） |
| `plugins/worker/runtime/worker-hook.mjs` | hook 处理程序，由 `--runtime` 选择 claude 或 codex |
| `plugins/worker/runtime/worker-mcp.mjs` | MCP 服务器，提供 `enter_worker_mode` 和 `exit_worker_mode` |
| `plugins/worker/rules/common.md`、`claude.md`、`codex.md` | 注入的中文规则：通用部分加各运行时的写法 |
| `codex/skills/worker/SKILL.md` | Codex 管理员 skill，`$worker` 或 `@worker` 时的兜底说明 |
| `install.sh` | 调度器：解析参数，检查 `NODE_BIN`，然后调用两个目标脚本 |
| `scripts/claude.sh` | Claude 目标：插件市场、插件、v1 清理 |
| `scripts/codex.sh` | Codex 目标：流程编排 |
| `scripts/codex-files.sh` | Codex 辅助脚本（被 source）：复制 runtime 和 rules，安装 skill |
| `scripts/codex-config.sh` | Codex 辅助脚本（被 source）：托管 hooks 块，`worker` MCP 条目 |
| `tests/` | Node 测试（hook、MCP）、Python 测试（元数据、发布一致性、安装器、探针）、两个 Codex 探针 |
| `.github/workflows/ci.yml` | CI：lint、tests、claude-plugin、codex 四个任务 |
| `LICENSE`、`CHANGELOG.md`、`docs/ARCHITECTURE.md` | 许可证、版本记录、本文档 |

运行时文件全部放在 `plugins/worker/` 之内。Claude Code 安装插件时只复制这个目录，之外的文件在安装后不可用。

## 3. 数据流

### 3.1 Claude

```
/worker:on 任务
  UserPromptSubmit hook    写入 on（by=user），注入规则
  skill on                 上下文里没有规则块时，调用 enter_worker_mode（MCP）
主线程调用 Edit、Write、Bash 或受限的 mcp__* 工具
  PreToolUse hook          开启、无 agent_id、工具在受限列表中 → deny
Agent 工具派出的子代理调用工具
  PreToolUse hook          有 agent_id → 放行
enter_worker_mode 或 exit_worker_mode 返回
  PostToolUse hook         按状态规则写入，输出规则或关闭提示
新会话或恢复会话
  SessionStart hook        开启 → 注入规则
```

### 3.2 Codex

```
@worker 任务 或 $worker 任务
  UserPromptSubmit hook（托管）   写入 on（by=user），注入规则
  skill worker（管理员）           规则块不存在时，本轮按简版规则执行
模型调用 mcp__worker__enter_worker_mode 或 exit_worker_mode
  PostToolUse hook（托管）        按状态规则写入，只输出 systemMessage
新会话或恢复会话
  SessionStart hook（托管）       开启 → 注入规则
```

- Codex 的托管块只有 UserPromptSubmit、SessionStart、PostToolUse 三个事件，没有 PreToolUse 处理。
- 子代理与主线程共享 `session_id`，hook 无法区分它们，所以子代理也会读到开启状态。规则第 8 条要求子代理忽略总控规则。
- 模型自己进入模式时，Codex 的 PostToolUse 只输出 systemMessage，规则从下一条用户消息起注入。

Codex 托管块中 PostToolUse 的匹配器是：

```
^mcp__.*__(enter|exit)_worker_mode$
```

### 3.3 MCP 服务器

`worker-mcp.mjs` 是 stdio 上的 JSON-RPC 服务器，每行一条消息。它不保存状态：两个工具只返回文本，状态变化由 PostToolUse hook 完成。

## 4. 状态模型

- 会话键来自 hook 输入的 `session_id`，只保留 `[A-Za-z0-9._-]`，其他字符换成 `_`，最长 200 字符。缺少或为空时视为关，且不写文件。
- 目录：环境变量 `WORKER_STATE_DIR` 优先。否则 Claude 用 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/worker-mode`，Codex 用 `${CODEX_HOME:-$HOME/.codex}/worker-mode`。目录权限 0700。
- 文件：`<目录>/<会话键>.json`，权限 0600，先写同目录的临时文件再 rename。
- 内容：`{"version":1,"on":true,"updatedAt":"<ISO 时间>","by":"user","notice":null}`，`by` 为 `user` 或 `model`，`notice` 为 `off` 或 `null`。
- 读取：文件缺失、损坏或 `version` 不是 1，都视为关。

写入规则：

| 来源 | 条件 | 结果 |
|---|---|---|
| 用户命令 on | 任何状态 | on，by=user，notice=null |
| 用户命令 off | 任何状态 | off，by=user，notice=null |
| exit_worker_mode 成功 | 当前为 on，reason 非空，没有报错，不是子代理（Claude） | off，by=model，notice=off |
| enter_worker_mode 成功 | 当前为 off，没有报错，不是子代理（Claude） | on，by=model，notice=null |
| 普通消息 | notice=off | 输出一次关闭文本，notice 清为 null |

`notice=off` 的作用是：模型退出后，之前注入的规则仍在对话历史里，下一条普通消息需要补一次关闭提示。

## 5. 事件表

| 事件 | 运行时 | 条件 | 输出 |
|---|---|---|---|
| UserPromptSubmit | 两者 | 命令 on | 注入规则，systemMessage 为「Worker 模式：已开启」 |
| UserPromptSubmit | 两者 | 命令 off | 注入关闭文本，systemMessage 为「Worker 模式：已关闭」 |
| UserPromptSubmit | 两者 | 命令 status | 状态行（开启时附规则），不写状态 |
| UserPromptSubmit | 两者 | 普通消息，开启 | 注入规则 |
| UserPromptSubmit | 两者 | 普通消息，notice=off | 注入关闭文本一次 |
| SessionStart | 两者 | 开启 | 注入规则 |
| PreToolUse | 仅 Claude | 开启，主线程，受限工具 | permissionDecision 为 deny，附原因 |
| PostToolUse（exit） | 两者 | 当前开启，reason 非空，没有报错 | Claude：关闭文本加 systemMessage；Codex：只有 systemMessage |
| PostToolUse（enter） | 两者 | 当前关闭，没有报错 | Claude：规则加 systemMessage；Codex：只有 systemMessage |

其他事件没有输出。hook 内部错误只在 stderr 写一行，退出码为 0。

## 6. 命令语法

- 形式：`^[/@$]worker(?::([A-Za-z_-]+))?(?=\s|$)`，大小写不敏感，先去掉开头的空白。
- 动作：冒号后的词是动作词时由它决定；否则由前缀后的第一个词决定。
- 动作词：`off`、`关闭`、`退出` 是关闭；`status`、`状态` 是查看；`on`、`开启` 以及其他任何内容都是开启，其余文字作为任务。
- 例：`/worker 修复登录`、`/worker:off`、`/worker:worker 任务`、`@worker off`、`$WORKER OFF`。
- 不是命令：`看看 /worker`、`worker off`（没有前缀）、`/workers`。

## 7. 安全模型

- 主线程只读只在 Claude 上由 hook 强制。PreToolUse 拒绝主线程（无 `agent_id`）的 `Edit`、`Write`、`MultiEdit`、`NotebookEdit`、`Bash`、`Monitor`、`PowerShell`，以及除 enter 和 exit 控制工具外的 `mcp__*` 工具。Codex 没有硬拦截，只靠规则。
- 失败放行：hook 的任何错误都不阻断用户。stdout 为空，stderr 写一行，退出码 0。从不以退出码 2 结束，因为 2 会阻断 PreToolUse 和 UserPromptSubmit。
- 路径白名单：`WORKER_HOME` 和 `NODE_BIN` 会写进托管 hook 的 `command` 字符串和 MCP 注册命令，只接受 `^/[A-Za-z0-9._/+-]+$`。空格、引号、反斜杠、`$` 和控制字符都会被拒绝，并给出原因。
- 托管 hook 由策略信任。它们写在 `requirements.toml` 的 `[hooks]` 中，`managed_dir` 指向 `WORKER_HOME`，用户不能在 hook 浏览器里关闭。非托管 hook 需要逐个信任，而 codex web 没有信任界面，所以安装器只写托管块。
- 安装器不使用 `rm -rf`。卸载只删除它写入的具体文件；目录用 `rmdir`，只删空目录；托管块用 awk 删除，文件只剩空白时才整个删除；状态目录不删除。
- MCP 命令以 `CODEX_HOME` 的所有者身份运行。root 运行且所有者不是 root 时，使用 `sudo -u "#<uid>"`，`config.toml` 始终由它的所有者读写。安装器只用 `codex mcp add` 和 `codex mcp remove` 修改它。
- 会话键净化后只写在状态目录内。`../../etc/x` 会变成 `.._.._etc_x.json`。
- 状态文件权限 0600，目录 0700，写入是原子的。
- CI 和测试使用临时的 HOME、CODEX_HOME 和状态目录，不调用模型，不登录。

## 8. 设计决策

| 决策 | 理由 | 来源 |
|---|---|---|
| 派工使用运行时原生子代理 | 派工和子代理输出由运行时管理，不需要自建调度 | [Claude 子代理](https://code.claude.com/docs/en/sub-agents)；[Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) |
| Claude 用插件分发 | skills、hooks、MCP 同包；`${CLAUDE_PLUGIN_ROOT}` 指向安装副本 | [插件清单](https://code.claude.com/docs/en/plugins-reference)；[插件市场](https://code.claude.com/docs/en/plugin-marketplaces) |
| 主线程拦截用 PreToolUse，子代理用 agent_id 放行 | 官方 hook 输入在子代理的工具调用中带有 `agent_id` | [Claude hooks](https://code.claude.com/docs/en/hooks) |
| Codex 用管理员 skill 和托管 hooks | 管理员 skill 由所有 collection 共用；托管 hooks 由策略信任，不依赖 web 的信任界面 | [Codex hooks](https://learn.chatgpt.com/docs/hooks)；[托管配置](https://learn.chatgpt.com/docs/enterprise/managed-configuration)；[Codex Skills](https://developers.openai.com/codex/skills) |
| MCP 服务器不保存状态 | 状态只由 hook 写入，避免两处状态不一致 | 设计选择 |
| Codex 的 MCP 由官方 CLI 写入 | `codex mcp add` 和 `codex mcp remove` 修改 `config.toml`，安装器不手改该文件 | [Codex MCP](https://developers.openai.com/codex/mcp)；[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference) |
| 子代理的模型和强度只在用户指定时传入 | 默认继承。Claude 的单次 `effort` 参数需要 Claude Code 2.1.292 或更新版本 | [Claude 子代理](https://code.claude.com/docs/en/sub-agents) |
| Codex 子代理默认值放在 `[agents]` 中 | `default_subagent_model` 和 `default_subagent_reasoning_effort` 在 config.toml 中设置 | [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)；[Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) |
| 只用 Node 内置模块，不用 jq | 两家运行时都依赖 Node，零依赖便于审计。两个 web 仓库的 package.json 都声明 `node >=22.12.0` | 两个 web 仓库的 package.json（内部） |
| 最低 Node.js 22 | Node 18 和 20 在官方版本表中已标为 EOL | [Node.js 版本表](https://nodejs.org/en/about/previous-releases) |
| Claude 的 hook 和 MCP 命令从 PATH 运行 `node` | 插件是通用的，不依赖安装机器上的绝对路径；claude web 的 systemd 单元把 Node 目录放在 PATH 最前面 | claude-official-web 仓库的 `deploy/*.service`（内部） |

## 9. 验证矩阵

| 层次 | 检查内容 | 位置 |
|---|---|---|
| 单元（Node） | 命令识别、事件输出、状态文件格式和权限、损坏文件、会话键净化、MCP 协议 | `tests/hook-commands.test.mjs`、`tests/hook-robustness.test.mjs`、`tests/hook-tools.test.mjs`、`tests/mcp.test.mjs` |
| 黑盒（Python） | 插件、市场、MCP、hooks、skill 的元数据；hooks 中的路径存在；发布一致性；探针的判断逻辑 | `tests/test_metadata.py`、`tests/test_release.py`、`tests/test_hooks_probe.py`、`tests/test_discovery.py` |
| 安装器 | 两个目标的 install、uninstall、status，退出码，v1 清理，路径白名单，失败提示；用假 CLI 记录调用 | `tests/test_install_claude.py`、`tests/test_install_codex.py` |
| 真实 CLI 往返（CI） | `claude plugin validate`；插件的 install、status、uninstall；Codex 托管块、admin skill 发现、MCP 注册和删除 | `claude-plugin` 和 `codex` 任务（Claude Code 2.1.295，Codex 0.163.0） |
| 托管 hook 发现（CI） | 发送 initialize 和 initialized 后，`hooks/list` 列出 worker 的托管 hook | `tests/check-codex-hooks.py` |
| 语法和格式（CI） | shellcheck（`-s sh`）、`node --check`、`py_compile`，以及 JSON 和 YAML 解析 | `lint` 任务 |

未验证的范围：

- 真实模型的行为：模型是否遵守规则，是否在该退出时调用 `exit_worker_mode`。
- web 界面的显示：claude web 的时间线和拒绝信息，codex web 的工具卡片和“官方事件”。
- claude web 如何把插件链接到各 collection，以及服务进程是否继承 `CLAUDE_CONFIG_DIR` 或 `CODEX_HOME`。
- Codex 主线程的硬拦截：没有，也无法做到，因为 hook 输入无法区分主线程和子代理。
- Codex 的 Linux 系统需求文件路径：官方 hooks 页面没有写明，本项目按 `/etc/codex/requirements.toml` 编写，以 CI 中 `hooks/list` 的结果为准。
- Codex 0.124 这个下限（托管 hooks 需要）：来自社区资料，没有官方确认。
- 派工工具名 `spawn_agent`：官方子代理页面没有写出工具名，规则按它编写。

## 10. 约定

- 代码、注释、日志和 stderr 用英文。给模型或用户看的文本（规则、skill、systemMessage、README、本文档）用中文。
- 运行时代码是 Node.js ESM，零依赖。stdout 只写协议消息或 hook 的 JSON。
- 安装脚本是 POSIX sh，`set -eu`，可以重复执行，每个动作输出一行，不使用 `rm -rf`。
- 可执行脚本（`install.sh`、`scripts/claude.sh`、`scripts/codex.sh`、`tests/check-*.py`）权限为 755；被 source 的辅助脚本（`scripts/codex-files.sh`、`scripts/codex-config.sh`）权限为 644。
- 测试使用临时目录，不调用模型，不登录。
- 注入的规则（标题、common 和运行时文件）合计少于 1800 字符。

## 11. 版本管理

- 版本号遵循语义化版本。发布时同时更新 `plugins/worker/.claude-plugin/plugin.json` 的 `version` 和 `plugins/worker/runtime/worker-mcp.mjs` 中 serverInfo 的 `version`，并在 CHANGELOG.md 顶部加一条新记录。
- 发布一致性测试（`tests/test_release.py`）检查三处版本一致，检查 CHANGELOG 最新条目的版本，并检查 LICENSE 是 MIT、`plugin.json` 的 `license` 是 `"MIT"`。
- CHANGELOG.md 按 Keep a Changelog 的格式书写，最新版本在前，日期为发布日期。
