// setup 组件：接入状态、一键接入 wechat-decrypt、刷新镜像与索引。
const STEP_REASONS = {
  install: '从 GitHub 克隆固定版本的 WeinanHu/wechat-decrypt 到本机私有目录，并创建其 Python 虚拟环境、安装依赖（frida、pycryptodome、zstandard）。'
    + '这是第三方解密工具，仅用于处理你本人账号的数据；插件仓库本身不含解密代码。',
  extract_key: '运行 wechat-decrypt 的取钥脚本：它会关闭电脑微信，需要你在看到提示后从桌面手动重新打开并登录微信。'
    + '密钥只保存在 wechat-decrypt 目录，不会返回给模型或写入日志。',
  decrypt: '运行 wechat-decrypt 的解密脚本，只读原始库，生成本地明文镜像，随后更新插件的本地索引。可能需要几分钟。',
};

const COVERAGE = { first_time: true, last_time: true };

export default {
  id: 'setup',
  prompt: {
    name: 'tool:wechat-one-setup',
    order: 116,
    text: '- 首次使用或检索提示索引过期时，先调用 wechat_one_status 查看 next_action；按提示依次 wechat_one_setup(install → extract_key → decrypt)，已就绪时用 wechat_one_refresh 更新。extract_key 会关闭微信，调用前先向用户说明。',
  },
  tools: [
    {
      name: 'wechat_one_status',
      action: 'status',
      kind: 'meta',
      includeHealth: true,
      description: '查看微信聊天索引的接入与健康状态：解密工具是否就绪、镜像时间、索引覆盖范围与是否过期、向量检索是否可用，以及建议的下一步 next_action。不读取聊天正文。',
      parameters: {},
      result: {
        ready: true, next_action: true, warnings: true,
        source: { configured: true, type: true, origin: true, available: true, mirror_latest: true, mirror_age_hours: true, stale: true },
        decrypt_tool: { found: true, origin: true, key_ready: true, install_allowed: true, platform_supported: true },
        index: { indexed: true, version: true, built_at: true, messages: true, conversations: true, chunks: true,
          ...COVERAGE, building: true, building_progress: true, last_error: true, rebuild_required: true },
        vector: { enabled: true, available: true, model: true, indexed_chunks: true },
      },
    },
    {
      name: 'wechat_one_setup',
      action: 'setup',
      kind: 'write',
      idempotent: false,
      description: '一键接入 WeinanHu/wechat-decrypt 的某一步：install（安装工具）、extract_key（取数据库密钥，会关闭微信，需用户手动重开）、decrypt（解密并建索引）。每步单独审批；已完成的步骤会直接返回已就绪。',
      parameters: {
        step: { type: 'string', required: true, enum: ['install', 'extract_key', 'decrypt'], description: '要执行的接入步骤。' },
      },
      approvalReason: args => STEP_REASONS[args?.step] || '执行微信数据接入步骤。',
      result: { step: true, status: true, detail: true, next_action: true, index: { messages: true, conversations: true, chunks: true, ...COVERAGE } },
    },
    {
      name: 'wechat_one_refresh',
      action: 'refresh',
      kind: 'write',
      idempotent: false,
      description: '更新本地数据：调用 wechat-decrypt 重新解密生成最新镜像（已配置时），再增量更新插件索引。只返回统计，不返回聊天正文。可能需要几分钟。',
      parameters: {
        reindex_only: { type: 'boolean', description: '为 true 时跳过解密，只按现有镜像重建索引。默认 false。' },
      },
      approvalReason: args => args?.reindex_only ? '按现有本地镜像增量更新插件索引。'
        : '运行 wechat-decrypt 解密脚本刷新本地明文镜像（只读原始库），然后增量更新插件索引。',
      result: {
        decrypted: true, reindexed: true, changed_shards: true, messages: true, conversations: true, chunks: true,
        ...COVERAGE, vector_indexed: true, duration_seconds: true, warnings: true,
      },
    },
  ],
};
