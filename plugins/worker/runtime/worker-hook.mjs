#!/usr/bin/env node
// Worker-mode hook handler for Claude Code and Codex (General-Worker-MCP v2).
// Usage: node worker-hook.mjs --runtime claude|codex   (default: claude)
// Reads one hook input object from stdin; writes at most one JSON object to stdout.
// Fail-open: on any problem stdout stays empty, a one-line note goes to stderr, exit code is 0.
import { readFileSync } from 'node:fs';
import { readState, sessionKey, writeState } from './state.mjs';

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
  claude: '用 Agent 工具派发子代理；主线程的 Edit、Write、Bash 和其他 MCP 工具会被 hook 拦截。',
  codex: '用 spawn_agent 派发子代理；只做只读检查，不要 apply_patch 或运行写入命令。',
};
const BLOCKED_TOOLS = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Bash', 'Monitor']);
const CONTROL_TOOL_RE = /^mcp__.+__(enter|exit)_worker_mode$/;
const COMMAND_RE = /^[/@$]worker(?::([A-Za-z_-]+))?(?=\s|$)/i;
const ACTION_WORDS = new Map([
  ['off', 'off'], ['关闭', 'off'], ['退出', 'off'],
  ['status', 'status'], ['状态', 'status'],
  ['on', 'on'], ['开启', 'on'],
]);
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
  const first = (text.slice(match[0].length).trim().split(/\s+/)[0] || '').toLowerCase();
  const word = ACTION_WORDS.has(ns) ? ns : first;
  return ACTION_WORDS.get(word) || 'on';
}

function onPrompt(input, runtime) {
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
      writeState(runtime, sid, { on: true, updatedAt: nowIso(), by: 'user', notice: null });
      return hookOutput('UserPromptSubmit', rulesText(runtime), MSG.onUser);
    }
    writeState(runtime, sid, { on: false, updatedAt: nowIso(), by: 'user', notice: null });
    return hookOutput('UserPromptSubmit', OFF_TEXT, MSG.offUser);
  }

  if (state.on) return hookOutput('UserPromptSubmit', rulesText(runtime));
  if (state.notice === 'off') {
    writeState(runtime, sid, { ...state, notice: null });
    return hookOutput('UserPromptSubmit', OFF_TEXT);
  }
  return null;
}

function onSessionStart(input, runtime) {
  if (!readState(runtime, input.session_id).on) return null;
  return hookOutput('SessionStart', rulesText(runtime));
}

function isBlockedTool(name) {
  if (typeof name !== 'string' || CONTROL_TOOL_RE.test(name)) return false;
  return BLOCKED_TOOLS.has(name) || name.startsWith('mcp__');
}

function onPreToolUse(input, runtime) {
  if (runtime !== 'claude' || nonEmpty(input.agent_id)) return null;
  if (!isBlockedTool(input.tool_name)) return null;
  if (!readState(runtime, input.session_id).on) return null;
  return {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: DENY_REASON,
    },
  };
}

function isErrorResponse(response) {
  return response !== null && typeof response === 'object' && !Array.isArray(response) && response.isError === true;
}

function postOutput(runtime, additionalContext, systemMessage) {
  if (runtime === 'claude') return hookOutput('PostToolUse', additionalContext, systemMessage);
  return { systemMessage };
}

// Acts only on real transitions: exit only while ON, enter only while OFF.
function onPostToolUse(input, runtime) {
  const name = input.tool_name;
  if (typeof name !== 'string' || !CONTROL_TOOL_RE.test(name)) return null;
  if (runtime === 'claude' && nonEmpty(input.agent_id)) return null;
  if (isErrorResponse(input.tool_response)) return null;
  const sid = input.session_id;
  if (sessionKey(sid) === null) {
    note('no session_id: worker mode state is not stored');
    return null;
  }
  const state = readState(runtime, sid);
  if (name.endsWith('__exit_worker_mode')) {
    const reason = input.tool_input && input.tool_input.reason;
    if (!state.on || !nonEmpty(reason)) return null;
    writeState(runtime, sid, { on: false, updatedAt: nowIso(), by: 'model', notice: 'off' });
    return postOutput(runtime, OFF_TEXT, MSG.offModel);
  }
  if (state.on) return null;
  writeState(runtime, sid, { on: true, updatedAt: nowIso(), by: 'model', notice: null });
  return postOutput(runtime, rulesText(runtime), MSG.onModel);
}

function handle(input, runtime) {
  switch (input.hook_event_name) {
    case 'UserPromptSubmit':
      return onPrompt(input, runtime);
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
