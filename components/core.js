// core 组件：唯一配置、唯一 bridge 与审批状态，经 ctx.provide('wechatOne') 共享给功能组件。
// 自身不注册工具；core 卸载时，依赖它的功能组件随之停止。
import { Schema } from '../lib/host.js';
import { buildConfigSchema, normalizeConfig } from '../lib/config.js';
import { makeBridge, engineManifest } from '../lib/bridge.js';
import { createConsent } from '../lib/consent.js';
import { registerTools } from '../lib/toolkit.js';
import { COMMON_SECTION, registerPrompt } from '../lib/prompt.js';
import { MANIFESTS, validateManifests } from '../lib/registry.js';

export const SERVICE = 'wechatOne';
export const name = 'dsh-wechat-one-core';
export const inject = ['tools', 'systemPrompt'];
export const Config = buildConfigSchema(Schema);

/** 构造共享 API。测试可注入 bridge。 */
export function createApi(ctx, rawConfig = {}, { bridge } = {}) {
  const config = normalizeConfig(rawConfig);
  const engineBridge = bridge ?? makeBridge(config);
  const consent = createConsent(ctx, config);
  const health = new Map();
  const problems = validateManifests(MANIFESTS, engineManifest());

  return Object.freeze({
    config,
    bridge: engineBridge,
    consent,
    problems,
    /** 功能组件调用：在组件自己的 ctx 上注册工具与提示段落，失败只影响该组件。 */
    mount(componentCtx, id) {
      const manifest = MANIFESTS[id];
      try {
        if (!manifest) throw new Error(`unknown component ${id}`);
        if (problems.length) throw new Error('manifest invalid');
        registerTools(componentCtx, manifest.tools, { bridge: engineBridge, consent, health: () => Object.fromEntries(health) });
        if (manifest.prompt) registerPrompt(componentCtx, manifest.prompt);
        health.set(id, { ok: true });
        return true;
      } catch (error) {
        health.set(id, { ok: false, code: 'COMPONENT_UNAVAILABLE' });
        try { componentCtx?.logger?.('wechat-one')?.warn?.(`component ${id} unavailable: ${error?.message}`); } catch { /* 无日志 */ }
        return false;
      }
    },
    unmount(id) { health.delete(id); },
    health: () => Object.fromEntries(health),
  });
}

export function apply(ctx, config = {}) {
  const api = createApi(ctx, config);
  registerPrompt(ctx, COMMON_SECTION);
  ctx.provide(SERVICE, api);
}
