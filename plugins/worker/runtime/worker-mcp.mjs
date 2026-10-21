// Worker MCP server: a minimal Model Context Protocol server over stdio.
// Newline-delimited JSON-RPC 2.0 with zero dependencies. stdout carries only
// protocol messages; diagnostics go to stderr. The server keeps no state: the
// worker hooks own the per-session mode and react to these tool calls.
import { createInterface } from 'node:readline';

const SUPPORTED_VERSIONS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'];
const DEFAULT_VERSION = '2025-06-18';

const INSTRUCTIONS =
  '本服务提供 enter_worker_mode 和 exit_worker_mode 两个工具，用于进入或退出 Worker 模式；仅在用户明确要求时调用，退出时必须填写 reason。';

const TOOLS = [
  {
    name: 'enter_worker_mode',
    description: '进入 Worker 模式：主代理变为只读主控，把工作派给原生子代理执行。仅在用户要求时调用。',
    inputSchema: {
      type: 'object',
      properties: { reason: { type: 'string' } },
      additionalProperties: false,
    },
  },
  {
    name: 'exit_worker_mode',
    description:
      '退出 Worker 模式：主代理恢复直接读写文件、运行命令。在用户要求停止派发、自己动手或退出时调用，或在用户同意后调用。',
    inputSchema: {
      type: 'object',
      properties: { reason: { type: 'string' } },
      required: ['reason'],
      additionalProperties: false,
    },
  },
];

const isObject = (value) => typeof value === 'object' && value !== null && !Array.isArray(value);
const isValidId = (id) => typeof id === 'string' || typeof id === 'number' || id === null;

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function reply(id, result) {
  send({ jsonrpc: '2.0', id, result });
}

function fail(id, code, message) {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

function log(message) {
  process.stderr.write(`[worker-mcp] ${message}\n`);
}

function toolResult(text, isError = false) {
  return { content: [{ type: 'text', text }], isError };
}

function callTool(params) {
  const name = params?.name;
  if (typeof name !== 'string') return toolResult('缺少工具名（name）。', true);
  if (name !== 'enter_worker_mode' && name !== 'exit_worker_mode') {
    return toolResult(`未知工具：${name}`, true);
  }

  const args = params.arguments ?? {};
  if (!isObject(args)) return toolResult('参数（arguments）必须是对象。', true);
  const { reason } = args;
  if (reason != null && typeof reason !== 'string') return toolResult('reason 必须是字符串。', true);
  const why = typeof reason === 'string' ? reason.trim() : '';

  if (name === 'enter_worker_mode') {
    return toolResult(
      `已进入 Worker 模式：${why || '用户要求'}。本会话会持续保持，直到用户关闭或你调用 exit_worker_mode。请按注入的总控规则工作。`,
    );
  }
  if (!why) {
    return toolResult('退出 Worker 模式需要提供 reason（退出原因），例如：用户要求自己动手。', true);
  }
  return toolResult(`已退出 Worker 模式：${why}。从现在起可以直接读写文件、运行命令。`);
}

function handleRequest(method, params, id) {
  switch (method) {
    case 'initialize': {
      const requested = params?.protocolVersion;
      return reply(id, {
        protocolVersion: SUPPORTED_VERSIONS.includes(requested) ? requested : DEFAULT_VERSION,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'worker', version: '2.0.0' },
        instructions: INSTRUCTIONS,
      });
    }
    case 'ping':
      return reply(id, {});
    case 'tools/list':
      return reply(id, { tools: TOOLS });
    case 'tools/call':
      return reply(id, callTool(params));
    default:
      return fail(id, -32601, `Method not found: ${method}`);
  }
}

function handleMessage(msg) {
  if (!isObject(msg)) return fail(null, -32600, 'Invalid Request'); // batches are not supported
  if (typeof msg.method !== 'string') {
    // The server sends no requests of its own, so replies from the client are ignored.
    if ('result' in msg || 'error' in msg) return;
    return fail(isValidId(msg.id) ? msg.id : null, -32600, 'Invalid Request');
  }
  if (msg.id === undefined) return; // notification: never answered, even when unknown
  if (!isValidId(msg.id)) return fail(null, -32600, 'Invalid Request');
  return handleRequest(msg.method, msg.params, msg.id);
}

function handleLine(line) {
  if (line.trim() === '') return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (err) {
    log(`parse error: ${err.message}`);
    return fail(null, -32700, 'Parse error');
  }
  try {
    handleMessage(msg);
  } catch (err) {
    log(`internal error: ${err.stack}`);
    if (isObject(msg) && isValidId(msg.id)) fail(msg.id, -32603, 'Internal error');
  }
}

process.stdout.on('error', (err) => {
  // The client has gone away, so there is nobody left to answer.
  log(`stdout error: ${err.message}`);
  process.exit(err.code === 'EPIPE' ? 0 : 1);
});

createInterface({ input: process.stdin, crlfDelay: Infinity }).on('line', handleLine);
