// Command parsing and UserPromptSubmit behaviour of worker-hook.mjs, black-box via the CLI.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const HOOK = decodeURIComponent(new URL('../plugins/worker/runtime/worker-hook.mjs', import.meta.url).pathname);

const HEADER = '【Worker 模式规则（由 hook 注入；开启期间每轮都会出现）】';
const OFF_TEXT = 'Worker 模式已关闭。之前注入的总控规则不再适用：你可以直接读写文件、运行命令；需要时仍可派子代理。';
const MSG = {
  onUser: 'Worker 模式：已开启',
  offUser: 'Worker 模式：已关闭',
  statusOn: 'Worker 模式：开',
  statusOff: 'Worker 模式：关',
};

function freshDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wk-cmd-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// Runs the hook once. runtime === null means no --runtime flag.
function run(dir, input, runtime = 'claude') {
  const args = [HOOK];
  if (runtime !== null) args.push('--runtime', runtime);
  const env = { ...process.env, WORKER_STATE_DIR: dir };
  const r = spawnSync(process.execPath, args, {
    input: typeof input === 'string' ? input : JSON.stringify(input),
    env,
    encoding: 'utf8',
    timeout: 15000,
  });
  assert.equal(r.status, 0, `exit code must be 0, stderr: ${r.stderr}`);
  return r.stdout;
}

// Asserts exactly one JSON object + newline on stdout and returns it parsed.
function one(stdout) {
  assert.ok(stdout.endsWith('\n'), `stdout must end with a newline: ${JSON.stringify(stdout)}`);
  const body = stdout.slice(0, -1);
  assert.ok(!body.includes('\n'), 'stdout must be exactly one line');
  return JSON.parse(body);
}

const prompt = (text, sid = 'sess') => ({ hook_event_name: 'UserPromptSubmit', session_id: sid, prompt: text });
const file = (dir, sid = 'sess') => path.join(dir, `${sid}.json`);
const state = (dir, sid = 'sess') => JSON.parse(fs.readFileSync(file(dir, sid), 'utf8'));
const exists = (dir, sid = 'sess') => fs.existsSync(file(dir, sid));

// Turns the mode on as the model would: PostToolUse of enter_worker_mode (claude, main thread).
function modelEnter(dir, sid = 'sess') {
  return run(dir, {
    hook_event_name: 'PostToolUse',
    session_id: sid,
    tool_name: 'mcp__worker__enter_worker_mode',
    tool_input: { reason: '测试' },
    tool_response: { isError: false },
  });
}

function commonPrefix(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return a.slice(0, i);
}

describe('command matches: ON', () => {
  const ON = ['/worker 修复登录', '/worker:worker 任务', '@worker 任务', '$worker', '/worker', '/worker:on 任务', '/worker 开启', '  /worker 修复'];
  for (const p of ON) {
    test(`turns ON: ${JSON.stringify(p)}`, (t) => {
      const dir = freshDir(t);
      const out = one(run(dir, prompt(p)));
      assert.deepEqual(Object.keys(out).sort(), ['hookSpecificOutput', 'systemMessage']);
      assert.deepEqual(Object.keys(out.hookSpecificOutput).sort(), ['additionalContext', 'hookEventName']);
      assert.equal(out.hookSpecificOutput.hookEventName, 'UserPromptSubmit');
      assert.ok(out.hookSpecificOutput.additionalContext.startsWith(HEADER));
      assert.equal(out.systemMessage, MSG.onUser);
      const s = state(dir);
      assert.equal(s.on, true);
      assert.equal(s.by, 'user');
      assert.equal(s.notice, null);
    });
  }
});

describe('command matches: OFF', () => {
  const OFF = ['/worker off', '/worker:off', '@worker off', '$WORKER OFF', '  /worker off', '/worker 关闭', '/worker 退出'];
  for (const p of OFF) {
    test(`turns OFF: ${JSON.stringify(p)}`, (t) => {
      const dir = freshDir(t);
      const out = one(run(dir, prompt(p)));
      assert.deepEqual(out, {
        hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: OFF_TEXT },
        systemMessage: MSG.offUser,
      });
      const s = state(dir);
      assert.equal(s.on, false);
      assert.equal(s.by, 'user');
      assert.equal(s.notice, null);
    });
  }
});

