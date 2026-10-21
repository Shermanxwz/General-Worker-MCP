// Protocol tests for plugins/worker/runtime/worker-mcp.mjs. Each test spawns the
// server and talks to it over stdin/stdout. Run with: node --test tests/mcp.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const SERVER = fileURLToPath(new URL('../plugins/worker/runtime/worker-mcp.mjs', import.meta.url));
const CJK = /[㐀-鿿]/;

const EXPECTED_SCHEMAS = {
  enter_worker_mode: {
    type: 'object',
    properties: { reason: { type: 'string' } },
    additionalProperties: false,
  },
  exit_worker_mode: {
    type: 'object',
    properties: { reason: { type: 'string' } },
    required: ['reason'],
    additionalProperties: false,
  },
};

// One server process plus a small JSON-RPC client over its stdio.
function startServer() {
  const child = spawn(process.execPath, [SERVER], { stdio: ['pipe', 'pipe', 'pipe'] });
  child.stdin.on('error', () => {}); // a dead server shows up as a timeout or a bad exit status
  let stderr = '';
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });

  const lines = createInterface({ input: child.stdout });
  const stdoutLines = []; // every raw line the server wrote, for the stdout-hygiene check
  lines.on('line', (line) => stdoutLines.push(line));
  const reader = lines[Symbol.asyncIterator](); // hands out stdout lines in order
  const stdoutClosed = new Promise((resolve) => lines.once('close', resolve));
  const exited = new Promise((resolve) => {
    child.once('exit', (code, signal) => resolve({ code, signal }));
  });
  let nextId = 1;
  let closing;

  // Next line the server wrote to stdout, parsed as JSON.
  async function receive(timeoutMs = 5000) {
    let timer;
    const timedOut = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`no stdout line within ${timeoutMs} ms; stderr: ${stderr}`)),
        timeoutMs,
      );
    });
    try {
      const { value, done } = await Promise.race([reader.next(), timedOut]);
      if (done) throw new Error(`server closed stdout; stderr: ${stderr}`);
      return JSON.parse(value);
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    stdoutLines,
    receive,
    send: (message) => child.stdin.write(`${JSON.stringify(message)}\n`),
    raw: (text) => child.stdin.write(`${text}\n`),
    // Sends a request and returns its reply, which must carry the same id.
    async request(method, params, id = nextId++) {
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
      const reply = await receive();
      assert.equal(reply.id, id, `reply to ${method}`);
      return reply;
    },
    // Ends stdin and resolves with the exit status once the process has finished.
    close() {
      closing ??= (async () => {
        const killer = setTimeout(() => child.kill(), 5000);
        child.stdin.end();
        const [status] = await Promise.all([exited, stdoutClosed]);
        clearTimeout(killer);
        return status;
      })();
      return closing;
    },
  };
}

// Runs one test body against a fresh server and always closes it afterwards.
async function withServer(body) {
  const server = startServer();
  try {
    return await body(server);
  } finally {
    await server.close();
  }
}

const callTool = (server, name, args) => server.request('tools/call', { name, arguments: args });

for (const version of ['2025-06-18', '2025-11-25', '2025-03-26', '2024-11-05']) {
  test(`initialize echoes the supported protocol version ${version}`, () =>
    withServer(async (server) => {
      const { result } = await server.request('initialize', {
        protocolVersion: version,
        capabilities: {},
        clientInfo: { name: 'test', version: '0.0.0' },
      });
      assert.equal(result.protocolVersion, version);
    }));
}

test('initialize answers 2025-06-18 for an unknown protocol version', () =>
  withServer(async (server) => {
    const { result } = await server.request('initialize', { protocolVersion: '1999-01-01', capabilities: {} });
    assert.equal(result.protocolVersion, '2025-06-18');
  }));

test('initialize answers 2025-06-18 when no protocol version is sent', () =>
  withServer(async (server) => {
    const { result } = await server.request('initialize', { capabilities: {} });
    assert.equal(result.protocolVersion, '2025-06-18');
  }));

test('initialize reports the tools capability, server name and Chinese instructions', () =>
  withServer(async (server) => {
    const { result } = await server.request('initialize', { protocolVersion: '2025-06-18', capabilities: {} });
    assert.deepEqual(result.capabilities.tools, { listChanged: false });
    assert.equal(result.serverInfo.name, 'worker');
    assert.equal(result.serverInfo.version, '2.0.0');
    assert.match(result.instructions, /enter_worker_mode/);
    assert.ok(CJK.test(result.instructions));
  }));

test('ping returns an empty result', () =>
  withServer(async (server) => {
    const reply = await server.request('ping');
    assert.deepEqual(reply.result, {});
    assert.equal(reply.error, undefined);
  }));

test('tools/list returns exactly the two worker tools with their schemas', () =>
  withServer(async (server) => {
    const { result } = await server.request('tools/list');
    assert.deepEqual(
      result.tools.map((tool) => tool.name),
      ['enter_worker_mode', 'exit_worker_mode'],
    );
    for (const tool of result.tools) {
      assert.deepEqual(tool.inputSchema, EXPECTED_SCHEMAS[tool.name], tool.name);
      assert.ok(CJK.test(tool.description), `${tool.name} description should be Chinese`);
    }
  }));

test('enter_worker_mode with a reason succeeds and includes the reason', () =>
  withServer(async (server) => {
    const { result } = await callTool(server, 'enter_worker_mode', { reason: '并行改三个模块' });
    assert.equal(result.isError, false);
    assert.equal(result.content[0].type, 'text');
    assert.equal(
      result.content[0].text,
      '已进入 Worker 模式：并行改三个模块。本会话会持续保持，直到用户关闭或你调用 exit_worker_mode。从现在起按 Worker 模式规则工作：主控只读，实际工作派给原生子代理。',
    );
  }));

