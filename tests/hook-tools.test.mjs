// PreToolUse, PostToolUse and SessionStart behaviour of worker-hook.mjs, black-box via the CLI.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const HOOK = decodeURIComponent(new URL('../plugins/worker/runtime/worker-hook.mjs', import.meta.url).pathname);

const HEADER = '【Worker 模式规则（由 hook 注入；开启期间每轮都会出现）】';
const OFF_TEXT = 'Worker 模式已关闭。之前注入的总控规则不再适用：你可以直接读写文件、运行命令；需要时仍可派子代理。';
const DENY = {
  hookSpecificOutput: {
    hookEventName: 'PreToolUse',
    permissionDecision: 'deny',
    permissionDecisionReason: 'Worker 模式：主控只读，这一步请派给 worker（Agent 工具）。确实需要自己动手时，先调用 exit_worker_mode 退出 Worker 模式。',
  },
};

const BLOCKED = [
  'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Bash', 'PowerShell', 'Monitor',
  'mcp__github__push_files', 'mcp__github__get_file_contents',
];
const ALLOWED = [
  'Read', 'Agent', 'SendMessage', 'ToolSearch', 'Glob', 'Grep', 'TaskStop',
  'mcp__plugin_worker_worker__exit_worker_mode',
  'mcp__plugin_worker_worker__enter_worker_mode',
  'mcp__worker__exit_worker_mode',
  'mcp__worker__enter_worker_mode',
];
const ENTER_P = 'mcp__plugin_worker_worker__enter_worker_mode';
const EXIT_P = 'mcp__plugin_worker_worker__exit_worker_mode';

function freshDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wk-tools-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// Runs the hook once and returns stdout. Asserts exit code 0.
function run(dir, input, runtime = 'claude') {
  const env = { ...process.env, WORKER_STATE_DIR: dir };
  const r = spawnSync(process.execPath, [HOOK, '--runtime', runtime], {
    input: JSON.stringify(input),
    env,
    encoding: 'utf8',
    timeout: 15000,
  });
  assert.equal(r.status, 0, `exit code must be 0, stderr: ${r.stderr}`);
  return r.stdout;
}

function one(stdout) {
  assert.ok(stdout.endsWith('\n'), `stdout must end with a newline: ${JSON.stringify(stdout)}`);
  const body = stdout.slice(0, -1);
  assert.ok(!body.includes('\n'), 'stdout must be exactly one line');
  return JSON.parse(body);
}

const file = (dir, sid = 'sess') => path.join(dir, `${sid}.json`);
const state = (dir, sid = 'sess') => JSON.parse(fs.readFileSync(file(dir, sid), 'utf8'));
const rawState = (dir, sid = 'sess') => fs.readFileSync(file(dir, sid), 'utf8');
const exists = (dir, sid = 'sess') => fs.existsSync(file(dir, sid));

const prompt = (text, sid = 'sess') => ({ hook_event_name: 'UserPromptSubmit', session_id: sid, prompt: text });
const turnOn = (dir, runtime = 'claude') => run(dir, prompt('/worker 任务'), runtime);

function preInput(tool, extra = {}) {
  return { hook_event_name: 'PreToolUse', session_id: 'sess', tool_name: tool, tool_input: {}, ...extra };
}

function postInput(tool, toolInput, toolResponse = { isError: false }, extra = {}) {
  return { hook_event_name: 'PostToolUse', session_id: 'sess', tool_name: tool, tool_input: toolInput, tool_response: toolResponse, ...extra };
}

describe('PreToolUse (claude, main thread, mode on)', () => {
  for (const tool of BLOCKED) {
    test(`denies ${tool} with the exact deny JSON`, (t) => {
      const dir = freshDir(t);
      turnOn(dir);
      assert.deepEqual(one(run(dir, preInput(tool))), DENY);
    });
  }

  for (const tool of ALLOWED) {
    test(`never denies ${tool}`, (t) => {
      const dir = freshDir(t);
      turnOn(dir);
      assert.equal(run(dir, preInput(tool)), '');
    });
  }

  test('a non-empty agent_id (subagent) is never denied', (t) => {
    const dir = freshDir(t);
    turnOn(dir);
    for (const tool of BLOCKED) {
      assert.equal(run(dir, preInput(tool, { agent_id: 'agent-1' })), '', `subagent call to ${tool}`);
    }
  });

  test('an empty agent_id is not a subagent, so the call is denied', (t) => {
    const dir = freshDir(t);
    turnOn(dir);
    assert.deepEqual(one(run(dir, preInput('Bash', { agent_id: '' }))), DENY);
  });

  test('mode off: nothing is denied', (t) => {
    const dir = freshDir(t);
    for (const tool of BLOCKED) {
      assert.equal(run(dir, preInput(tool)), '', tool);
    }
  });
});

