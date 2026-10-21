// Per-session worker-mode state: one small JSON file per session id.
// Zero dependencies: node: built-ins only.
import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, join } from 'node:path';

const VERSION = 1;
const MAX_KEY_LENGTH = 200;

export function offState() {
  return { on: false, updatedAt: null, by: null, notice: null };
}

// File key for a session id: [A-Za-z0-9._-] only, other characters become "_", max 200 chars.
// Returns null when the id is missing or empty (no state is read or written then).
export function sessionKey(sessionId) {
  const text = typeof sessionId === 'number' ? String(sessionId) : sessionId;
  if (typeof text !== 'string' || text === '') return null;
  return text.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, MAX_KEY_LENGTH);
}

export function stateDir(runtime, env = process.env) {
  if (env.WORKER_STATE_DIR) return env.WORKER_STATE_DIR;
  const home = env.HOME || homedir();
  if (runtime === 'codex') {
    return join(env.CODEX_HOME || join(home, '.codex'), 'worker-mode');
  }
  return join(env.CLAUDE_CONFIG_DIR || join(home, '.claude'), 'worker-mode');
}

export function statePath(runtime, sessionId, env = process.env) {
  const key = sessionKey(sessionId);
  return key === null ? null : join(stateDir(runtime, env), `${key}.json`);
}

// Missing, unreadable or corrupt state counts as off.
export function readState(runtime, sessionId, env = process.env) {
  const file = statePath(runtime, sessionId, env);
  if (file === null) return offState();
  try {
    const data = JSON.parse(readFileSync(file, 'utf8'));
    if (!data || typeof data !== 'object' || data.version !== VERSION || typeof data.on !== 'boolean') {
      return offState();
    }
    return {
      on: data.on,
      updatedAt: typeof data.updatedAt === 'string' ? data.updatedAt : null,
      by: data.by === 'user' || data.by === 'model' ? data.by : null,
      notice: data.notice === 'off' ? 'off' : null,
    };
  } catch {
    return offState();
  }
}

// Atomic write: a temp file in the same directory, then rename, so a reader never sees a partial file.
// Callers check sessionKey first; a missing id throws instead of silently dropping the change.
export function writeState(runtime, sessionId, state, env = process.env) {
  const file = statePath(runtime, sessionId, env);
  if (file === null) throw new Error('writeState: session id is missing or empty');
  const dir = dirname(file);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const record = {
    version: VERSION,
    on: state.on === true,
    updatedAt: typeof state.updatedAt === 'string' ? state.updatedAt : new Date().toISOString(),
    by: state.by === 'model' ? 'model' : 'user',
    notice: state.notice === 'off' ? 'off' : null,
  };
  const tmp = join(dir, `.${basename(file)}.${process.pid}.${randomBytes(4).toString('hex')}.tmp`);
  try {
    writeFileSync(tmp, `${JSON.stringify(record)}\n`, { mode: 0o600 });
    renameSync(tmp, file);
  } catch (err) {
    try {
      unlinkSync(tmp);
    } catch {
      // temp file already gone
    }
    throw err;
  }
}
