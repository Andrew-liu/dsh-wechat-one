// 按风险等级审批。审批状态只存在 core 组件持有的这一个实例里。
//   meta        ：不读聊天正文（状态、诊断），不审批
//   read        ：读取聊天正文，同一 agent 同一轮用户输入内审批一次；confirmReads=false 或 DSH Auto 模式下放行
//   write       ：写本地文件 / 执行外部工具，每次单独审批
//   side_effect ：对外产生副作用（如后续的发消息），交给注册的策略；未注册策略时一律拒绝
const isObject = v => v !== null && (typeof v === 'object' || typeof v === 'function');

const READ_REASON = '读取本机微信聊天索引中的消息正文与联系人名称，并返回给当前模型用于回答本轮问题。'
  + '只读、不发消息、不修改微信；授权仅在本轮用户输入内有效。';

export function createConsent(ctx, config) {
  const turns = new WeakMap();   // session -> { seq, gen }
  const grants = new WeakMap();  // agent -> { session, seq, gen, promise, status }
  let sideEffectPolicy = null;
  let tracking = false;
  let disposed = false;

  if (typeof ctx?.on === 'function') {
    try {
      const off = ctx.on('session/event', (session, event) => {
        if (!isObject(session) || !event || typeof event.type !== 'string') return;
        if (event.type === 'turn/start') turns.set(session, { seq: event.seq, gen: 0 });
        else if (event.type === 'turn/end') turns.delete(session);
        else if (event.type === 'agent/inbox/spliced' && Array.isArray(event.data?.inserted) && event.data.inserted.length) {
          const t = turns.get(session);
          if (t) t.gen += 1;
        }
      });
      tracking = true;
      if (typeof ctx.effect === 'function') {
        ctx.effect(() => () => { disposed = true; if (typeof off === 'function') off(); }, 'wechat-one.consent');
      }
    } catch { tracking = false; }
  }

  const approvalService = () => {
    const service = ctx?.get?.('approval');
    return typeof service?.request === 'function' ? service : null;
  };

  function isAuto(agent) {
    try {
      const presets = ctx?.get?.('permissionPresets');
      return isObject(agent?.session) && typeof presets?.current === 'function' && presets.current(agent.session) === 'auto';
    } catch { return false; }
  }

  function currentTurn(agent) {
    if (!tracking || !isObject(agent?.session)) return null;
    const t = turns.get(agent.session);
    return t ? { session: agent.session, seq: t.seq, gen: t.gen } : null;
  }

  const sameTurn = (a, b) => Boolean(a && b && a.session === b.session && a.seq === b.seq && a.gen === b.gen);

  async function ask(agent, toolName, execution, reason) {
    const service = approvalService();
    if (!service) return false;
    try {
      const outcome = await service.request({ agent, toolName, callId: execution?.callId, reason, signal: execution?.signal });
      return outcome === 'allowed-once';
    } catch { return false; }
  }

  return {
    /** 注册副作用策略（供后续 send 组件使用）；返回注销函数。 */
    setSideEffectPolicy(policy) {
      sideEffectPolicy = policy;
      return () => { if (sideEffectPolicy === policy) sideEffectPolicy = null; };
    },

    async approve(spec, execution, args) {
      if (disposed || execution?.signal?.aborted) return false;
      const kind = spec.kind;
      if (kind === 'meta') return true;
      const agent = execution?.agent;
      if (kind === 'side_effect') {
        if (typeof sideEffectPolicy !== 'function') return false;
        try { return (await sideEffectPolicy({ spec, args, execution, ask: reason => ask(agent, spec.name, execution, reason) })) === true; }
        catch { return false; }
      }
      const reason = typeof spec.approvalReason === 'function' ? spec.approvalReason(args) : (spec.approvalReason || READ_REASON);
      if (kind === 'write') return ask(agent, spec.name, execution, reason);
      if (kind !== 'read') return false;
      if (config.confirmReads === false || isAuto(agent)) return true;
      const turn = currentTurn(agent);
      if (turn && isObject(agent)) {
        const old = grants.get(agent);
        if (old && sameTurn(old, turn)) {
          if (old.status === 'granted') return true;
          if (old.status === 'pending') return old.promise;
        }
        const record = { ...turn, status: 'pending' };
        record.promise = ask(agent, spec.name, execution, READ_REASON).then(ok => {
          record.status = ok && sameTurn(record, currentTurn(agent)) ? 'granted' : 'denied';
          if (record.status !== 'granted' && grants.get(agent) === record) grants.delete(agent);
          return record.status === 'granted';
        });
        grants.set(agent, record);
        return record.promise;
      }
      return ask(agent, spec.name, execution, READ_REASON);
    },
  };
}
