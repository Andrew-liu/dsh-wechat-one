// 测试替身：宿主 ctx、审批服务、引擎 bridge。
export function fakeCtx({ approval, presets, services = {} } = {}) {
  const listeners = new Map();
  const tools = new Map();
  const sections = [];
  const effects = [];
  const provided = {};
  const ctx = {
    tools: {
      register(def) {
        if (tools.has(def.name)) throw new Error(`duplicate ${def.name}`);
        tools.set(def.name, def);
        return () => tools.delete(def.name);
      },
    },
    systemPrompt: { section(s) { sections.push(s); return () => sections.splice(sections.indexOf(s), 1); } },
    on(name, fn) { listeners.set(name, fn); return () => listeners.delete(name); },
    effect(fn) { const dispose = fn(); effects.push(dispose); return dispose; },
    provide(name, value) { provided[name] = value; },
    get(name) {
      if (name === 'approval') return approval;
      if (name === 'permissionPresets') return presets;
      return services[name] ?? provided[name];
    },
    emit(name, ...args) { listeners.get(name)?.(...args); },
    dispose() { for (const d of effects.splice(0)) d?.(); },
    registered: tools, sections, provided,
  };
  return ctx;
}

export function fakeApproval(answers = []) {
  const calls = [];
  return {
    calls,
    async request(req) { calls.push(req); return answers.length ? answers.shift() : 'allowed-once'; },
  };
}

export function fakeBridge(responder) {
  const calls = [];
  const bridge = async (action, params) => { calls.push({ action, params }); return responder(action, params, calls.length); };
  bridge.calls = calls;
  return bridge;
}

export function turnStart(ctx, session, seq = 1) {
  session.seq = seq + 1;
  ctx.emit('session/event', session, { type: 'turn/start', seq });
}

export function exec(agent, extra = {}) {
  return { agent, callId: 'c1', signal: new AbortController().signal, ...extra };
}
