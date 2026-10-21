// Robustness of worker-hook.mjs: bad input, session ids, state file format, default directories.
// Black-box, via the command line and the JSON state files it writes.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const HOOK = decodeURIComponent(new URL('../plugins/worker/runtime/worker-hook.mjs', import.meta.url).pathname);

function mkRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wk-rob-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

// Environment without any inherited state-dir settings, plus the given overrides.
function cleanEnv(extra = {}) {
  const env = { ...process.env };
  for (const k of ['WORKER_STATE_DIR', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME']) delete env[k];
  return { ...env, ...extra };
}

function runWith(env, stdin, args = ['--runtime', 'claude']) {
  return spawnSync(process.execPath, [HOOK, ...args], {
    input: typeof stdin === 'string' ? stdin : JSON.stringify(stdin),
    env,
    encoding: 'utf8',
    timeout: 15000,
  });
}

// Runs the hook with WORKER_STATE_DIR=dir, asserts exit 0, returns stdout.
function runDir(dir, stdin, runtime = 'claude') {
  const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), stdin, ['--runtime', runtime]);
  assert.equal(r.status, 0, `exit code must be 0, stderr: ${r.stderr}`);
  return r.stdout;
}

function one(stdout) {
  assert.ok(stdout.endsWith('\n'), `stdout must end with a newline: ${JSON.stringify(stdout)}`);
  const body = stdout.slice(0, -1);
  assert.ok(!body.includes('\n'), 'stdout must be exactly one line');
  return JSON.parse(body);
}

const listDir = (dir) => (fs.existsSync(dir) ? fs.readdirSync(dir) : []);
const prompt = (text, sid = 'sess') => ({ hook_event_name: 'UserPromptSubmit', session_id: sid, prompt: text });
// Control tool names per runtime: Claude's plugin-scoped names and Codex's plain server names.
const CONTROL = {
  claude: { enter: 'mcp__plugin_worker_worker__enter_worker_mode', exit: 'mcp__plugin_worker_worker__exit_worker_mode' },
  codex: { enter: 'mcp__worker__enter_worker_mode', exit: 'mcp__worker__exit_worker_mode' },
};
const enterInput = (sid, runtime = 'claude') => ({
  hook_event_name: 'PostToolUse',
  ...(sid === undefined ? {} : { session_id: sid }),
  tool_name: CONTROL[runtime].enter,
  tool_input: { reason: '测试' },
  tool_response: { isError: false },
});

describe('bad input fails open: exit 0, empty stdout', () => {
  const cases = [
    ['malformed JSON', '{not json'],
    ['empty stdin', ''],
    ['whitespace-only stdin', '   \n'],
    ['JSON array', JSON.stringify([prompt('/worker 任务')])],
    ['JSON null', 'null'],
    ['JSON string', '"/worker 任务"'],
  ];
  for (const [name, stdin] of cases) {
    test(`${name}: exit 0, no stdout, no state written`, (t) => {
      const dir = path.join(mkRoot(t), 'state');
      const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), stdin);
      assert.equal(r.status, 0, r.stderr);
      assert.equal(r.stdout, '');
      assert.ok(r.stderr.split('\n').filter(Boolean).length <= 1, `stderr should be one line: ${r.stderr}`);
      assert.deepEqual(listDir(dir), []);
    });
  }

  test('unknown event with mode on: exit 0, no stdout, state untouched', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    runDir(dir, prompt('/worker 任务'));
    const before = fs.readFileSync(path.join(dir, 'sess.json'), 'utf8');
    const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), { hook_event_name: 'Stop', session_id: 'sess' });
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout, '');
    assert.equal(fs.readFileSync(path.join(dir, 'sess.json'), 'utf8'), before);
  });

  test('unknown --runtime value: exit 0, no stdout', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), prompt('/worker 任务'), ['--runtime', 'foo']);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout, '');
  });
});

describe('missing or empty session_id never writes a file', () => {
  const ids = [['missing', undefined], ['empty', '']];
  for (const [name, sid] of ids) {
    test(`UserPromptSubmit ON with ${name} session_id writes nothing`, (t) => {
      const dir = path.join(mkRoot(t), 'state');
      const input = { hook_event_name: 'UserPromptSubmit', prompt: '/worker 任务' };
      if (sid !== undefined) input.session_id = sid;
      const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), input);
      assert.equal(r.status, 0, r.stderr);
      assert.deepEqual(listDir(dir), []);
    });
    test(`PostToolUse enter with ${name} session_id writes nothing`, (t) => {
      const dir = path.join(mkRoot(t), 'state');
      const input = enterInput(sid);
      const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), input);
      assert.equal(r.status, 0, r.stderr);
      assert.deepEqual(listDir(dir), []);
    });
  }
});

