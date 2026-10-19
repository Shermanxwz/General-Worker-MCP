# General Worker MCP

## 这是什么

Worker 模式：让 Claude Code 和 Codex 的主控 agent 进入只读的总控模式。主控只负责理解需求、拆成完整的工作包、派给各家原生的 subagent，并验收结果。它不直接改文件，也不自己跑命令。

- 仓库名里有 MCP，但**它不是 MCP server**。它就是两个官方 skill：Claude Code 的 `claude/skills/worker/SKILL.md` 和 Codex 的 `codex/skills/worker/SKILL.md`，外加一个安装脚本。
- **不改任何 web 应用**的代码、配置或服务。
- 只用官方扩展点：skill 文件，以及 Claude 版 skill frontmatter 里声明的 PreToolUse hook。

## 使用

### Claude Code

在会话里输入：

```
/worker 任务…，worker 用 haiku
```

- 模型和强度写在任务里即可，例如 `worker 用 opus，强度 high`。
- 本会话内一直有效。要退出 worker 模式，开一个新会话。

### Codex

Codex 的 skill 按轮生效，所以**每条消息都要带上 `@worker` 或 `$worker`**：

```
@worker 任务…，worker 用 haiku
```

## 工作原理

- **官方 skill**：`worker/SKILL.md` 放进 skills 目录后，只有用户调用时才生效，不会自动触发。
- **skill 级 PreToolUse hook（仅 Claude 版）**：hook 写在 skill 的 frontmatter 里，只在 worker 模式内生效。它检查 hook 输入 JSON 里有没有 `agent_id`。有，说明是 subagent，放行；没有，说明是主线程，拒绝 Edit、Write、NotebookEdit、Bash、Monitor 和所有 MCP 工具。输入解析失败时也拒绝（fail closed）。
- **worker 就是原生派活工具**：Claude 用 `Agent` 工具，可传 `model` 和 `effort`；Codex 用 `spawn_agent`，可设 `model` 和 `reasoning_effort`。用户没指定就都不传，让 worker 继承当前模型和强度。

## 限制

- Claude 主线程读文件用 Read。跑命令、看 diff、跑测试、调 MCP 都会被 hook 拦截，要派给 Explore 或 general-purpose worker。
- Claude 版依赖 `jq`。hook 靠它判断 `agent_id`，没有 jq 时所有工具调用（包括 subagent 的）都会被拒绝，所以安装脚本在没有 jq 时拒绝安装 Claude 版。
- hook 只拦截 Edit、Write、NotebookEdit、Bash、Monitor 和 `mcp__*` 工具。Grep、Glob、CronCreate、RemoteTrigger、EnterWorktree 等其他工具不在拦截范围内，只靠指令约束。
- Codex 版的只读完全靠指令约束，没有 hook 强制。
- 还没有用真实模型对话（live model turn）跑过端到端测试。目前只有 hook 的本地测试，以及 skill 发现的探测。

## 安装

```
sh install.sh [install|uninstall|status] [all|claude|codex]
```

默认是 `install all`。两个目标目录由环境变量决定：

| 变量 | 默认值 |
|---|---|
| `CLAUDE_SKILLS_DIR` | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills` |
| `CODEX_SKILLS_DIR` | `$HOME/.agents/skills` |

- 安装只写 `<目录>/worker/SKILL.md`，权限 0644。目标已存在且内容不同时，先存为 `SKILL.md.bak`（只保留上一次被替换的版本）。
- 不改 `settings.json`、`config.toml`，也不重启任何服务。
- 没有 jq 时，安装 Claude 版会报错并拒绝；Codex 版不受影响。

通用示例：

```sh
sh install.sh                  # 装到两个默认目录
sh install.sh status           # 查看两个目标的状态
sh install.sh uninstall all    # 卸载
```

### 自托管 claude web / codex web 的例子

```sh
sudo env CLAUDE_SKILLS_DIR=/var/lib/claude-collection-web/.claude/skills CODEX_SKILLS_DIR=/etc/codex/skills sh install.sh
```

- claude web 的 collection 会链接到 `/var/lib/claude-collection-web/.claude/skills`，所以装在这里，所有 collection 的新会话都能用 `/worker`。
- `/etc/codex/skills` 是 Codex 的 admin skill 范围，所有 codex web collection 都能看到这个 skill。

## 测试

```sh
sh tests/test-hook.sh                          # hook 和 matcher 测试，需要 jq，全部 PASS 时退出码为 0
python3 tests/check-codex-skills.py            # 问本机的 codex app-server 能否列出 worker
python3 tests/check-codex-skills.py --cwd DIR  # 同上，但针对 DIR（DIR 下要有 .agents/skills/worker/SKILL.md）
```

`check-codex-skills.py` 使用全新的临时 `CODEX_HOME`，不会读取真实的 `~/.codex`。它会列出非 system 的 skill，并标出 scope，找到 `worker` 时退出码为 0。

## 给部署它的 AI 的检查清单

1. 克隆仓库并进入目录：`git clone https://github.com/Shermanxwz/General-Worker-MCP && cd General-Worker-MCP`
2. 运行 `sh tests/test-hook.sh`，应全部 PASS，退出码为 0。
3. 安装（自托管例子）：`sudo env CLAUDE_SKILLS_DIR=/var/lib/claude-collection-web/.claude/skills CODEX_SKILLS_DIR=/etc/codex/skills sh install.sh`。如果报 jq 缺失，先装 jq，再重跑。
4. 查看状态：`sudo env CLAUDE_SKILLS_DIR=/var/lib/claude-collection-web/.claude/skills CODEX_SKILLS_DIR=/etc/codex/skills sh install.sh status`，两个目标都应是 `installed (up to date)`。
5. 运行 `python3 tests/check-codex-skills.py`，输出里应有 `worker`，scope 为 `admin`。
6. 告诉用户：打开一个新的 claude web 会话才能用 `/worker`；刷新 codex web，或新开一个线程，才能用 `@worker` / `$worker`。

## 卸载

```sh
sudo env CLAUDE_SKILLS_DIR=/var/lib/claude-collection-web/.claude/skills CODEX_SKILLS_DIR=/etc/codex/skills sh install.sh uninstall all
```

只删除 `worker/SKILL.md`、`worker/SKILL.md.bak`，以及变空的 `worker/` 目录。不使用 `rm -rf`，也不动 skills 目录里的其他内容。
