#!/usr/bin/env node
// Worker-mode hook handler for Claude Code and Codex (General-Worker-MCP v2).
// Usage: node worker-hook.mjs --runtime claude|codex   (default: claude)
// Reads one hook input object from stdin; writes at most one JSON object to stdout.
// Fail-open: on any problem stdout stays empty, a one-line note goes to stderr, exit code is 0.
import { readFileSync } from 'node:fs';
import { readState, sessionKey, stateDir, writeState } from './state.mjs';

const HEADER = '【Worker 模式规则（由 hook 注入；开启期间每轮都会出现）】';
const OFF_TEXT = 'Worker 模式已关闭。之前注入的总控规则不再适用：你可以直接读写文件、运行命令；需要时仍可派子代理。';
const DENY_REASON = 'Worker 模式：主控只读，这一步请派给 worker（Agent 工具）。确实需要自己动手时，先调用 exit_worker_mode 退出 Worker 模式。';
const MSG = {
  onUser: 'Worker 模式：已开启',
  offUser: 'Worker 模式：已关闭',
  onModel: 'Worker 模式：已由模型开启',
  offModel: 'Worker 模式：已由模型关闭',
  statusOn: 'Worker 模式：开',
  statusOff: 'Worker 模式：关',
};
// Used only when a rules file is missing or empty.
const FALLBACK = {
  common: '每条回复以〔Worker 模式〕开头。你是总控：拆分任务、派发给原生子代理、验收结果并汇报；不要自己改文件或运行写入命令。用户要求退出时调用 exit_worker_mode。',
  claude: '用 Agent 工具派发子代理；主线程的写入、命令、Worktree、定时、远程触发和其他 MCP 工具会被 hook 拦截。',
  codex: '用 spawn_agent 派发子代理；只做只读检查，不要 apply_patch 或运行写入命令。',
};
// Policy: while the mode is on, the orchestrator may read, dispatch, steer and stop workers, and talk to the user.
// Tools with side effects are delegated: file and shell writes, background monitors, worktree switches,
// scheduled runs and remote runs. Every mcp__ tool is blocked too, except the control tools (see isBlockedTool).
// PowerShell is the Windows counterpart of Bash.
const BLOCKED_TOOLS = new Set([
  'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Bash', 'PowerShell', 'Monitor',
  'EnterWorktree', 'ExitWorktree', 'CronCreate', 'CronDelete', 'ScheduleWakeup', 'RemoteTrigger',
]);
// Control tools are runtime-specific: Claude sees the plugin-scoped name, Codex the plain server name.
// Any other name, including the other runtime's and look-alikes from other servers, is an ordinary MCP tool.
const CONTROL_TOOL_RE = {
  claude: /^mcp__plugin_worker_worker__(enter|exit)_worker_mode$/,
  codex: /^mcp__worker__(enter|exit)_worker_mode$/,
};
const COMMAND_RE = /^[/@$]worker(?::([A-Za-z_-]+))?(?=\s|$)/i;
// Action words, compared after trailing punctuation is stripped. Any other first word is task text, so the mode turns ON.
const ACTION_WORDS = new Map([
  ['off', 'off'], ['stop', 'off'], ['关', 'off'], ['关闭', 'off'], ['退出', 'off'], ['停止', 'off'],
  ['status', 'status'], ['状态', 'status'],
  ['on', 'on'], ['start', 'on'], ['开', 'on'], ['开启', 'on'],
]);
// Namespaces accepted in /worker:<x>. `worker` (the plugin name) is also accepted but carries no action.
const NAMESPACE_ACTIONS = new Map([['on', 'on'], ['off', 'off'], ['stop', 'off'], ['status', 'status']]);
const TRAILING_PUNCTUATION_RE = /[.,!?;:。，！？；：、…]+$/;
const AUTHOR_LABEL = new Map([['user', '用户'], ['model', '模型']]);
const RULES_DIR = new URL('../rules/', import.meta.url);

function note(message) {
  process.stderr.write(`worker-hook: ${message}\n`);
}

function nonEmpty(value) {
  return typeof value === 'string' && value.trim() !== '';
}

