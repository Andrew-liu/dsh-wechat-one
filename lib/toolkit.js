// 工具执行流水线：严格参数校验 → 审批 → 引擎调用（只读幂等操作可重试一次）→ 结果字段白名单。
// 各功能组件只声明 manifest，不自行实现这套流程。
import { defineTool, own } from './host.js';
import { engineManifest } from './bridge.js';
import { failure } from './errors.js';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_ARRAY = 200;

function checkValue(key, p, v) {
  switch (p.type) {
    case 'string':
      if (typeof v !== 'string') return `${key} 必须是字符串`;
      if (p.minLength !== undefined && v.length < p.minLength) return `${key} 过短`;
      if (p.maxLength !== undefined && v.length > p.maxLength) return `${key} 过长`;
      if (p.enum && !p.enum.includes(v)) return `${key} 取值无效`;
      if (p.format === 'date' && (!DATE_RE.test(v) || Number.isNaN(Date.parse(v + 'T00:00:00Z')))) return `${key} 需为 YYYY-MM-DD`;
      if (p.pattern && !new RegExp(p.pattern).test(v)) return `${key} 格式无效`;
      return null;
    case 'integer':
      if (!Number.isSafeInteger(v)) return `${key} 必须是整数`;
      if (p.minimum !== undefined && v < p.minimum) return `${key} 过小`;
      if (p.maximum !== undefined && v > p.maximum) return `${key} 过大`;
      return null;
    case 'boolean':
      return typeof v === 'boolean' ? null : `${key} 必须是布尔值`;
    case 'array': {
      if (!Array.isArray(v)) return `${key} 必须是数组`;
      if (p.minItems !== undefined && v.length < p.minItems) return `${key} 至少 ${p.minItems} 项`;
      if (v.length > (p.maxItems ?? MAX_ARRAY)) return `${key} 最多 ${p.maxItems ?? MAX_ARRAY} 项`;
      for (const item of v) { const e = checkValue(`${key}[]`, p.items || {}, item); if (e) return e; }
      return null;
    }
    case 'json':
      return v === undefined ? `${key} 缺失` : null;
    default:
      return `${key} 类型未声明`;
  }
}

/** 返回错误说明；通过时返回 null。拒绝未声明的字段。 */
export function validateArgs(spec, args) {
  if (args === undefined || args === null) args = {};
  if (typeof args !== 'object' || Array.isArray(args)) return '参数必须是对象';
  const params = spec.parameters || {};
  for (const key of Object.keys(args)) if (!Object.hasOwn(params, key)) return `不支持的参数 ${key}`;
  for (const [key, p] of Object.entries(params)) {
    const v = args[key];
    if (v === undefined || v === null) { if (p.required) return `缺少参数 ${key}`; continue; }
    const e = checkValue(key, p, v);
    if (e) return e;
  }
  return typeof spec.check === 'function' ? spec.check(args) : null;
}

/** 按白名单裁剪结果。shape: { key: true | 'json' | nestedShape }，数组按元素套用同一 shape。
 *  true 只允许原始值或原始值数组；'json' 允许任意纯 JSON（用于统计、schema 等结构化字段）。 */
export function pickResult(shape, value, depth = 0) {
  if (depth > 6) return undefined;
  if (shape === true) return primitiveOrPlain(value, depth);
  if (shape === 'json') return plainJson(value, depth);
  if (Array.isArray(value)) return value.slice(0, MAX_ARRAY).map(v => pickResult(shape, v, depth + 1)).filter(v => v !== undefined);
  if (!value || typeof value !== 'object') return undefined;
  const out = {};
  for (const [key, sub] of Object.entries(shape)) {
    if (!Object.hasOwn(value, key)) continue;
    const picked = pickResult(sub, value[key], depth + 1);
    if (picked !== undefined) out[key] = picked;
  }
  return out;
}

function primitiveOrPlain(value, depth) {
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return value;
  if (Array.isArray(value) && depth < 6) {
    return value.slice(0, MAX_ARRAY).filter(v => v === null || ['string', 'number', 'boolean'].includes(typeof v));
  }
  return undefined;
}

function plainJson(value, depth) {
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return value;
  if (depth > 10) return undefined;
  if (Array.isArray(value)) return value.slice(0, MAX_ARRAY).map(v => plainJson(v, depth + 1)).filter(v => v !== undefined);
  if (typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      const x = plainJson(v, depth + 1);
      if (x !== undefined) out[k] = x;
    }
    return out;
  }
  return undefined;
}

/** 把我们的参数规格转换为 dsh-tools 参数规格（去掉宿主不认识的校验关键字）。 */
export function hostParameters(params) {
  const strip = p => {
    const out = { type: p.type };
    if (p.description) out.description = p.description;
    if (p.enum) out.enum = p.enum;
    if (p.type === 'array') out.items = strip(p.items || { type: 'json' });
    if (p.required) out.required = true;
    return out;
  };
  return Object.fromEntries(Object.entries(params || {}).map(([k, p]) => [k, strip(p)]));
}

const OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    ok: { type: 'boolean', required: true },
    data: { type: 'json' },
    error: { type: 'json' },
  },
};

export function buildTool(spec, deps) {
  const { bridge, consent } = deps;
  const engine = engineManifest().actions[spec.action];
  return defineTool({
    name: spec.name,
    description: spec.description,
    parameters: hostParameters(spec.parameters),
    timeoutMs: (engine?.timeout_ms ?? 20000) + 10000,
    isConcurrencySafe: () => spec.kind === 'read' || spec.kind === 'meta',
    output: { schema: OUTPUT_SCHEMA, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    async execute(args, exec) {
      const problem = validateArgs(spec, args);
      if (problem) return failure('INVALID_ARGS', `参数不符合工具要求：${problem}`);
      if (!(await consent.approve(spec, exec, args))) return failure('APPROVAL_REQUIRED');
      const params = typeof spec.toParams === 'function' ? spec.toParams(args || {}) : (args || {});
      let result = await bridge(spec.action, params, exec?.signal);
      const canRetry = (spec.kind === 'read' || spec.kind === 'meta') && spec.idempotent !== false;
      if (!result.ok && result.error?.retryable && canRetry && !exec?.signal?.aborted) {
        result = await bridge(spec.action, params, exec?.signal);
      }
      if (!result.ok) return result;
      const data = pickResult(spec.result, result.data) ?? {};
      if (spec.includeHealth && typeof deps.health === 'function') data.components = deps.health();
      return { ok: true, data };
    },
  });
}

/** 在组件自己的 ctx 上注册一组工具；组件卸载时由 Cordis 一并释放。 */
export function registerTools(ctx, specs, deps) {
  for (const spec of specs) {
    const tool = buildTool(spec, deps);
    own(ctx, () => ctx.tools.register(tool), `wechat-one.tool:${spec.name}`);
  }
}
