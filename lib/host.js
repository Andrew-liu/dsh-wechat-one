// 宿主 API 适配：优先使用 DSH 提供的 defineTool / Schemastery；
// 测试或宿主缺失时退化为本地实现（只用于把参数规格转换成 JSON Schema）。
let defineToolImpl = null;
let SchemaImpl = null;

try {
  ({ defineTool: defineToolImpl } = await import('@deepseek-ai/dsh-tools'));
} catch { /* 非 DSH 环境 */ }
try {
  SchemaImpl = (await import('@deepseek-ai/schemastery')).default;
} catch { /* 非 DSH 环境 */ }

export const Schema = SchemaImpl;
export const hostAvailable = Boolean(defineToolImpl);

/** 把 dsh-tools 风格的值规格转换成 JSON Schema（本地兜底）。 */
export function toJsonSchema(spec) {
  if (!spec || typeof spec !== 'object') return {};
  const { required: _r, ...rest } = spec;
  if (rest.type === 'json') {
    const { type: _t, ...ann } = rest;
    return ann;
  }
  if (rest.type === 'array') return { ...rest, items: rest.items ? toJsonSchema(rest.items) : {} };
  if (rest.type === 'object') return { ...rest, ...objectSchema(rest.properties || {}, rest.additionalProperties) };
  return rest;
}

function objectSchema(properties, additionalProperties) {
  const out = { type: 'object', properties: {}, additionalProperties: Boolean(additionalProperties) };
  const required = [];
  for (const [key, value] of Object.entries(properties)) {
    out.properties[key] = toJsonSchema(value);
    if (value?.required === true) required.push(key);
  }
  if (required.length) out.required = required;
  return out;
}

/** defineTool：宿主可用时用宿主实现，否则构造等价的 ToolDefinition。 */
export function defineTool(options) {
  if (defineToolImpl) return defineToolImpl(options);
  return {
    name: options.name,
    description: options.description,
    parameters: objectSchema(options.parameters, true),
    output: { schema: toJsonSchema(options.output.schema), render: options.output.render },
    timeoutMs: options.timeoutMs,
    isConcurrencySafe: options.isConcurrencySafe,
    execute: (args, exec) => options.execute(args, exec),
  };
}

/** 把一个返回 disposer 的注册动作绑定到组件生命周期；disposer 只执行一次且永不抛出。 */
export function own(ctx, register, label) {
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    try { if (typeof undo === 'function') undo(); } catch { /* 已被宿主释放 */ }
  };
  let undo;
  if (typeof ctx?.effect === 'function') {
    ctx.effect(() => { undo = register(); return dispose; }, label);
  } else {
    undo = register();
  }
  return dispose;
}
