// 宿主加载探针：在真实 DSH 进程里确认 9 个工具已注册，并通过宿主执行管线调用 status / topic_search。
// 只用于本地开发验证（合成数据、隔离 profile），不随插件发布。
export const name = 'dsh-wechat-one-probe';
export const inject = ['tools', 'wechatOne'];

export function apply(ctx) {
  const timer = setTimeout(async () => {
    try {
      const names = ctx.tools.definitions().map(t => t.name).filter(n => n.startsWith('wechat_one_')).sort();
      if (names.length !== 9) throw new Error(`expected 9 tools, got ${names.join(',')}`);
      const run = async (toolName, args) => {
        const out = await ctx.tools.execute({
          calls: [{ type: 'tool-call', id: `probe-${toolName}`, name: toolName, arguments: JSON.stringify(args) }],
          signal: AbortSignal.timeout(60000),
        });
        return JSON.stringify(out);
      };
      const refresh = await run('wechat_one_refresh', { reindex_only: true });
      const status = await run('wechat_one_status', {});
      const topic = await run('wechat_one_topic_search', { topic: '涨粉技巧', keywords: ['起号', '完播率', '引流', '流量池'] });
      const bad = await run('wechat_one_search', { query: '涨粉', path: 'C:/x' });
      const checks = {
        writeNeedsApproval: refresh.includes('APPROVAL_REQUIRED'),
        status: status.includes('next_action') && status.includes('\\"ready\\":true'),
        topic: topic.includes('增长交流群') && topic.includes('完播率'),
        strict: bad.includes('INVALID_ARGS'),
      };
      if (!Object.values(checks).every(Boolean)) throw new Error('unexpected: ' + JSON.stringify({ checks, refresh, status, topic: topic.slice(0, 600), bad }));
      console.log('DSH_WECHAT_ONE_PROBE_OK ' + names.join(','));
      process.exit(0);
    } catch (error) {
      console.error('DSH_WECHAT_ONE_PROBE_FAILED ' + (error?.stack || error));
      process.exit(1);
    }
  }, 4000);
  ctx.effect(() => () => clearTimeout(timer));
}
