// Python 引擎子进程桥：不经 shell、环境变量白名单、超时、输出上限、可取消、stderr 不外传。
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { ENGINE_DIR, engineConfig, stateDirectory } from './config.js';
import { failure, normalizeError } from './errors.js';

const DEFAULT_LIMIT = 4 * 1024 * 1024;
const GRACE_MS = 3000;
const SAFE_ENV = ['PATH', 'Path', 'PATHEXT', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'ComSpec', 'COMSPEC',
  'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'LOCALAPPDATA', 'APPDATA', 'XDG_DATA_HOME',
  'USERNAME', 'USER', 'LANG', 'LC_ALL', 'WS_CONFIG', 'WECHAT_DATA_DIR'];

let manifestCache;
/** 引擎 action 清单（kind / timeout），由 tools/gen_manifest.py 生成，测试保证与 Python 注册表一致。 */
export function engineManifest() {
  if (!manifestCache) manifestCache = JSON.parse(readFileSync(join(ENGINE_DIR, 'manifest.json'), 'utf8'));
  return manifestCache;
}

function childEnv() {
  const env = {};
  for (const key of SAFE_ENV) if (typeof process.env[key] === 'string') env[key] = process.env[key];
  env.PYTHONIOENCODING = 'utf-8';
  env.PYTHONUTF8 = '1';
  env.PYTHONDONTWRITEBYTECODE = '1';
  env.PYTHONPATH = ENGINE_DIR;
  return env;
}

/** 启动一个子进程并收集 stdout；永不 reject。 */
export function run(command, args, { stdin = '', signal, timeoutMs = 20000, maxOutput = DEFAULT_LIMIT } = {}) {
  return new Promise(done => {
    let child; let size = 0; let finished = false; let timer;
    const chunks = [];
    const finish = result => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal?.removeEventListener?.('abort', abort);
      done(result);
    };
    const stop = code => { try { child?.kill(); } catch { /* 已退出 */ } finish({ error: code }); };
    const abort = () => stop('CANCELLED');
    if (signal?.aborted) return finish({ error: 'CANCELLED' });
    try {
      child = spawn(command, args, { shell: false, windowsHide: true, cwd: ENGINE_DIR, env: childEnv(),
        stdio: ['pipe', 'pipe', 'pipe'] });
      child.on('error', () => finish({ error: 'START_FAILED' }));
      child.stdout.on('data', chunk => {
        size += chunk.length;
        if (size > maxOutput) return stop('OUTPUT_LIMIT');
        chunks.push(chunk);
      });
      child.stderr.on('data', () => { /* 诊断信息可能含本地路径，不转发 */ });
      child.on('close', code => finish({ code, stdout: Buffer.concat(chunks).toString('utf8') }));
      child.stdin.on('error', () => {});
      child.stdin.end(stdin);
      timer = setTimeout(() => stop('TIMEOUT'), timeoutMs);
      signal?.addEventListener?.('abort', abort, { once: true });
    } catch { finish({ error: 'START_FAILED' }); }
  });
}

const PROBE = 'import sys,sqlite3\nc=sqlite3.connect(":memory:")\nc.execute("create virtual table t using fts5(x)")\nprint("READY" if sys.version_info>=(3,10) else "OLD")';

export function makeBridge(config, { runner = run } = {}) {
  let selected;
  async function interpreter(signal) {
    if (selected) return selected;
    const candidates = config.pythonExecutable ? [[config.pythonExecutable, []]]
      : process.platform === 'win32' ? [['python', []], ['py', ['-3']]] : [['python3', []], ['python', []]];
    for (const [command, prefix] of candidates) {
      if (signal?.aborted) return null;
      const probe = await runner(command, [...prefix, '-X', 'utf8', '-B', '-c', PROBE], { signal, timeoutMs: 10000, maxOutput: 200 });
      if (probe.code === 0 && probe.stdout?.trim() === 'READY') return (selected = { command, prefix });
    }
    return null;
  }

  /**
   * 调用一个引擎 action。
   * @returns {Promise<{ok: boolean, data?: object, error?: object}>}
   */
  return async function bridge(action, params = {}, signal) {
    let state;
    try { state = stateDirectory(config); } catch { return failure('PRIVATE_STATE_REQUIRED'); }
    const spec = engineManifest().actions[action];
    if (!spec) return failure('ENGINE_ERROR', '未知的引擎操作。');
    const python = await interpreter(signal);
    if (!python) return failure(signal?.aborted ? 'CANCELLED' : 'PYTHON_UNAVAILABLE');
    const request = JSON.stringify({ action, params, request_id: randomUUID(), deadline_ms: spec.timeout_ms,
      config: engineConfig(config) });
    const result = await runner(python.command, [...python.prefix, '-X', 'utf8', '-B', '-m', 'wechat_one', '--state', state],
      { stdin: request + '\n', signal, timeoutMs: spec.timeout_ms + GRACE_MS });
    if (result.error) {
      if (result.error === 'START_FAILED') selected = undefined;
      return failure(['CANCELLED', 'TIMEOUT', 'OUTPUT_LIMIT'].includes(result.error) ? result.error : 'ENGINE_UNAVAILABLE');
    }
    try {
      const value = JSON.parse(result.stdout.trim().split('\n').pop());
      if (!value || typeof value !== 'object' || typeof value.ok !== 'boolean') throw new Error('shape');
      if (!value.ok) return normalizeError(value.error);
      if (!value.data || typeof value.data !== 'object' || Array.isArray(value.data)) throw new Error('shape');
      return { ok: true, data: value.data };
    } catch { return failure('ENGINE_UNAVAILABLE'); }
  };
}