describe('session_id is sanitized and writes stay inside the state dir', () => {
  test('../../etc/x becomes .._.._etc_x.json inside the state dir and nothing escapes', (t) => {
    const root = mkRoot(t);
    const dir = path.join(root, 'state');
    runDir(dir, prompt('/worker 任务', '../../etc/x'));
    assert.deepEqual(listDir(dir), ['.._.._etc_x.json']);
    assert.deepEqual(listDir(root), ['state']);
    assert.equal(fs.existsSync(path.join(dir, '..', '..', 'etc', 'x.json')), false);
  });

  test('a/b c becomes a_b_c.json', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    runDir(dir, prompt('/worker 任务', 'a/b c'));
    assert.deepEqual(listDir(dir), ['a_b_c.json']);
  });

  test('allowed characters [A-Za-z0-9._-] are kept as they are', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    runDir(dir, prompt('/worker 任务', 'Abc-9._x'));
    assert.deepEqual(listDir(dir), ['Abc-9._x.json']);
  });

  test('a session_id longer than 200 characters is capped at 200', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    runDir(dir, prompt('/worker 任务', 'a'.repeat(300)));
    assert.deepEqual(listDir(dir), [`${'a'.repeat(200)}.json`]);
  });
});

describe('state file format and permissions', () => {
  test('ON writes version 1 with the spec fields; file mode 0600, directory mode 0700', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    runDir(dir, prompt('/worker 任务'));
    const f = path.join(dir, 'sess.json');
    const s = JSON.parse(fs.readFileSync(f, 'utf8'));
    assert.deepEqual(Object.keys(s).sort(), ['by', 'notice', 'on', 'updatedAt', 'version']);
    assert.equal(s.version, 1);
    assert.equal(s.on, true);
    assert.equal(s.by, 'user');
    assert.equal(s.notice, null);
    assert.equal(new Date(s.updatedAt).toISOString(), s.updatedAt);
    assert.equal(fs.statSync(f).mode & 0o777, 0o600);
    assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
    assert.deepEqual(fs.readdirSync(dir), ['sess.json'], 'no temp files left behind');
  });

  test('OFF keeps the same format with on=false and mode 0600', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    runDir(dir, prompt('/worker 任务'));
    runDir(dir, prompt('/worker off'));
    const f = path.join(dir, 'sess.json');
    const s = JSON.parse(fs.readFileSync(f, 'utf8'));
    assert.deepEqual(Object.keys(s).sort(), ['by', 'notice', 'on', 'updatedAt', 'version']);
    assert.equal(s.version, 1);
    assert.equal(s.on, false);
    assert.equal(s.by, 'user');
    assert.equal(s.notice, null);
    assert.equal(fs.statSync(f).mode & 0o777, 0o600);
    assert.deepEqual(fs.readdirSync(dir), ['sess.json']);
  });
});

describe('corrupt or empty state file counts as off', () => {
  const corrupt = [['invalid JSON', '{not json'], ['empty file', '']];
  for (const [name, content] of corrupt) {
    test(`${name}: STATUS reports off`, (t) => {
      const dir = path.join(mkRoot(t), 'state');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'sess.json'), content);
      const out = one(runDir(dir, prompt('/worker status')));
      assert.equal(out.hookSpecificOutput.additionalContext, 'Worker 模式当前：关');
      assert.equal(out.systemMessage, 'Worker 模式：关');
      assert.equal(runDir(dir, prompt('普通问题')), '');
    });

    test(`${name}: exit does nothing, enter turns on by model`, (t) => {
      const dir = path.join(mkRoot(t), 'state');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'sess.json'), content);
      const exitOut = runDir(dir, {
        hook_event_name: 'PostToolUse',
        session_id: 'sess',
        tool_name: CONTROL.claude.exit,
        tool_input: { reason: '用户要求' },
        tool_response: { isError: false },
      });
      assert.equal(exitOut, '');
      assert.equal(fs.readFileSync(path.join(dir, 'sess.json'), 'utf8'), content);
      const out = one(runDir(dir, enterInput('sess')));
      assert.equal(out.systemMessage, 'Worker 模式：已由模型开启');
      const s = JSON.parse(fs.readFileSync(path.join(dir, 'sess.json'), 'utf8'));
      assert.equal(s.on, true);
      assert.equal(s.by, 'model');
    });
  }
});

