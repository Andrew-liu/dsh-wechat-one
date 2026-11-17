// 配置：唯一的 Schemastery 声明（只在 core 组件导出）与本地路径规则。
import { homedir } from 'node:os';
import { dirname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const PACKAGE_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
export const ENGINE_DIR = resolve(PACKAGE_ROOT, 'engine');

export const DEFAULTS = Object.freeze({
  confirmReads: true,
  stateDirectory: '',
  pythonExecutable: '',
  sourceType: 'decrypted_dir',
  sourcePath: '',
  decryptToolDir: '',
  decryptToolRef: '',
  allowDecryptInstall: true,
  allowedConversations: [],
  meNames: [],
  groupSets: {},
  embeddingProvider: 'none',
  embeddingModel: 'bge-m3',
  embeddingBaseUrl: 'http://127.0.0.1:11434',
  maxItems: 30,
  maxContentChars: 500,
});

/** 传给引擎的配置键（不含 JS 专用项）。 */
export const ENGINE_KEYS = ['sourceType', 'sourcePath', 'decryptToolDir', 'decryptToolRef', 'allowDecryptInstall',
  'allowedConversations', 'meNames', 'groupSets', 'embeddingProvider', 'embeddingModel', 'embeddingBaseUrl', 'maxItems', 'maxContentChars'];

export function buildConfigSchema(Schema) {
  if (!Schema) return undefined;
  return Schema.object({
    confirmReads: Schema.boolean().default(true).description('读取聊天正文前，每轮对话确认一次（DSH Auto 模式下不额外弹窗）。'),
    stateDirectory: Schema.string().default('').description('本地私有数据目录（索引、报告、日志），必须在插件代码目录之外。默认 ~/.dsh-wechat-one'),
    pythonExecutable: Schema.string().default('').description('Python 3.10+ 可执行文件；留空自动寻找。'),
    sourceType: Schema.union(['decrypted_dir', 'json']).default('decrypted_dir').description('数据来源类型。'),
    sourcePath: Schema.string().default('').description('解密镜像目录或 JSON 文件；留空时使用 setup 结果或 ~/.wechat-report/config.yaml。'),
    decryptToolDir: Schema.string().default('').description('WeinanHu/wechat-decrypt 所在目录；留空自动探测。'),
    decryptToolRef: Schema.string().default('').description('一键安装 wechat-decrypt 时固定的 commit；留空使用插件内置版本。'),
    allowDecryptInstall: Schema.boolean().default(true).description('允许一键安装 wechat-decrypt（关闭后只探测已有安装）。'),
    allowedConversations: Schema.array(Schema.string()).default([]).description('只允许查询这些会话（显示名或微信 ID）；留空不限制。'),
    meNames: Schema.array(Schema.string()).default([]).description('我在群里的昵称 / 群昵称，用于日报的“与我相关”；留空读取 ~/.wechat-report/config.yaml 的 me.names。'),
    groupSets: Schema.dict(Schema.array(Schema.string())).default({}).description('日报群集合：集合名 → 群名或会话 ID 列表；留空读取 ~/.wechat-report/config.yaml 的 group_sets。'),
    embeddingProvider: Schema.union(['none', 'ollama']).default('none').description('可选本地向量检索；只允许本机 Ollama。'),
    embeddingModel: Schema.string().default('bge-m3').description('Ollama embedding 模型名。'),
    embeddingBaseUrl: Schema.string().default('http://127.0.0.1:11434').description('Ollama 地址，只接受本机回环地址。'),
    maxItems: Schema.natural().min(1).max(100).default(30).description('精确检索每页最多条数。'),
    maxContentChars: Schema.natural().min(50).max(2000).default(500).description('单条消息返回给模型的最大字符数。'),
  });
}

export function expandPath(value, home = homedir()) {
  if (value === '~') return resolve(home);
  if (/^~[\\/]/.test(value)) return resolve(home, value.slice(2));
  return resolve(value);
}

/** 解析私有目录；位于插件代码目录内时抛 PRIVATE_STATE_REQUIRED。 */
export function stateDirectory(config = {}, env = process.env, home = homedir()) {
  const raw = config.stateDirectory || env.DSH_WECHAT_ONE_STATE_DIR || '~/.dsh-wechat-one';
  const state = expandPath(raw, home);
  const a = state.toLowerCase();
  const root = resolve(PACKAGE_ROOT).toLowerCase();
  if (a === root || a.startsWith(root + sep)) throw new Error('PRIVATE_STATE_REQUIRED');
  return state;
}

export function normalizeConfig(config = {}) {
  const out = { ...DEFAULTS };
  for (const [key, value] of Object.entries(config || {})) {
    if (key in DEFAULTS && value !== undefined && value !== null) out[key] = value;
  }
  return Object.freeze(out);
}

export function engineConfig(config) {
  return Object.fromEntries(ENGINE_KEYS.map(k => [k, config[k]]));
}