describe('PreToolUse (codex)', () => {
  test('codex never outputs anything on PreToolUse, even with mode on', (t) => {
    const dir = freshDir(t);
    turnOn(dir, 'codex');
    for (const tool of BLOCKED) {
      const r = spawnSync(process.execPath, [HOOK, '--runtime', 'codex'], {
        input: JSON.stringify(preInput(tool)),
        env: { ...process.env, WORKER_STATE_DIR: dir },
        encoding: 'utf8',
        timeout: 15000,
      });
      assert.equal(r.status, 0);
      assert.equal(r.stdout, '', tool);
    }
  });
});

describe('PostToolUse: enter_worker_mode', () => {
  test('claude: enter while off turns on by model; rules + systemMessage', (t) => {
    const dir = freshDir(t);
    const out = one(run(dir, postInput(ENTER_P, { reason: '用户要求' })));
    assert.deepEqual(Object.keys(out).sort(), ['hookSpecificOutput', 'systemMessage']);
    assert.deepEqual(Object.keys(out.hookSpecificOutput).sort(), ['additionalContext', 'hookEventName']);
    assert.equal(out.hookSpecificOutput.hookEventName, 'PostToolUse');
    assert.ok(out.hookSpecificOutput.additionalContext.startsWith(HEADER));
    assert.equal(out.systemMessage, 'Worker 模式：已由模型开启');
    const s = state(dir);
    assert.equal(s.on, true);
    assert.equal(s.by, 'model');
    assert.equal(s.notice, null);
  });

  test('codex: enter while off gives systemMessage only and turns on by model', (t) => {
    const dir = freshDir(t);
    const out = one(run(dir, postInput('mcp__worker__enter_worker_mode', { reason: '用户要求' }), 'codex'));
    assert.deepEqual(out, { systemMessage: 'Worker 模式：已由模型开启' });
    assert.equal(state(dir).on, true);
    assert.equal(state(dir).by, 'model');
  });

  for (const rt of ['claude', 'codex']) {
    test(`${rt}: enter while already on changes nothing and prints nothing`, (t) => {
      const dir = freshDir(t);
      turnOn(dir, rt);
      const before = rawState(dir);
      const tool = rt === 'claude' ? ENTER_P : 'mcp__worker__enter_worker_mode';
      assert.equal(run(dir, postInput(tool, { reason: 'x' }), rt), '');
      assert.equal(rawState(dir), before);
    });
  }

  test('enter with isError true while off: no write, no output', (t) => {
    const dir = freshDir(t);
    assert.equal(run(dir, postInput(ENTER_P, { reason: 'x' }, { isError: true, content: [] })), '');
    assert.equal(exists(dir), false);
  });

  test('claude subagent (non-empty agent_id) cannot enter: no write, no output', (t) => {
    const dir = freshDir(t);
    assert.equal(run(dir, postInput(ENTER_P, { reason: 'x' }, { isError: false }, { agent_id: 'sub-1' })), '');
    assert.equal(exists(dir), false);
  });
});

