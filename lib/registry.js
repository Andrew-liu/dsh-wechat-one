// 组件清单汇总与校验：工具名全局唯一、统一前缀、风险等级齐全、与引擎 action 的 kind 一致。
import setup from '../manifests/setup.js';
import read from '../manifests/read.js';
import persona from '../manifests/persona.js';
import summary from '../manifests/summary.js';

export const MANIFESTS = Object.freeze({ setup, read, persona, summary });
export const TOOL_PREFIX = 'wechat_one_';
export const KINDS = ['meta', 'read', 'write', 'side_effect'];
const ENGINE_KIND = { meta: 'read', read: 'read', write: 'write', side_effect: 'side_effect' };

/** 返回问题列表；空数组表示通过。 */
export function validateManifests(manifests, engine) {
  const problems = [];
  const names = new Set();
  const prompts = new Set();
  for (const [id, m] of Object.entries(manifests)) {
    if (m.id !== id) problems.push(`${id}: manifest.id 不一致`);
    if (m.prompt) {
      if (prompts.has(m.prompt.name)) problems.push(`${id}: prompt 段落名重复 ${m.prompt.name}`);
      prompts.add(m.prompt.name);
    }
    for (const t of m.tools || []) {
      if (!t.name?.startsWith(TOOL_PREFIX)) problems.push(`${t.name}: 缺少前缀 ${TOOL_PREFIX}`);
      if (names.has(t.name)) problems.push(`${t.name}: 工具名重复`);
      names.add(t.name);
      if (!KINDS.includes(t.kind)) problems.push(`${t.name}: 未声明风险等级`);
      if (!t.result || typeof t.result !== 'object') problems.push(`${t.name}: 未声明结果白名单`);
      if (typeof t.description !== 'string' || t.description.length < 10) problems.push(`${t.name}: 描述过短`);
      const action = engine?.actions?.[t.action];
      if (!action) problems.push(`${t.name}: 引擎缺少 action ${t.action}`);
      else if (action.kind !== ENGINE_KIND[t.kind]) problems.push(`${t.name}: kind ${t.kind} 与引擎 ${action.kind} 不一致`);
    }
  }
  return problems;
}
