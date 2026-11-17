import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createConsent } from '../../lib/consent.js';
import { buildTool } from '../../lib/toolkit.js';
import { MANIFESTS } from '../../lib/registry.js';
import { failure } from '../../lib/errors.js';
import { fakeCtx, fakeApproval, fakeBridge, turnStart, exec } from './fakes.mjs';

const tool = name => Object.values(MANIFESTS).flatMap(m => m.tools).find(t => t.name === name);
const spec = kind => ({ name: `wechat_one_${kind}`, kind });

test('meta needs no approval; read asks once per turn; new turn asks again', async () => {
  const approval = fakeApproval();
  const ctx = fakeCtx({ approval });
  const consent = createConsent(ctx, { confirmReads: true });
  const session = {}; const agent = { session };
  assert.equal(await consent.approve(spec('meta'), exec(agent)), true);
  assert.equal(approval.calls.length, 0);
  turnStart(ctx, session, 1);
  assert.equal(await consent.approve(spec('read'), exec(agent)), true);
  assert.equal(await consent.approve(spec('read'), exec(agent)), true);
  assert.equal(approval.calls.length, 1);
  turnStart(ctx, session, 10);
  await consent.approve(spec('read'), exec(agent));
  assert.equal(approval.calls.length, 2);
});

test('concurrent reads in one turn share one pending approval', async () => {
  const approval = fakeApproval();
  const ctx = fakeCtx({ approval });
  const consent = createConsent(ctx, {});
  const session = {}; const agent = { session };
  turnStart(ctx, session);
  const [a, b] = await Promise.all([consent.approve(spec('read'), exec(agent)), consent.approve(spec('read'), exec(agent))]);
  assert.deepEqual([a, b], [true, true]);
  assert.equal(approval.calls.length, 1);
});

test('denied read is not cached; write always asks; side_effect denied without policy', async () => {
  const approval = fakeApproval(['rejected', 'allowed-once', 'allowed-once', 'allowed-once']);
  const ctx = fakeCtx({ approval });
  const consent = createConsent(ctx, {});
  const session = {}; const agent = { session };
  turnStart(ctx, session);
  assert.equal(await consent.approve(spec('read'), exec(agent)), false);
  assert.equal(await consent.approve(spec('read'), exec(agent)), true);
  assert.equal(await consent.approve(spec('write'), exec(agent)), true);
  assert.equal(await consent.approve(spec('write'), exec(agent)), true);
  assert.equal(approval.calls.length, 4);
  assert.equal(await consent.approve(spec('side_effect'), exec(agent)), false);
  const off = consent.setSideEffectPolicy(async ({ args }) => args?.to === 'allowed');
  assert.equal(await consent.approve(spec('side_effect'), exec(agent), { to: 'allowed' }), true);
  assert.equal(await consent.approve(spec('side_effect'), exec(agent), { to: 'other' }), false);
  off();
  assert.equal(await consent.approve(spec('side_effect'), exec(agent), { to: 'allowed' }), false);
});

test('confirmReads=false and Auto preset skip read prompts, never write prompts', async () => {
  const approval = fakeApproval();
  const consent = createConsent(fakeCtx({ approval }), { confirmReads: false });
  assert.equal(await consent.approve(spec('read'), exec({ session: {} })), true);
  assert.equal(approval.calls.length, 0);
  const session = {};
  const auto = createConsent(fakeCtx({ approval, presets: { current: s => (s === session ? 'auto' : 'ask') } }), {});
  assert.equal(await auto.approve(spec('read'), exec({ session })), true);
  assert.equal(approval.calls.length, 0);
  await auto.approve(spec('write'), exec({ session }));
  assert.equal(approval.calls.length, 1);
});

test('no approval service means no access', async () => {
  const consent = createConsent(fakeCtx({}), {});
  assert.equal(await consent.approve(spec('read'), exec({ session: {} })), false);
});

test('tool pipeline: invalid args and denial never reach the engine', async () => {
  const bridge = fakeBridge(() => ({ ok: true, data: { messages: [] } }));
  const approval = fakeApproval(['rejected']);
  const ctx = fakeCtx({ approval });
  const t = buildTool(tool('wechat_one_search'), { bridge, consent: createConsent(ctx, {}) });
  const agent = { session: {} };
  assert.equal((await t.execute({ query: 'a', extra: 1 }, exec(agent))).error.code, 'INVALID_ARGS');
  assert.equal((await t.execute({ query: '涨粉' }, exec(agent))).error.code, 'APPROVAL_REQUIRED');
  assert.equal(bridge.calls.length, 0);
});

test('tool pipeline: retryable read retried once; write never retried; result filtered', async () => {
  const consent = createConsent(fakeCtx({ approval: fakeApproval() }), { confirmReads: false });
  const flaky = fakeBridge((_a, _p, n) => (n === 1 ? failure('BUSY', 'busy', { retryable: true })
    : { ok: true, data: { messages: [{ message_id: 'm1', text: 'x', secret: 'wxid_1' }], total: 1 } }));
  const read = buildTool(tool('wechat_one_search'), { bridge: flaky, consent });
  const res = await read.execute({ query: '涨粉' }, exec({ session: {} }));
  assert.equal(flaky.calls.length, 2);
  assert.deepEqual(res, { ok: true, data: { messages: [{ message_id: 'm1', text: 'x' }], total: 1 } });

  const always = fakeBridge(() => failure('BUSY', 'busy', { retryable: true }));
  const write = buildTool(tool('wechat_one_refresh'), { bridge: always, consent });
  const out = await write.execute({}, exec({ session: {} }));
  assert.equal(out.error.code, 'BUSY');
  assert.equal(always.calls.length, 1);
});

test('status tool merges component health', async () => {
  const consent = createConsent(fakeCtx({}), {});
  const bridge = fakeBridge(() => ({ ok: true, data: { ready: true, next_action: 'ready' } }));
  const t = buildTool(tool('wechat_one_status'), { bridge, consent, health: () => ({ read: { ok: true } }) });
  const res = await t.execute({}, exec({ session: {} }));
  assert.deepEqual(res.data.components, { read: { ok: true } });
});