describe('PostToolUse: exit_worker_mode', () => {
  test('claude: exit while on turns off by model; rules-off text + systemMessage', (t) => {
    const dir = freshDir(t);
    turnOn(dir);
    const out = one(run(dir, postInput(EXIT_P, { reason: '用户要求' })));
    assert.deepEqual(out, {
      hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext: OFF_TEXT },
      systemMessage: 'Worker 模式：已由模型关闭',
    });
    const s = state(dir);
    assert.equal(s.on, false);
    assert.equal(s.by, 'model');
    assert.equal(s.notice, 'off');
    assert.ok(!Number.isNaN(Date.parse(s.updatedAt)));
  });

  test('codex: exit while on gives systemMessage only and turns off by model', (t) => {
    const dir = freshDir(t);
    turnOn(dir, 'codex');
    const out = one(run(dir, postInput('mcp__worker__exit_worker_mode', { reason: '用户要求' }), 'codex'));
    assert.deepEqual(out, { systemMessage: 'Worker 模式：已由模型关闭' });
    assert.equal(state(dir).on, false);
    assert.equal(state(dir).by, 'model');
    assert.equal(state(dir).notice, 'off');
  });

  test('claude: plain-name exit (mcp__worker__exit_worker_mode) also acts', (t) => {
    const dir = freshDir(t);
    turnOn(dir);
    const out = one(run(dir, postInput('mcp__worker__exit_worker_mode', { reason: '用户要求' })));
    assert.equal(out.systemMessage, 'Worker 模式：已由模型关闭');
    assert.equal(state(dir).on, false);
  });

  test('a reason reduced to surrounding whitespace is trimmed and a real reason acts', (t) => {
    const dir = freshDir(t);
    turnOn(dir);
    const out = one(run(dir, postInput(EXIT_P, { reason: '  用户要求  ' })));
    assert.equal(out.systemMessage, 'Worker 模式：已由模型关闭');
    assert.equal(state(dir).on, false);
  });

  for (const ti of [{}, { reason: '' }, { reason: '   ' }, { reason: 42 }, { reason: null }]) {
    test(`exit with tool_input ${JSON.stringify(ti)} is ignored while on`, (t) => {
      const dir = freshDir(t);
      turnOn(dir);
      const before = rawState(dir);
      assert.equal(run(dir, postInput(EXIT_P, ti)), '');
      assert.equal(rawState(dir), before);
    });
  }

  test('exit with isError true is ignored while on', (t) => {
    const dir = freshDir(t);
    turnOn(dir);
    const before = rawState(dir);
    assert.equal(run(dir, postInput(EXIT_P, { reason: '用户要求' }, { isError: true, content: [] })), '');
    assert.equal(rawState(dir), before);
  });

  test('exit while off: no write, no output', (t) => {
    const dir = freshDir(t);
    assert.equal(run(dir, postInput(EXIT_P, { reason: '用户要求' })), '');
    assert.equal(exists(dir), false);
  });

  test('claude subagent (non-empty agent_id) cannot exit: state unchanged, no output', (t) => {
    const dir = freshDir(t);
    turnOn(dir);
    const before = rawState(dir);
    assert.equal(run(dir, postInput(EXIT_P, { reason: '用户要求' }, { isError: false }, { agent_id: 'sub-1' })), '');
    assert.equal(rawState(dir), before);
  });
});

describe('PostToolUse: other tools', () => {
  for (const rt of ['claude', 'codex']) {
    test(`${rt}: a non-worker tool (Bash) while on gives no output and no write`, (t) => {
      const dir = freshDir(t);
      turnOn(dir, rt);
      const before = rawState(dir);
      assert.equal(run(dir, postInput('Bash', { command: 'ls' }), rt), '');
      assert.equal(rawState(dir), before);
    });
  }
});

describe('after a model exit', () => {
  for (const rt of ['claude', 'codex']) {
    test(`${rt}: the next plain prompt gets OFF text exactly once`, (t) => {
      const dir = freshDir(t);
      turnOn(dir, rt);
      const tool = rt === 'claude' ? EXIT_P : 'mcp__worker__exit_worker_mode';
      run(dir, postInput(tool, { reason: '用户要求' }), rt);
      const first = one(run(dir, prompt('继续做这个'), rt));
      assert.deepEqual(first, { hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: OFF_TEXT } });
      assert.equal(state(dir).on, false);
      assert.equal(state(dir).notice, null);
      assert.equal(run(dir, prompt('再继续'), rt), '');
    });
  }
});

describe('SessionStart', () => {
  for (const rt of ['claude', 'codex']) {
    test(`${rt}: while on, injects the same rules text as the ON command`, (t) => {
      const dir = freshDir(t);
      const on = one(turnOn(dir, rt));
      const start = one(run(dir, { hook_event_name: 'SessionStart', session_id: 'sess', source: 'startup' }, rt));
      assert.deepEqual(Object.keys(start), ['hookSpecificOutput']);
      assert.deepEqual(Object.keys(start.hookSpecificOutput).sort(), ['additionalContext', 'hookEventName']);
      assert.equal(start.hookSpecificOutput.hookEventName, 'SessionStart');
      assert.ok(start.hookSpecificOutput.additionalContext.startsWith(HEADER));
      assert.equal(start.hookSpecificOutput.additionalContext, on.hookSpecificOutput.additionalContext);
    });

    test(`${rt}: while off, no output`, (t) => {
      const dir = freshDir(t);
      assert.equal(run(dir, { hook_event_name: 'SessionStart', session_id: 'sess', source: 'startup' }, rt), '');
    });
  }
});
