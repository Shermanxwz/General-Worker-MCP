# General Worker MCP

## 这是什么

v2 的 Worker 模式：在 Claude Code 和 Codex 的会话里开启后，主会话变成只读总控。它只负责理解需求、拆成完整的工作包、派给原生子代理、验收结果并汇报；文件修改和命令执行都交给子代理。模式按会话保持，直到用户关闭，或模型按规则退出。

- 只用官方扩展点。Claude Code：插件（skills + hooks + MCP）。Codex：管理员 skill（`/etc/codex/skills`）+ 托管 hooks（`/etc/codex/requirements.toml`）+ MCP。
- 不修改 [codex web](https://github.com/Shermanxwz/Codex-Official-App-Server-Web) 和 [claude web](https://github.com/Shermanxwz/Claude-official-web) 的代码、它们自己的配置（CAW_*/CWEB_* 设置）或服务。安装时只通过两家官方机制写入：Claude Code 插件安装（写入服务用户的 `CLAUDE_CONFIG_DIR` 里的 `settings.json`（`enabledPlugins`）和 `plugins/` 目录）、`/etc/codex/skills/worker/`、`/etc/codex/requirements.toml` 中的托管块，以及 owner 的 `config.toml` 里由 `codex mcp add` 写入的 worker 条目。
- 派工用的是两家的原生子代理（Claude 的 `Agent` 工具，Codex 的 `spawn_agent`），所以两个 web 都能原生显示派工过程。
- 仓库包含一个 MCP 服务器 `plugins/worker/runtime/worker-mcp.mjs`，提供 `enter_worker_mode` 和 `exit_worker_mode` 两个工具。运行时代码只用 Node.js 内置模块（需要 Node.js 22 或更新版本），没有第三方依赖，也不需要 jq。

## 使用

Claude Code：

| 操作 | 输入 |
|---|---|
| 开启并交任务 | `/worker:on 任务`，或 `@worker 任务` |
| 关闭 | `/worker:off`，或 `@worker off` |
| 查看状态 | `/worker:status`，或 `@worker status` |

Codex：

| 操作 | 输入 |
|---|---|
| 开启并交任务 | `@worker 任务`，或 `$worker 任务` |
| 关闭 | `@worker off` |
| 查看状态 | `@worker status` |

- 开启后，模式在本会话内一直保持，直到关闭。开启期间每条回复都以 `〔Worker 模式〕` 开头。
- 模型可以自己退出：你要求它停止派发、自己动手时，它调用 `exit_worker_mode`。它认为模式已经没用时，会先问你，你同意后才退出。
- 总控的协作做法（写在规则里）：共享的背景（需求、约束、接口约定）先由一个工作者写入文件，之后的工作包只引用路径。工作者结束时只汇报改动的文件、自测结果和遗留问题，不贴完整内容或过程日志。返工发回同一个工作者，保留它的上下文；只有方向错了才换新的工作者。重要改动由没有实现它的工作者独立检查。
- 子代理的模型和强度：
  - Claude：在任务里写明，例如 `/worker:on 修复登录，worker 用 haiku，强度 high`。模型可选 sonnet、opus、haiku、fable，强度可选 low、medium、high、xhigh、max。只有你写明时才会传给 `Agent` 工具，否则子代理继承默认值。需要 Claude Code 2.1.292 或更新版本：官方[子代理文档](https://code.claude.com/docs/en/sub-agents)写明，单次调用的 `effort` 参数要求这个版本。CI 测试的是 2.1.295。
  - Codex：`spawn_agent` 不一定支持设置模型和强度。要统一子代理的配置，在 codex web 宿主的 `config.toml` 里设置：

```toml
[agents]
default_subagent_model = "<模型 ID>"
default_subagent_reasoning_effort = "high"
```

## 工作原理

### 命令识别

hook 识别以 `/worker`、`@worker` 或 `$worker` 开头的输入，大小写不敏感。冒号后面的词（如 `/worker:off`）或紧跟其后的第一个词决定动作：

- `off`、`关闭`、`退出`：关闭。
- `status`、`状态`：只查看，不改变状态。
- 其他（包括 `on`、`开启`，或没有词）：开启，其余文字作为任务。

`看看 /worker`、`worker off`（没有前缀）、`/workers` 不是命令。

### 各事件的行为

| 事件 | Claude | Codex | 行为 |
|---|---|---|---|
| UserPromptSubmit | 有 | 有 | 处理命令；开启期间每轮注入规则 |
| SessionStart | 有 | 有 | 开启中的会话开始或恢复时注入规则 |
| PreToolUse | 有 | 没有 | 主线程（无 `agent_id`）调用 `Edit`、`Write`、`MultiEdit`、`NotebookEdit`、`Bash`、`Monitor`、`PowerShell`，或除控制工具外的 `mcp__*` 工具时拒绝。Codex 无法区分主线程和子代理，所以不做拦截 |
| PostToolUse | 有 | 有 | 只处理 `enter_worker_mode` 和 `exit_worker_mode`；真正切换状态时输出 `additionalContext`（进入为规则，退出为关闭文本）和系统提示，两家相同 |

- PostToolUse 的切换条件：退出需要当前为开、`reason` 非空、工具没有报错；进入需要当前为关、工具没有报错。
- 模型调用 `enter_worker_mode` 或 `exit_worker_mode` 并完成切换后，两家都通过 PostToolUse 的 `additionalContext` 注入规则（进入）或关闭文本（退出），并显示系统提示。Claude 官方文档写明，这段文本放在工具结果旁，模型在下一次请求时读取。Codex 官方文档的示例包含 `additionalContext`，并说明它作为额外的开发者上下文加入，但没有写明同步 hook 的生效时间。两家都没有在真实模型上验证。
- Claude 的子代理（`agent_id` 非空）不能切换模式。
- hook 出错时放行：输入不是合法 JSON、事件未知或内部错误，都只在 stderr 写一行，退出码为 0，不会阻断用户。

### 状态文件

- Claude：`$CLAUDE_CONFIG_DIR/worker-mode/<会话>.json`，默认 `~/.claude/worker-mode/`。
- Codex：`$CODEX_HOME/worker-mode/<会话>.json`，默认 `~/.codex/worker-mode/`。
- 设置 `WORKER_STATE_DIR` 时，两者都改用这个目录。
- 目录权限 0700，文件权限 0600，原子写入。内容示例：`{"version":1,"on":true,"updatedAt":"<ISO 时间>","by":"user","notice":null}`。
- `session_id` 中不属于 `[A-Za-z0-9._-]` 的字符换成 `_`，最长 200 字符。缺少 `session_id` 时视为关，并且不写文件。
- 文件缺失或损坏都视为关。`by` 记录是用户还是模型切换的。`notice: "off"` 表示模型刚刚退出，下一条普通消息会补一次关闭提示，因为之前的规则还留在历史里。

### 规则文件

- `plugins/worker/rules/common.md`：两家共用（回复标记、总控职责、工作包写法、验收和退出，以及“使用”一节里的协作做法）；`claude.md`、`codex.md` 是各自的派发工具和命令写法。
- 每次 hook 调用都会重新读取 `common.md` 和当前运行时的文件（`claude.md` 或 `codex.md`）。文件缺失或为空时，使用 `worker-hook.mjs` 里的短文本。注入的内容（标题、common 和运行时文件）合计少于 1800 字符，测试会检查。

### MCP 工具

`plugins/worker/runtime/worker-mcp.mjs` 是 stdio 上的 MCP 服务器（换行分隔的 JSON-RPC），本身不保存状态，状态由 hook 管理。

- `enter_worker_mode`，参数 `reason` 可选。
- `exit_worker_mode`，参数 `reason` 必填。

工具名取决于注册方式：Claude 插件里是 `mcp__plugin_worker_worker__enter_worker_mode` 和 `mcp__plugin_worker_worker__exit_worker_mode`；Codex 里是 `mcp__worker__enter_worker_mode` 和 `mcp__worker__exit_worker_mode`。

### 用户在两个 web 里预期看到什么

以下是设计预期。界面的实际显示没有在真实 web 中验证，见“限制与未验证项”。

- claude web：时间线里有 hook 行，被拦截的工具调用显示拒绝原因，模式切换时有系统提示。
- codex web：MCP 工具调用显示为工具卡片，hook 运行记录在“官方事件”里。

## 安装

### 前提

- 服务器上的 Node.js 22 或更新版本（`node --version`）。安装器会拒绝 Node 22 之前的版本。两个 web 仓库的 `package.json` 都声明 `node >=22.12.0`。CI 在 Node 22 和 24 上测试。
- Claude 目标：Claude Code CLI，2.1.292 或更新版本，最好与 claude web 使用的版本一致。CI 测试的是 2.1.295。claude web 固定 `@anthropic-ai/claude-agent-sdk` 0.3.295，其 npm 元数据中的 `claudeCodeVersion` 为 2.1.295，所以 claude web 的 worker 可以使用单次 `effort` 参数（需要 2.1.292 或更新版本）。
- Codex 目标：codex CLI 0.124 或更新版本。0.124 这个下限（托管 hooks 需要）来自社区资料，没有经过官方文档确认；0.124.0 上 `codex mcp list --json` 和 `codex mcp get` 的行为已在本地确认。CI 只测试 0.163.0。

### 入口

先从 `main` 分支获取仓库（合并后仓库只有 `main` 分支）：

```sh
git clone --branch main https://github.com/Shermanxwz/General-Worker-MCP.git /opt/general-worker-mcp-src
```

然后在仓库目录里运行：

```sh
sh install.sh [install|uninstall|status] [all|claude|codex]
```

默认是 `install all`。退出码：0 表示所有请求的步骤都成功；1 表示有步骤失败（其他步骤仍会执行）；2 表示参数错误。`sh install.sh --help` 显示用法。

`install.sh` 检查参数和 `NODE_BIN`（版本 22 或更新，路径符合下文的规则），然后依次调用 `scripts/claude.sh` 和 `scripts/codex.sh`。`NODE_BIN` 不合格时，所有请求的目标都不执行，退出码为 1。`scripts/codex.sh` 再加载两个辅助脚本：`scripts/codex-files.sh` 复制 runtime 和 rules、安装 skill；`scripts/codex-config.sh` 维护 `requirements.toml` 中的托管块，并执行 `codex mcp add` 和 `codex mcp remove`。

仓库目录要放在稳定路径，例如 `/opt/general-worker-mcp-src`。Claude 的插件市场直接从这个目录读取。目录移动后，在新位置重新运行 install 会删除旧市场并从新路径重新添加，插件随之重新安装；status 会报告市场目录里缺少 `.claude-plugin/marketplace.json`（目录被移走时也是这种情况），或来源与 WORKER_MARKETPLACE_SOURCE 不一致。

### Claude web

在 claude web 的服务用户下执行，HOME 和 CLAUDE_CONFIG_DIR 要与 claude web 一致：

```sh
cd /opt/general-worker-mcp-src
sudo -u <服务用户> env HOME=/var/lib/claude-collection-web NODE_BIN=/path/to/node CLAUDE_BIN=/path/to/claude sh install.sh install claude
```

这一步会添加插件市场 `general-worker-mcp`，在用户范围安装 `worker@general-worker-mcp`，并删除 v1 的 Claude skill（见“从 v1 升级”）。claude web 会把基础配置中的插件和 settings 链接到每个 collection，新建的 claude web 会话会加载它。

也可以不依赖本地目录，改用 GitHub 源：`WORKER_MARKETPLACE_SOURCE=Shermanxwz/General-Worker-MCP`，从 `main` 分支读取。重新运行 install 会把已添加的市场切换到这个源（先删除再添加），status 会报告来源不一致。

### Codex web

```sh
cd /opt/general-worker-mcp-src
sudo env CODEX_HOME=<codex web 宿主的 CODEX_HOME> NODE_BIN="$(command -v node)" CODEX_BIN="$(command -v codex)" sh install.sh install codex
```

这一步会写入：

- `$WORKER_HOME`（默认 `/opt/general-worker-mcp`）：`runtime/*.mjs` 和 `rules/*.md`。
- `/etc/codex/skills/worker/SKILL.md`：管理员 skill，所有 codex web collection 共用。原文件与新内容不同时，先保存为 `SKILL.md.bak`；卸载时这两个文件都会删除。
- `/etc/codex/requirements.toml` 中的托管块，由 `# >>> general-worker-mcp (managed by install.sh)` 和 `# <<< general-worker-mcp` 包围，内含 `[hooks]`（`managed_dir` 指向 `$WORKER_HOME`），以及 UserPromptSubmit、SessionStart、PostToolUse 三个事件的 command hook。文件或目录不存在时创建。
- MCP 注册：`codex mcp add worker -- <NODE_BIN> <WORKER_HOME>/runtime/worker-mcp.mjs`。失败时打印等效的 `[mcp_servers.worker]` TOML，供手动添加。

注意：

- `codex mcp` 命令以 `CODEX_HOME` 的所有者身份运行。以 root 运行而 `CODEX_HOME` 属于其他用户时，安装器用 `sudo -u "#<uid>"` 执行；没有 sudo 时这一步失败。`CODEX_HOME` 必须是已存在的目录，否则 MCP 步骤失败。
- `requirements.toml` 中已经有 hooks 配置（`[hooks]`、`[[hooks.` 或 `hooks =`）时，安装器不覆盖它，这一步失败，并打印托管块，请手动合并后重新执行。
- `WORKER_HOME` 和 `NODE_BIN` 会写进 hook 和 MCP 命令，必须匹配 `^/[A-Za-z0-9._/+-]+$`：绝对路径，只含字母、数字和 `.`、`_`、`/`、`+`、`-`。空格、引号、反斜杠、`$` 和控制字符都会被拒绝，并给出原因。install、uninstall、status 都会检查；`NODE_BIN` 不合格时，所有目标都不执行。
- 安装后还要在 codex web 中操作：每个 collection 打开“MCP servers…”对话框，勾选 `worker`；然后刷新页面或新开线程。

### 状态和卸载

```sh
sh install.sh status              # 两个目标都检查，全部就绪时退出码为 0
sh install.sh status claude
sh install.sh uninstall claude
```

Codex 的 status 和 uninstall 要带与 install 相同的 `CODEX_HOME`、`NODE_BIN`、`CODEX_BIN`，非默认的 `WORKER_HOME`、`CODEX_SKILLS_DIR` 也要一致，通常用 `sudo env ...`，因为 sudo 会重置 PATH。`CODEX_REQUIREMENTS_FILE` 只用于测试：Codex 只读取 `/etc/codex/requirements.toml`，设成其他路径时 status 会打印警告（不影响退出码）。

Codex 卸载会删除托管块（文件只剩空白时整个删除）、名为 `worker` 的 MCP 注册（按名字精确匹配，其他服务器不受影响）、`SKILL.md` 和 `SKILL.md.bak`，以及 runtime 和 rules 文件。

### 环境变量（来自 `install.sh --help`）

| 变量 | 默认值 | 用于 |
|---|---|---|
| `NODE_BIN` | `command -v node`，必须是 Node 22 或更新版本的绝对路径，且只含 `^/[A-Za-z0-9._/+-]+$` 允许的字符 | 全部 |
| `CLAUDE_BIN` | `claude` | Claude |
| `WORKER_MARKETPLACE_SOURCE` | 本仓库目录，也可以是 `Shermanxwz/General-Worker-MCP` | Claude |
| `CLAUDE_CONFIG_DIR` | `$HOME/.claude` | Claude |
| `CLAUDE_SKILLS_DIR` | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills`，只用于清理 v1 | Claude |
| `CODEX_BIN` | `codex` | Codex |
| `CODEX_HOME` | 无默认值；必须是已存在的目录，MCP 步骤必需 | Codex |
| `WORKER_HOME` | `/opt/general-worker-mcp`，必须匹配 `^/[A-Za-z0-9._/+-]+$` | Codex |
| `CODEX_SKILLS_DIR` | `/etc/codex/skills` | Codex |
| `CODEX_REQUIREMENTS_FILE` | `/etc/codex/requirements.toml`；仅用于测试，Codex 只读取这个默认路径，设成其他路径时 status 警告 | Codex |

## 限制与未验证项

- Codex 的主线程没有硬拦截。Codex 的 hook 输入无法区分主线程和子代理，所以只能靠指令约束主线程不直接改文件。
- Codex 子代理与主线程共享 session id，子代理的 hook 也会读到开启状态。通用规则的最后一条要求子代理忽略总控规则。
- 官方文档没有说明 UserPromptSubmit 能否看到用户在输入框里打的 slash 命令。因此 `/worker:on`、`/worker:off` 对应的 skill 也会直接调用 MCP 工具，并先检查上下文里是否已有规则，避免重复。
- 状态文件的位置依赖 hook 进程继承了 `CLAUDE_CONFIG_DIR` 或 `CODEX_HOME`。如果没有继承，状态会落在 `$HOME` 下的默认目录。这一点还没有在 web 环境里验证；遇到问题时设置 `WORKER_STATE_DIR`。
- 没有做过真实模型的端到端测试。测试检查文件内容、插件和 skill 的发现、hook 逻辑，不检查模型是否遵守规则。
- 托管 hooks 需要 root 写入 `/etc/codex/requirements.toml`。不在托管配置里的 hook 需要逐个信任，而 codex web 没有信任界面。
- Claude 插件的 hooks 和 MCP 服务器分别运行 `node`（见 `plugins/worker/hooks/hooks.json` 和 `plugins/worker/.mcp.json`），它从 claude web 服务的 PATH 里找 node。claude web 仓库的 systemd 单元（`deploy/*.service`）把 Node 所在目录放在 PATH 最前面。因此对 Claude 目标，`NODE_BIN` 只影响安装器自己的检查，不决定插件运行时用哪个 node。这一点是按单元文件推断的，没有在运行中的服务里验证。
- claude web 如何把插件链接到各 collection，以及服务进程是否继承 `CLAUDE_CONFIG_DIR` 或 `CODEX_HOME`，没有在真实环境中验证。
- 官方子代理页面没有写出派工工具名，规则按 `spawn_agent` 编写。Codex 的 Linux 系统需求文件路径也没有写在官方 hooks 页面中，本项目按 `/etc/codex/requirements.toml` 编写，以 CI 中 `hooks/list` 的检查结果为准。

## 测试

```sh
shellcheck -s sh install.sh scripts/*.sh               # lint：shell 脚本（需要 shellcheck）
node --check plugins/worker/runtime/*.mjs tests/*.mjs  # lint：每个 .mjs 的语法
python3 -m py_compile tests/*.py                       # lint：Python 测试的语法
for f in install.sh scripts/*.sh; do sh -n "$f"; done  # 安装脚本和 scripts/ 下的辅助脚本语法
node --test tests/*.test.mjs                           # hook 和 MCP 服务器（Node 22 和 24 各运行一次）
python3 -m unittest discover -s tests -p 'test_*.py' -v  # 元数据、发布一致性、安装器、探针（需要 PyYAML）
```

两个探针需要本机有 `codex` 命令，并且不会启动模型回合。它们使用临时的 CODEX_HOME，不读取真实的 `~/.codex`；`--cwd` 可以省略，省略时使用临时目录：

```sh
python3 tests/check-codex-skills.py --cwd <项目目录> --expected-path /etc/codex/skills/worker/SKILL.md
python3 tests/check-codex-hooks.py --cwd <项目目录> --expect /opt/general-worker-mcp/runtime/worker-hook.mjs
```

GitHub Actions（`.github/workflows/ci.yml`）有五个 job，都不调用模型，也不需要登录，所有版本都已固定：

- `lint`：shellcheck（`-s sh`，检查 install.sh 和 scripts/*.sh）；`node --check` 检查每个 `.mjs`；`python -m py_compile` 检查 `tests/*.py`；解析每个 `.json` 文件和 CI 的 YAML。
- `tests`：Python 3.10 和 3.14 的矩阵，运行 Python 测试（元数据、发布一致性、安装器、探针）。
- `node-tests`：Node 22 和 24 的矩阵，运行 `node --test tests/*.test.mjs`（hook 和 MCP 服务器）。
- `claude-plugin`：安装固定版本的 Claude Code 2.1.295，执行 `claude plugin validate .` 和 `claude plugin validate plugins/worker`。然后在临时 HOME 中依次执行 install、status、uninstall，并检查插件出现和消失。
- `codex`：安装固定版本的 Codex 0.163.0，用 sudo 安装 codex 目标，使用默认的 `/etc/codex/requirements.toml` 和 `/etc/codex/skills`。检查 `/etc/codex/skills/worker/SKILL.md` 能被 skills/list 发现，`codex mcp list --json` 中有名为 `worker` 的条目（按名字精确匹配），托管 hook 能被列出；最后卸载，并确认托管块、admin skill 和 `worker` 条目都已删除。

## 给部署它的 AI 的检查清单

1. 确认环境：`node --version` 应为 22 或更新版本；记下 `command -v claude`、`command -v codex` 的路径；记下 claude web 的服务用户、它的 HOME 和 CLAUDE_CONFIG_DIR，以及 codex web 宿主的 CODEX_HOME（必须是已存在的目录）。
2. 把 `main` 分支克隆到 `/opt/general-worker-mcp-src`（见“入口”）。不要修改 claude web 或 codex web 的代码。
3. Claude web 安装：
   `cd /opt/general-worker-mcp-src && sudo -u <服务用户> env HOME=<服务 HOME> NODE_BIN=<node 绝对路径> CLAUDE_BIN=<claude 绝对路径> sh install.sh install claude`
4. 验证 Claude：
   `sudo -u <服务用户> env HOME=<服务 HOME> NODE_BIN=<node 绝对路径> CLAUDE_BIN=<claude 绝对路径> sh install.sh status claude` 退出码应为 0；
   `sudo -u <服务用户> env HOME=<服务 HOME> CLAUDE_CONFIG_DIR=<服务的 CLAUDE_CONFIG_DIR> <claude 绝对路径> plugin list` 中应有 `worker@general-worker-mcp`。
5. Codex web 安装：
   `cd /opt/general-worker-mcp-src && sudo env CODEX_HOME=<宿主 CODEX_HOME> NODE_BIN="$(command -v node)" CODEX_BIN="$(command -v codex)" sh install.sh install codex`
   如果提示 `requirements.toml` 已有 hooks，把打印出来的托管块手动合并进去，然后再执行一次。
6. 验证 Codex：
   `sudo env CODEX_HOME=<宿主 CODEX_HOME> NODE_BIN="$(command -v node)" CODEX_BIN="$(command -v codex)" sh install.sh status codex` 退出码应为 0；
   `env CODEX_HOME=<宿主 CODEX_HOME> codex mcp list --json` 中应有名为 `worker` 的条目（只看名字；参数里含 worker 的其他服务器不算）；
   `python3 tests/check-codex-hooks.py --expect /opt/general-worker-mcp/runtime/worker-hook.mjs` 退出码应为 0；
   `python3 tests/check-codex-skills.py --expected-path /etc/codex/skills/worker/SKILL.md` 退出码应为 0。
7. 不需要模型就能测试 hook：

```sh
echo '{"hook_event_name":"UserPromptSubmit","session_id":"smoke-1","prompt":"@worker status"}' \
  | WORKER_STATE_DIR="$(mktemp -d)" node /opt/general-worker-mcp/runtime/worker-hook.mjs --runtime codex
```

   输出应包含 `Worker 模式当前：关` 和 `Worker 模式：关`。
8. 在 claude web 新建会话，发送 `@worker status`，时间线里应出现 hook 行。
9. 在 codex web 的每个 collection 里勾选 `worker`，刷新或新开线程，发送 `@worker status`，“官方事件”里应有 hook 运行记录。
10. 告诉用户：
    - 改动了哪些位置：`/opt/general-worker-mcp`、`/etc/codex/skills/worker`、`/etc/codex/requirements.toml` 中的托管块、Claude 插件安装，以及 codex 的 MCP 配置。
    - 每个 codex collection 都要手动勾选 `worker`，新会话才生效。
    - 还没有做真实模型的端到端测试，请用户先在一个测试会话里试 `@worker status` 和一个小任务。
    - 卸载用 `sh install.sh uninstall <claude|codex>`，并提醒“限制与未验证项”中的注意点。

## 从 v1 升级

- Claude：`install claude` 会自动删除 v1 的 skill，即 `$CLAUDE_SKILLS_DIR/worker/SKILL.md`（内容含 `has("agent_id")` 的文件），连同 `SKILL.md.bak`；目录为空时也删除。同名文件如果不是 v1 的，保留并给出警告。
- Codex：`/etc/codex/skills/worker/SKILL.md` 原地替换，旧内容与新内容不同时保存为 `SKILL.md.bak`；卸载时一并删除。
- v1 的安装器默认把 Codex skill 放在 `$HOME/.agents/skills/worker/`，v2 不会删除那里的文件。如果那里还有 v1 的 `SKILL.md`，请手动删除，否则 Codex 可能同时列出两个 worker skill。
- v1 的 Codex 版每条消息都要写 `@worker`，只对当前这一轮生效。v1 的 Claude 版输入 `/worker` 后在整个会话内有效，只能开新会话来退出。v2 两家都开启一次即可，之后保持到关闭：Claude 用 `/worker:off` 或 `@worker off`，Codex 用 `@worker off`。仓库中的 `claude/` 目录已删除，Claude 版改为插件。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：组件和文件、数据流、状态模型、事件表、安全模型、设计决策和验证范围。
- [CHANGELOG.md](CHANGELOG.md)：版本记录，当前为 2.0.0。
- [LICENSE](LICENSE)：MIT 许可证，版权人为 General-Worker-MCP contributors。