function nowIso() {
  return new Date().toISOString();
}

function hookOutput(event, additionalContext, systemMessage) {
  const out = { hookSpecificOutput: { hookEventName: event, additionalContext } };
  if (systemMessage) out.systemMessage = systemMessage;
  return out;
}

function readRuleFile(name) {
  try {
    return readFileSync(new URL(name, RULES_DIR), 'utf8').trim() || null;
  } catch {
    return null;
  }
}

// Files are read on every call, so an edited rules file applies from the next hook call.
function rulesText(runtime) {
  const common = readRuleFile('common.md') || FALLBACK.common;
  const specific = readRuleFile(`${runtime}.md`) || FALLBACK[runtime];
  return `${HEADER}\n${common}\n\n${specific}`;
}

// Returns 'on' | 'off' | 'status' for a worker command, or null when the prompt is not a command.
function parseCommand(prompt) {
  const text = prompt.replace(/^\s+/, '');
  const match = COMMAND_RE.exec(text);
  if (!match) return null;
  const ns = (match[1] || '').toLowerCase();
  // A namespace decides the action by itself. Any other namespace (except the plugin name) is not a worker command.
  if (ns !== '' && ns !== 'worker') return NAMESPACE_ACTIONS.get(ns) ?? null;
  const rest = text.slice(match[0].length).trim();
  const word = (rest.split(/\s+/)[0] || '').replace(TRAILING_PUNCTUATION_RE, '').toLowerCase();
  return ACTION_WORDS.get(word) || 'on';
}

// Why: the hook still answers when the state cannot be saved. The warning tells the user the mode may not carry over.
function saveFailedMessage(runtime) {
  return `Worker 模式：状态无法保存（${stateDir(runtime)}），本轮之后可能不会保持`;
}

// Returns false when the state file cannot be written. The caller still answers and sends saveFailedMessage().
function saveState(runtime, sid, state) {
  try {
    writeState(runtime, sid, state);
    return true;
  } catch (err) {
    note(`state not saved: ${err.message}`);
    return false;
  }
}

function onUserPromptSubmit(input, runtime) {
  const sid = input.session_id;
  const state = readState(runtime, sid);
  const action = parseCommand(typeof input.prompt === 'string' ? input.prompt : '');

  if (action === 'status') {
    if (!state.on) return hookOutput('UserPromptSubmit', 'Worker 模式当前：关', MSG.statusOff);
    const who = AUTHOR_LABEL.get(state.by) ?? '未知';
    const line = `Worker 模式当前：开（${who} 于 ${state.updatedAt ?? '未知时间'} 开启）`;
    return hookOutput('UserPromptSubmit', `${line}\n${rulesText(runtime)}`, MSG.statusOn);
  }

  if (action === 'on' || action === 'off') {
    if (sessionKey(sid) === null) {
      note('no session_id: worker mode state is not stored');
      return null;
    }
    if (action === 'on') {
      const saved = saveState(runtime, sid, { on: true, updatedAt: nowIso(), by: 'user', notice: null });
      return hookOutput('UserPromptSubmit', rulesText(runtime), saved ? MSG.onUser : saveFailedMessage(runtime));
    }
    const saved = saveState(runtime, sid, { on: false, updatedAt: nowIso(), by: 'user', notice: null });
    return hookOutput('UserPromptSubmit', OFF_TEXT, saved ? MSG.offUser : saveFailedMessage(runtime));
  }

  if (state.on) return hookOutput('UserPromptSubmit', rulesText(runtime));
  // After a model exit, earlier turns still carry the rules, so the next prompt gets the off text once.
  if (state.notice === 'off') {
    const saved = saveState(runtime, sid, { ...state, notice: null });
    return hookOutput('UserPromptSubmit', OFF_TEXT, saved ? undefined : saveFailedMessage(runtime));
  }
  return null;
}

function onSessionStart(input, runtime) {
  if (!readState(runtime, input.session_id).on) return null;
  return hookOutput('SessionStart', rulesText(runtime));
}

// The control action ('enter' or 'exit') when toolName is this runtime's control tool, otherwise null.
function controlAction(runtime, toolName) {
  const match = typeof toolName === 'string' ? CONTROL_TOOL_RE[runtime]?.exec(toolName) : null;
  return match ? match[1] : null;
}