describe('default state directory when WORKER_STATE_DIR is unset', () => {
  test('claude uses CLAUDE_CONFIG_DIR/worker-mode', (t) => {
    const root = mkRoot(t);
    const cfg = path.join(root, 'cfg');
    const home = path.join(root, 'home');
    fs.mkdirSync(cfg);
    fs.mkdirSync(home);
    const r = runWith(cleanEnv({ CLAUDE_CONFIG_DIR: cfg, HOME: home }), prompt('/worker 任务'), ['--runtime', 'claude']);
    assert.equal(r.status, 0, r.stderr);
    assert.ok(fs.existsSync(path.join(cfg, 'worker-mode', 'sess.json')));
    assert.equal(fs.existsSync(path.join(home, '.claude')), false);
  });

  test('codex uses CODEX_HOME/worker-mode and ignores CLAUDE_CONFIG_DIR', (t) => {
    const root = mkRoot(t);
    const codexHome = path.join(root, 'codex');
    const cfg = path.join(root, 'cfg');
    const home = path.join(root, 'home');
    fs.mkdirSync(cfg);
    fs.mkdirSync(home);
    const r = runWith(cleanEnv({ CODEX_HOME: codexHome, CLAUDE_CONFIG_DIR: cfg, HOME: home }), prompt('/worker 任务'), ['--runtime', 'codex']);
    assert.equal(r.status, 0, r.stderr);
    assert.ok(fs.existsSync(path.join(codexHome, 'worker-mode', 'sess.json')));
    assert.equal(fs.existsSync(path.join(cfg, 'worker-mode')), false);
  });

  test('claude without CLAUDE_CONFIG_DIR uses HOME/.claude/worker-mode', (t) => {
    const home = path.join(mkRoot(t), 'home');
    fs.mkdirSync(home);
    const r = runWith(cleanEnv({ HOME: home }), prompt('/worker 任务'), ['--runtime', 'claude']);
    assert.equal(r.status, 0, r.stderr);
    assert.ok(fs.existsSync(path.join(home, '.claude', 'worker-mode', 'sess.json')));
  });

  test('codex without CODEX_HOME uses HOME/.codex/worker-mode', (t) => {
    const home = path.join(mkRoot(t), 'home');
    fs.mkdirSync(home);
    const r = runWith(cleanEnv({ HOME: home }), prompt('/worker 任务'), ['--runtime', 'codex']);
    assert.equal(r.status, 0, r.stderr);
    assert.ok(fs.existsSync(path.join(home, '.codex', 'worker-mode', 'sess.json')));
  });
});

describe('state write failure still answers and warns', () => {
  const OFF_TEXT = 'Worker 模式已关闭。之前注入的总控规则不再适用：你可以直接读写文件、运行命令；需要时仍可派子代理。';
  // A regular file in place of the state directory makes every write fail (ENOTDIR), even for root.
  function unwritableDir(t) {
    const root = mkRoot(t);
    fs.writeFileSync(path.join(root, 'blocker'), 'a file, not a directory');
    return path.join(root, 'blocker', 'state');
  }
  const warning = (dir) => `Worker 模式：状态无法保存（${dir}），本轮之后可能不会保持`;

  for (const rt of ['claude', 'codex']) {
    test(`${rt}: ON still injects the rules, sends the warning, and writes nothing`, (t) => {
      const dir = unwritableDir(t);
      const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), prompt('/worker 任务'), ['--runtime', rt]);
      assert.equal(r.status, 0, r.stderr);
      assert.equal(r.stderr.split('\n').filter(Boolean).length, 1, `one stderr line: ${r.stderr}`);
      const out = one(r.stdout);
      assert.ok(out.hookSpecificOutput.additionalContext.startsWith('【Worker 模式规则'));
      assert.equal(out.systemMessage, warning(dir));
      assert.equal(fs.existsSync(dir), false);
    });

    test(`${rt}: OFF still answers with the off text and sends the warning`, (t) => {
      const dir = unwritableDir(t);
      const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), prompt('/worker off'), ['--runtime', rt]);
      assert.equal(r.status, 0, r.stderr);
      assert.deepEqual(one(r.stdout), {
        hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: OFF_TEXT },
        systemMessage: warning(dir),
      });
    });

    test(`${rt}: PostToolUse enter still injects the rules and sends the warning`, (t) => {
      const dir = unwritableDir(t);
      const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), enterInput('sess', rt), ['--runtime', rt]);
      assert.equal(r.status, 0, r.stderr);
      const out = one(r.stdout);
      assert.ok(out.hookSpecificOutput.additionalContext.startsWith('【Worker 模式规则'));
      assert.equal(out.systemMessage, warning(dir));
    });
  }
});

describe('no --runtime flag', () => {
  test('defaults to claude: main-thread Bash is denied while on', (t) => {
    const dir = path.join(mkRoot(t), 'state');
    runDir(dir, prompt('/worker 任务'));
    const r = runWith(cleanEnv({ WORKER_STATE_DIR: dir }), {
      hook_event_name: 'PreToolUse',
      session_id: 'sess',
      tool_name: 'Bash',
      tool_input: { command: 'ls' },
    }, []);
    assert.equal(r.status, 0, r.stderr);
    const out = one(r.stdout);
    assert.equal(out.hookSpecificOutput.hookEventName, 'PreToolUse');
    assert.equal(out.hookSpecificOutput.permissionDecision, 'deny');
  });
});
