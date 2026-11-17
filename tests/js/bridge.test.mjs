import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { makeBridge, run } from '../../lib/bridge.js';
import { stateDirectory, PACKAGE_ROOT } from '../../lib/config.js';

const node = process.execPath;

test('run(): timeout, output limit, cancel and start failure never throw', async () => {
  assert.equal((await run(node, ['-e', 'setTimeout(()=>{},5000)'], { timeoutMs: 200 })).error, 'TIMEOUT');
  assert.equal((await run(node, ['-e', 'process.stdout.write("x".repeat(5000))'], { maxOutput: 100 })).error, 'OUTPUT_LIMIT');
  const ac = new AbortController();
  const p = run(node, ['-e', 'setTimeout(()=>{},5000)'], { signal: ac.signal });
  ac.abort();
  assert.equal((await p).error, 'CANCELLED');
  assert.equal((await run('definitely-not-a-command-xyz', [])).error, 'START_FAILED');
  const ok = await run(node, ['-e', 'process.stdout.write("hi")']);
  assert.equal(ok.stdout, 'hi');
});

test('state directory inside the package is refused', async () => {
  assert.throws(() => stateDirectory({ stateDirectory: join(PACKAGE_ROOT, 'data') }), /PRIVATE_STATE_REQUIRED/);
  const bridge = makeBridge({ stateDirectory: join(PACKAGE_ROOT, 'x') });
  assert.equal((await bridge('status')).error.code, 'PRIVATE_STATE_REQUIRED');
  assert.equal(stateDirectory({}, {}, 'C:/home'), resolve('C:/home', '.dsh-wechat-one'));
});

test('bridge maps bad engine output and python absence to stable errors', async () => {
  const state = mkdtempSync(join(tmpdir(), 'wo-'));
  const probeOk = { code: 0, stdout: 'READY\n' };
  const mk = response => makeBridge({ stateDirectory: state }, {
    runner: async (_c, args) => (args.includes('-c') ? probeOk : response),
  });
  assert.equal((await mk({ code: 0, stdout: 'garbage' })('status')).error.code, 'ENGINE_UNAVAILABLE');
  assert.equal((await mk({ error: 'TIMEOUT' })('status')).error.code, 'TIMEOUT');
  assert.equal((await mk({ code: 0, stdout: '{"ok":false,"error":{"code":"BUSY","message":"m","retryable":true}}' })('status')).error.retryable, true);
  assert.equal((await mk({ code: 0, stdout: '{"ok":false,"error":{"code":"bad code!","message":"m"}}' })('status')).error.code, 'ENGINE_ERROR');
  assert.equal((await mk({ code: 0, stdout: '{"ok":true,"data":{"a":1}}' })('nope')).error.code, 'ENGINE_ERROR');
  const noPython = makeBridge({ stateDirectory: state }, { runner: async () => ({ code: 1, stdout: '' }) });
  assert.equal((await noPython('status')).error.code, 'PYTHON_UNAVAILABLE');
  rmSync(state, { recursive: true, force: true });
});

// ---- 真实 Python 引擎集成（合成数据）
let tmp; let mirror; let bridge;
before(() => {
  tmp = mkdtempSync(join(tmpdir(), 'wo-e2e-'));
  const res = spawnSync('python', ['-X', 'utf8', '-B', join(PACKAGE_ROOT, 'tests', 'python', 'fixtures.py'), join(tmp, 'decrypted')],
    { encoding: 'utf8' });
  assert.equal(res.status, 0, res.stderr);
  mirror = join(tmp, 'decrypted');
  process.env.WS_CONFIG = join(tmp, 'no-config.yaml');
  bridge = makeBridge({ stateDirectory: join(tmp, 'state'), sourcePath: mirror, sourceType: 'decrypted_dir' });
});
after(() => rmSync(tmp, { recursive: true, force: true }));

test('end to end through the real engine: refresh, topic search, context', async () => {
  const refreshed = await bridge('refresh', { reindex_only: true });
  assert.equal(refreshed.ok, true, JSON.stringify(refreshed));
  assert.ok(refreshed.data.messages > 50);
  const topic = await bridge('topic_search', { topic: '涨粉技巧', keywords: ['起号', '完播率', '引流', '流量池'] });
  assert.equal(topic.ok, true);
  assert.equal(topic.data.chunks[0].conversation, '增长交流群');
  const id = topic.data.chunks[0].messages[0].message_id;
  const ctx = await bridge('context', { message_id: id, before: 0, after: 2 });
  assert.equal(ctx.data.messages.length, 3);
  const status = await bridge('status');
  assert.equal(status.data.ready, true);
  assert.ok(!JSON.stringify(status).includes(mirror.replaceAll('\\', '\\\\')));
});