// Every MCP tool is blocked on the main thread, since any of them may write outside the repo.
// Only this runtime's control tools pass, so the mode can still be switched.
function isBlockedTool(runtime, name) {
  if (typeof name !== 'string' || controlAction(runtime, name) !== null) return false;
  return BLOCKED_TOOLS.has(name) || name.startsWith('mcp__');
}

// Claude only. A subagent carries a non-empty agent_id and does the work itself, so it is never denied.
// Codex gets no denial here and relies on the injected rules.
function onPreToolUse(input, runtime) {
  if (runtime !== 'claude' || nonEmpty(input.agent_id)) return null;
  if (!isBlockedTool(runtime, input.tool_name)) return null;
  if (!readState(runtime, input.session_id).on) return null;
  return {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: DENY_REASON,
    },
  };
}

// The MCP server marks failed calls with isError; a failed call never changes the stored mode.
function isErrorResponse(response) {
  return response !== null && typeof response === 'object' && !Array.isArray(response) && response.isError === true;
}

// Acts only on real transitions: exit only while ON, enter only while OFF.
// Both runtimes send the same shape. Codex's documented PostToolUse example uses hookSpecificOutput.additionalContext too.
function onPostToolUse(input, runtime) {
  const action = controlAction(runtime, input.tool_name);
  if (action === null) return null;
  if (runtime === 'claude' && nonEmpty(input.agent_id)) return null;
  if (isErrorResponse(input.tool_response)) return null;
  const sid = input.session_id;
  if (sessionKey(sid) === null) {
    note('no session_id: worker mode state is not stored');
    return null;
  }
  const state = readState(runtime, sid);
  if (action === 'exit') {
    const reason = input.tool_input && input.tool_input.reason;
    if (!state.on || !nonEmpty(reason)) return null;
    const saved = saveState(runtime, sid, { on: false, updatedAt: nowIso(), by: 'model', notice: 'off' });
    return hookOutput('PostToolUse', OFF_TEXT, saved ? MSG.offModel : saveFailedMessage(runtime));
  }
  if (state.on) return null;
  const saved = saveState(runtime, sid, { on: true, updatedAt: nowIso(), by: 'model', notice: null });
  return hookOutput('PostToolUse', rulesText(runtime), saved ? MSG.onModel : saveFailedMessage(runtime));
}

function handle(input, runtime) {
  switch (input.hook_event_name) {
    case 'UserPromptSubmit':
      return onUserPromptSubmit(input, runtime);
    case 'SessionStart':
      return onSessionStart(input, runtime);
    case 'PreToolUse':
      return onPreToolUse(input, runtime);
    case 'PostToolUse':
      return onPostToolUse(input, runtime);
    default:
      note(`unknown hook event: ${String(input.hook_event_name)}`);
      return null;
  }
}

function parseRuntime(argv) {
  let runtime = 'claude';
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--runtime') runtime = argv[i + 1];
    else if (argv[i].startsWith('--runtime=')) runtime = argv[i].slice('--runtime='.length);
  }
  return runtime;
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks).toString('utf8');
}

async function main() {
  const runtime = parseRuntime(process.argv.slice(2));
  // Read all of stdin before any early return, so the runner never writes into a closed pipe.
  const raw = await readStdin();
  if (runtime !== 'claude' && runtime !== 'codex') {
    note(`unknown runtime: ${String(runtime)}`);
    return;
  }
  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    note('stdin is not valid JSON');
    return;
  }
  if (input === null || typeof input !== 'object' || Array.isArray(input)) {
    note('stdin is not a JSON object');
    return;
  }
  const out = handle(input, runtime);
  if (out) process.stdout.write(`${JSON.stringify(out)}\n`);
}

// The reader may close the pipe before the output is written. Nobody is left to read it, so exit 0.
process.stdout.on('error', (err) => {
  note(`stdout error: ${err.message}`);
  process.exit(0);
});

main().catch((err) => {
  note(`internal error: ${err && err.message ? err.message : String(err)}`);
});
