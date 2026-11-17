import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MANIFESTS, validateManifests } from '../../lib/registry.js';
import { engineManifest } from '../../lib/bridge.js';
import { validateArgs, pickResult, hostParameters } from '../../lib/toolkit.js';

const tool = name => Object.values(MANIFESTS).flatMap(m => m.tools).find(t => t.name === name);

test('manifests are valid against the engine manifest', () => {
  assert.deepEqual(validateManifests(MANIFESTS, engineManifest()), []);
  const names = Object.values(MANIFESTS).flatMap(m => m.tools.map(t => t.name));
  assert.equal(names.length, 13);
  assert.ok(names.every(n => n.startsWith('wechat_one_')));
});

test('registry detects duplicates, missing kind and kind mismatch', () => {
  const dup = { a: { id: 'a', tools: [{ ...tool('wechat_one_search') }] }, b: { id: 'b', tools: [{ ...tool('wechat_one_search') }] } };
  assert.ok(validateManifests(dup, engineManifest()).some(p => p.includes('重复')));
  const bad = { a: { id: 'a', tools: [{ ...tool('wechat_one_search'), kind: 'write' }] } };
  assert.ok(validateManifests(bad, engineManifest()).some(p => p.includes('不一致')));
  const none = { a: { id: 'a', tools: [{ ...tool('wechat_one_search'), kind: undefined }] } };
  assert.ok(validateManifests(none, engineManifest()).some(p => p.includes('风险等级')));
  const noAction = { a: { id: 'a', tools: [{ ...tool('wechat_one_search'), action: 'nope' }] } };
  assert.ok(validateManifests(noAction, engineManifest()).some(p => p.includes('缺少 action')));
});

test('validateArgs is strict', () => {
  const search = tool('wechat_one_search');
  assert.equal(validateArgs(search, { query: '涨粉' }), null);
  assert.match(validateArgs(search, { query: '涨粉', path: 'C:/x' }), /不支持的参数/);
  assert.match(validateArgs(search, {}), /缺少参数/);
  assert.match(validateArgs(search, { query: 'x'.repeat(101) }), /过长/);
  assert.match(validateArgs(search, { query: 'a', start_date: '2026-13-40' }), /YYYY-MM-DD/);
  assert.match(validateArgs(search, { query: 'a', start_date: '2026-09-02', end_date: '2026-09-01' }), /不能晚于/);
  assert.match(validateArgs(search, { query: 'a', conversation_ids: ['wxid_abc'] }), /格式无效/);
  const topic = tool('wechat_one_topic_search');
  assert.match(validateArgs(topic, { topic: '涨粉', keywords: Array(21).fill('a') }), /最多 20/);
  assert.match(validateArgs(topic, { topic: '涨粉', keywords: [] }), /至少 1/);
  assert.equal(validateArgs(topic, { topic: '涨粉', keywords: ['起号'], kinds: ['group'] }), null);
  assert.match(validateArgs(topic, { topic: '涨粉', keywords: ['起号'], kinds: ['official'] }), /取值无效/);
  assert.match(validateArgs(tool('wechat_one_setup'), { step: 'rm -rf' }), /取值无效/);
  assert.match(validateArgs(tool('wechat_one_context'), { message_id: 'm1', before: 99 }), /格式无效|过大/);
  const prep = tool('wechat_one_summary_prepare');
  assert.equal(validateArgs(prep, { group_set: '常看群' }), null);
  assert.match(validateArgs(prep, {}), /group_set/);
  assert.match(validateArgs(prep, { chats: ['a'], period: 'yearly' }), /取值无效/);
  assert.match(validateArgs(prep, { chats: ['a'], path: 'C:/x' }), /不支持的参数/);
  const rend = tool('wechat_one_summary_render');
  const run = 'run_summary_20260101000000_abc123';
  assert.match(validateArgs(rend, { run_id: run }), /stats_only/);
  assert.equal(validateArgs(rend, { run_id: run, stats_only: true }), null);
  assert.match(validateArgs(tool('wechat_one_summary_material'), { run_id: run, part: 11 }), /过大/);
});

test('pickResult keeps whitelisted fields only', () => {
  const search = tool('wechat_one_search');
  const raw = {
    messages: [{ message_id: 'm1', text: 'hi', raw_id: 'wxid_secret', path: 'C:/Users/x', conversation: '群' }],
    total: 1, source_path: 'C:/secret', coverage: { first_time: 'a', last_time: 'b', stale: false, root: 'C:/x' },
  };
  const out = pickResult(search.result, raw);
  assert.deepEqual(out, { messages: [{ message_id: 'm1', text: 'hi', conversation: '群' }], total: 1,
    coverage: { first_time: 'a', last_time: 'b', stale: false } });
  assert.ok(!JSON.stringify(out).includes('wxid_secret'));
});

test('hostParameters strips validation keywords the host does not know', () => {
  const params = hostParameters(tool('wechat_one_topic_search').parameters);
  assert.deepEqual(params.keywords, { type: 'array', description: params.keywords.description, items: { type: 'string' }, required: true });
  assert.ok(!('maxLength' in params.topic));
});