describe('command matches: STATUS', () => {
  const STATUS = ['/worker status', '/worker:status', '/worker 状态'];
  for (const p of STATUS) {
    test(`STATUS while off: ${JSON.stringify(p)} reports off and writes nothing`, (t) => {
      const dir = freshDir(t);
      const out = one(run(dir, prompt(p)));
      assert.deepEqual(out, {
        hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: 'Worker 模式当前：关' },
        systemMessage: MSG.statusOff,
      });
      assert.equal(exists(dir), false);
    });
  }
});

describe('non-matches', () => {
  const NOT_COMMAND = ['/workers', 'worker off', '看看 /worker', '/worker-off', '/worker:'];
  for (const p of NOT_COMMAND) {
    test(`not a command, mode off: ${JSON.stringify(p)} gives no output and writes nothing`, (t) => {
      const dir = freshDir(t);
      assert.equal(run(dir, prompt(p)), '');
      assert.equal(exists(dir), false);
    });
    test(`not a command, mode on: ${JSON.stringify(p)} injects rules only and leaves state alone`, (t) => {
      const dir = freshDir(t);
      run(dir, prompt('/worker 任务'));
      const before = fs.readFileSync(file(dir), 'utf8');
      const out = one(run(dir, prompt(p)));
      assert.deepEqual(Object.keys(out), ['hookSpecificOutput']);
      assert.equal(out.hookSpecificOutput.hookEventName, 'UserPromptSubmit');
      assert.ok(out.hookSpecificOutput.additionalContext.startsWith(HEADER));
      assert.equal(fs.readFileSync(file(dir), 'utf8'), before);
    });
  }
});

describe('plain prompts', () => {
  for (const rt of ['claude', 'codex']) {
    test(`${rt}: plain prompt while on injects rules without systemMessage`, (t) => {
      const dir = freshDir(t);
      run(dir, prompt('/worker 任务'), rt);
      const out = one(run(dir, prompt('修复登录页的 bug'), rt));
      assert.deepEqual(Object.keys(out), ['hookSpecificOutput']);
      assert.deepEqual(Object.keys(out.hookSpecificOutput).sort(), ['additionalContext', 'hookEventName']);
      assert.ok(out.hookSpecificOutput.additionalContext.startsWith(HEADER));
    });
    test(`${rt}: plain prompt while off gives no output`, (t) => {
      const dir = freshDir(t);
      assert.equal(run(dir, prompt('修复登录页的 bug'), rt), '');
      assert.equal(exists(dir), false);
    });
  }
});

describe('status line wording', () => {
  test('STATUS while on by user: 用户 label, stored updatedAt, rules follow, no write', (t) => {
    const dir = freshDir(t);
    run(dir, prompt('/worker 任务'));
    const s = state(dir);
    const out = one(run(dir, prompt('/worker status')));
    const ctx = out.hookSpecificOutput.additionalContext;
    assert.ok(ctx.includes(`Worker 模式当前：开（用户 于 ${s.updatedAt} 开启）`), ctx);
    assert.ok(ctx.includes(HEADER));
    assert.equal(out.systemMessage, MSG.statusOn);
    assert.deepEqual(state(dir), s);
  });

  test('STATUS while on by model: 模型 label', (t) => {
    const dir = freshDir(t);
    modelEnter(dir);
    const s = state(dir);
    const out = one(run(dir, prompt('/worker:status')));
    const ctx = out.hookSpecificOutput.additionalContext;
    assert.ok(ctx.includes(`Worker 模式当前：开（模型 于 ${s.updatedAt} 开启）`), ctx);
    assert.equal(out.systemMessage, MSG.statusOn);
  });

  test('a user ON after a model ON re-labels the author as 用户', (t) => {
    const dir = freshDir(t);
    modelEnter(dir);
    run(dir, prompt('/worker 任务'));
    const s = state(dir);
    assert.equal(s.by, 'user');
    const ctx = one(run(dir, prompt('/worker status'))).hookSpecificOutput.additionalContext;
    assert.ok(ctx.includes(`Worker 模式当前：开（用户 于 ${s.updatedAt} 开启）`), ctx);
  });
});

