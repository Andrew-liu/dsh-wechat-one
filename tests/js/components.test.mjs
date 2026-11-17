import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApi, apply as applyCore, SERVICE } from '../../components/core.js';
import * as setup from '../../components/setup.js';
import * as read from '../../components/read.js';
import * as persona from '../../components/persona.js';
import * as summary from '../../components/summary.js';
import { fakeCtx, fakeBridge } from './fakes.mjs';

const okBridge = () => fakeBridge(() => ({ ok: true, data: {} }));

test('core provides the shared service and the common prompt, registers no tools', () => {
  const ctx = fakeCtx();
  applyCore(ctx, {});
  assert.ok(ctx.provided[SERVICE]);
  assert.equal(ctx.registered.size, 0);
  assert.ok(ctx.sections.some(s => s.name === 'tool:wechat-one'));
  assert.deepEqual(ctx.provided[SERVICE].problems, []);
});

test('each feature component registers only its own tools on its own ctx', () => {
  const core = fakeCtx();
  const api = createApi(core, {}, { bridge: okBridge() });
  const expected = {
    setup: ['wechat_one_status', 'wechat_one_setup', 'wechat_one_refresh'],
    read: ['wechat_one_contacts', 'wechat_one_search', 'wechat_one_topic_search', 'wechat_one_context'],
    persona: ['wechat_one_persona_prepare', 'wechat_one_persona_render'],
    summary: ['wechat_one_summary_pending', 'wechat_one_summary_prepare', 'wechat_one_summary_material', 'wechat_one_summary_render'],
  };
  for (const [id, mod] of Object.entries({ setup, read, persona, summary })) {
    const ctx = fakeCtx({ services: { wechatOne: api } });
    mod.apply(ctx);
    assert.deepEqual([...ctx.registered.keys()].sort(), [...expected[id]].sort());
    assert.ok(ctx.sections.some(s => s.name === `tool:wechat-one-${id}`));
    assert.equal(mod.name, `dsh-wechat-one-${id}`);
    assert.deepEqual(mod.inject, ['tools', 'systemPrompt', 'wechatOne']);
  }
  assert.deepEqual(api.health(), { setup: { ok: true }, read: { ok: true }, persona: { ok: true }, summary: { ok: true } });
});

test('disabling one component leaves the others working', () => {
  const api = createApi(fakeCtx(), {}, { bridge: okBridge() });
  const readCtx = fakeCtx({ services: { wechatOne: api } });
  const personaCtx = fakeCtx({ services: { wechatOne: api } });
  read.apply(readCtx);
  persona.apply(personaCtx);
  personaCtx.dispose();
  assert.equal(personaCtx.registered.size, 0);
  assert.equal(readCtx.registered.size, 4);
  assert.deepEqual(Object.keys(api.health()), ['read']);
});

test('a failing component is isolated and reported in health', () => {
  const api = createApi(fakeCtx(), {}, { bridge: okBridge() });
  const broken = fakeCtx({ services: { wechatOne: api } });
  broken.tools.register = () => { throw new Error('host refused'); };
  assert.doesNotThrow(() => read.apply(broken));
  const ok = fakeCtx({ services: { wechatOne: api } });
  setup.apply(ok);
  assert.equal(ok.registered.size, 3);
  assert.deepEqual(api.health().read, { ok: false, code: 'COMPONENT_UNAVAILABLE' });
  assert.equal(api.mount(fakeCtx(), 'nope'), false);
});

test('feature component without core is a no-op', () => {
  const ctx = fakeCtx();
  assert.doesNotThrow(() => read.apply(ctx));
  assert.equal(ctx.registered.size, 0);
});

test('tool definitions carry timeouts from the engine manifest', () => {
  const api = createApi(fakeCtx(), {}, { bridge: okBridge() });
  const ctx = fakeCtx({ services: { wechatOne: api } });
  setup.apply(ctx);
  assert.equal(ctx.registered.get('wechat_one_refresh').timeoutMs, 1800000 + 10000);
  assert.equal(ctx.registered.get('wechat_one_status').timeoutMs, 20000 + 10000);
});
