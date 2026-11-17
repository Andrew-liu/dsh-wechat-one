// 功能组件的通用装配：注入共享服务，只在自己的 ctx 上注册本组工具。
export function featureComponent(id) {
  return {
    name: `dsh-wechat-one-${id}`,
    inject: ['tools', 'systemPrompt', 'wechatOne'],
    apply(ctx) {
      const api = ctx.get?.('wechatOne') ?? ctx.wechatOne;
      if (!api) return;
      api.mount(ctx, id);
      if (typeof ctx.effect === 'function') ctx.effect(() => () => api.unmount(id), `wechat-one.${id}`);
    },
  };
}