describe('rules text per runtime', () => {
  test('claude rules name /worker:on, /worker:off and /worker:status', (t) => {
    const dir = freshDir(t);
    const ctx = one(run(dir, prompt('/worker 任务'), 'claude')).hookSpecificOutput.additionalContext;
    for (const s of ['/worker:on', '/worker:off', '/worker:status']) assert.ok(ctx.includes(s), `missing ${s}`);
  });

  test('codex rules name @worker 任务, @worker off and @worker status, not the slash form', (t) => {
    const dir = freshDir(t);
    const ctx = one(run(dir, prompt('@worker 任务'), 'codex')).hookSpecificOutput.additionalContext;
    for (const s of ['@worker 任务', '@worker off', '@worker status']) assert.ok(ctx.includes(s), `missing ${s}`);
    assert.ok(!ctx.includes('/worker:'), 'codex rules must not name the claude slash form');
  });

  test('the common part (shared text before the runtime part) names neither command form', (t) => {
    const cDir = freshDir(t);
    const xDir = freshDir(t);
    const claudeCtx = one(run(cDir, prompt('/worker 任务'), 'claude')).hookSpecificOutput.additionalContext;
    const codexCtx = one(run(xDir, prompt('/worker 任务'), 'codex')).hookSpecificOutput.additionalContext;
    const common = commonPrefix(claudeCtx, codexCtx);
    assert.ok(common.startsWith(HEADER), 'common text starts with the header');
    assert.ok(common.includes('〔Worker 模式〕'), 'common text has the reply marker');
    assert.ok(common.includes('exit_worker_mode'), 'common text names the MCP exit tool');
    for (const s of ['/worker', '@worker', '$worker']) {
      assert.ok(!common.includes(s), `common text must not mention ${s}`);
    }
  });

  test('injected rules stay under 1800 characters for both runtimes', (t) => {
    for (const rt of ['claude', 'codex']) {
      const dir = freshDir(t);
      const ctx = one(run(dir, prompt('/worker 任务'), rt)).hookSpecificOutput.additionalContext;
      const n = Array.from(ctx).length;
      assert.ok(n < 1800, `${rt}: ${n} characters`);
    }
  });
});

describe('rule practices per runtime', () => {
  for (const rt of ['claude', 'codex']) {
    test(`${rt}: rules carry the four practices and name the runtime's rework tool`, (t) => {
      const dir = freshDir(t);
      const ctx = one(run(dir, prompt('/worker 任务'), rt)).hookSpecificOutput.additionalContext;
      assert.ok(ctx.includes('共用背景'), 'shared background practice');
      assert.ok(ctx.includes('简短汇报'), 'short report practice');
      assert.ok(ctx.includes('保留上下文'), 'same-worker rework practice');
      assert.ok(ctx.includes('没有参与实现'), 'independent review practice');
      assert.ok(ctx.includes(rt === 'claude' ? 'SendMessage' : 'send_input'), 'rework tool name');
    });
  }
});

describe('command outputs per runtime', () => {
  for (const rt of ['claude', 'codex']) {
    test(`${rt}: ON, OFF and STATUS outputs have the exact shape and strings`, (t) => {
      const dir = freshDir(t);
      const on = one(run(dir, prompt('/worker 任务'), rt));
      assert.equal(on.systemMessage, MSG.onUser);
      assert.ok(on.hookSpecificOutput.additionalContext.startsWith(HEADER));

      const off = one(run(dir, prompt('/worker off'), rt));
      assert.deepEqual(off, {
        hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: OFF_TEXT },
        systemMessage: MSG.offUser,
      });

      const st = one(run(dir, prompt('/worker status'), rt));
      assert.deepEqual(st, {
        hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: 'Worker 模式当前：关' },
        systemMessage: MSG.statusOff,
      });
    });
  }
});