test('enter_worker_mode works without a reason', () =>
  withServer(async (server) => {
    const bare = await callTool(server, 'enter_worker_mode');
    const empty = await callTool(server, 'enter_worker_mode', {});
    for (const { result } of [bare, empty]) {
      assert.equal(result.isError, false);
      assert.ok(result.content[0].text.startsWith('已进入 Worker 模式：用户要求。'), result.content[0].text);
    }
  }));

test('exit_worker_mode with a reason succeeds and includes the reason', () =>
  withServer(async (server) => {
    const { result } = await callTool(server, 'exit_worker_mode', { reason: '用户要求自己动手' });
    assert.equal(result.isError, false);
    assert.equal(
      result.content[0].text,
      '已退出 Worker 模式：用户要求自己动手。从现在起可以直接读写文件、运行命令。',
    );
  }));

test('exit_worker_mode without a reason is an error result', () =>
  withServer(async (server) => {
    for (const args of [undefined, {}, { reason: '' }, { reason: '   ' }, { reason: null }]) {
      const { result } = await callTool(server, 'exit_worker_mode', args);
      assert.equal(result.isError, true, JSON.stringify(args));
      assert.ok(CJK.test(result.content[0].text), result.content[0].text);
    }
  }));

test('an unknown tool returns an error result', () =>
  withServer(async (server) => {
    const { result } = await callTool(server, 'delete_everything', {});
    assert.equal(result.isError, true);
    assert.equal(result.content[0].text, '未知工具：delete_everything');
  }));

test('malformed tool calls return error results and the server keeps working', () =>
  withServer(async (server) => {
    const malformed = [
      { name: 'enter_worker_mode', arguments: { reason: 42 } },
      { name: 'enter_worker_mode', arguments: 'why' },
      { name: 'exit_worker_mode', arguments: { reason: 7 } },
      {},
    ];
    for (const params of malformed) {
      const { result } = await server.request('tools/call', params);
      assert.equal(result.isError, true, JSON.stringify(params));
    }
    assert.deepEqual((await server.request('ping')).result, {});
  }));

test('an unknown method with an id returns -32601 with the same id', () =>
  withServer(async (server) => {
    const reply = await server.request('resources/list', {}, 'req-42');
    assert.equal(reply.error.code, -32601);
    assert.equal(reply.result, undefined);
  }));

test('a garbage line returns -32700 with id null and the server keeps working', () =>
  withServer(async (server) => {
    server.raw('this is not json');
    const parseError = await server.receive();
    assert.equal(parseError.id, null);
    assert.equal(parseError.error.code, -32700);

    server.raw('{"jsonrpc":"2.0","id":5,"method":"ping"'); // truncated
    const truncated = await server.receive();
    assert.equal(truncated.id, null);
    assert.equal(truncated.error.code, -32700);

    assert.deepEqual((await server.request('ping')).result, {});
  }));

test('JSON that is not a request object returns -32600', () =>
  withServer(async (server) => {
    for (const raw of ['[]', '42', '{"jsonrpc":"2.0"}']) {
      server.raw(raw);
      const reply = await server.receive();
      assert.equal(reply.id, null, raw);
      assert.equal(reply.error.code, -32600, raw);
    }
    server.send({ jsonrpc: '2.0', id: 7 });
    const reply = await server.receive();
    assert.equal(reply.id, 7);
    assert.equal(reply.error.code, -32600);
    assert.deepEqual((await server.request('ping')).result, {});
  }));

test('notifications and client replies get no reply', () =>
  withServer(async (server) => {
    server.send({ jsonrpc: '2.0', method: 'notifications/initialized' });
    server.send({ jsonrpc: '2.0', method: 'notifications/cancelled', params: { requestId: 3 } });
    server.send({ jsonrpc: '2.0', method: 'no/such/notification' });
    server.send({ jsonrpc: '2.0', id: 99, result: {} });
    // Replies come back in order, so the first line after these must be the ping's reply.
    const reply = await server.request('ping');
    assert.deepEqual(reply.result, {});
  }));

test('blank lines are ignored', () =>
  withServer(async (server) => {
    server.raw('');
    server.raw('   ');
    assert.deepEqual((await server.request('ping')).result, {});
  }));

test('stdout carries only JSON-RPC lines and the process exits 0 when stdin closes', () =>
  withServer(async (server) => {
    server.raw('not json');
    assert.equal((await server.receive()).error.code, -32700);
    await server.request('initialize', { protocolVersion: '2025-11-25', capabilities: {} });
    server.send({ jsonrpc: '2.0', method: 'notifications/initialized' });
    await server.request('tools/list');
    await callTool(server, 'enter_worker_mode', { reason: '测试' });
    await callTool(server, 'exit_worker_mode', {}); // an error result, still a valid reply
    await server.request('no/such/method');

    const status = await server.close();
    assert.deepEqual(status, { code: 0, signal: null });
    // Six replies: the parse error, four requests answered, and the -32601. The notification adds none.
    assert.equal(server.stdoutLines.length, 6);
    for (const line of server.stdoutLines) {
      assert.equal(JSON.parse(line).jsonrpc, '2.0', line);
    }
  }));

test('the process exits 0 as soon as stdin closes', async () => {
  const status = await startServer().close();
  assert.deepEqual(status, { code: 0, signal: null });
});
