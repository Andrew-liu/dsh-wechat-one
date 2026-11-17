# dsh-wechat-one

DeepSeek Harness（DSH）插件：在 DSH 里用自然语言查你自己的微信聊天记录。

- **主题检索（RAG）**：问“群里聊过哪些涨粉技巧”，它把各个私聊和群聊里讨论过的相关对话片段找出来，助手归纳成带出处（发言人、会话、日期、msg_id）的要点清单。
- **精确检索**：按关键词、发言人、日期、会话查原文，并可查看前后文。
- **好友人格报告**：本地统计 + 抽样素材，助手写出证据化画像，每条结论都附逐字原话并经过校验，生成 Markdown 与单文件 HTML。
- **群聊日报 / 周报 / 月报**：程序算统计（发言、发言人、时段、链接、@我），助手按“与我相关 / 主线话题 / 观点与分歧 / 值得跟进 / 精选链接”写稿，证据校验后生成报告；可配合 DSH 定时任务每天自动出，错过的最多补 3 期。

数据来自 [WeinanHu/wechat-decrypt](https://github.com/WeinanHu/wechat-decrypt) 生成的本地明文镜像。**插件本身不包含任何取钥或解密代码。**

## 要求

- Windows 10/11、电脑微信 4.x（已登录）
- DSH 0.2.2 系列（已在 `0.2.2-rc.1` 验证），Node.js 22+
- Python 3.10+（含 SQLite FTS5；Python 3.14 自带 zstd，更早版本需 `pip install zstandard`）

## 安装

```sh
dsh plugin --profile web add github:Andrew-liu/dsh-wechat-one
# 或本地目录
dsh plugin --profile web add file:/path/to/dsh-wechat-one
```

安装后**完整重启 DSH**。插件会在 profile 中插入 5 个组件：`core`、`setup`、`read`、`persona`、`summary`，可在插件管理中分别启停。

> 从 0.1.x 升级：索引结构升级到 v2（新增链接字段），升级后先说一次“刷新微信记录”重建索引（约 1 分钟）。

## 首次接入

在 DSH 里说“接入微信聊天记录”，助手会按 `wechat_one_status` 的 `next_action` 带你完成：

1. `wechat_one_setup(step=install)`：检测到已有 wechat-decrypt（插件配置 `decryptToolDir`，或 `~/.wechat-report/config.yaml` 中 wechat-summary 的配置）就直接复用；否则在审批后克隆固定版本到私有目录。
2. `wechat_one_setup(step=extract_key)`：会关闭微信，看到提示后从桌面重新打开并登录。
3. `wechat_one_setup(step=decrypt)`：解密并建立索引（首次几分钟）。

之后想要最新记录时说“刷新微信记录”即可（`wechat_one_refresh`，只有变化的分片会重新索引）。

已经有解密镜像？直接在插件配置里填 `sourcePath`，再调用一次 `wechat_one_refresh`。

## 可以这样问

- “我加的群里聊过哪些涨粉技巧？按技巧归纳，附原话和出处”
- “上个月和舟舟聊到辞职的那段，给我原文和上下文”
- “给舟舟生成一份人格报告，用 claude 主题”
- “出一份常看群昨天的日报” / “出增长交流群上周的周报”
- “每天早上 9 点给常看群出日报”（助手会用 DSH 的 `schedule_create` 建每日任务；任务里先 `wechat_one_summary_pending` 判断有没有待出的期，再刷新、生成。DSH 需保持运行）

日报报告保存在 `~/.dsh-wechat-one/reports/summary/<群或集合名>/<周期>-<日期>/`。群集合沿用 wechat-summary 的 `~/.wechat-report/config.yaml` 中 `group_sets`，“我的身份”沿用 `me.names`；也可在插件配置里单独填写。

## 配置

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `confirmReads` | `true` | 读取聊天正文前每轮确认一次 |
| `stateDirectory` | `~/.dsh-wechat-one` | 索引、报告、日志所在的私有目录 |
| `sourcePath` / `sourceType` | 自动 | 解密镜像目录或 JSON 文件 |
| `decryptToolDir` / `decryptToolRef` / `allowDecryptInstall` | 自动 / 内置版本 / `true` | wechat-decrypt 接入 |
| `allowedConversations` | `[]` | 只允许查询这些会话（显示名或微信 ID），留空不限制 |
| `meNames` | `[]` | 我在群里的昵称，用于日报“与我相关”；留空读 `me.names` |
| `groupSets` | `{}` | 日报群集合（集合名 → 群名或会话 ID）；留空读 `group_sets` |
| `embeddingProvider` | `none` | 设为 `ollama` 开启本地向量混合检索（模型默认 `bge-m3`，只允许本机地址） |
| `maxItems` / `maxContentChars` | 30 / 500 | 返回条数与单条长度上限 |

## 工作方式

```
wechat-decrypt ─► decrypted/ 明文镜像 ─(只读)─► Python 引擎：解析 → 30 分钟间隔切片段 → 中文二元组 FTS5（向量可选）
                                                  │ 私有目录 ~/.dsh-wechat-one
DSH 模型 ⇄ JS 组件（Cordis 工具 + 审批 + 结果白名单）⇄ 子进程 JSON 桥
```

主题检索 = 模型把主题扩展成 5~20 个检索词 → 每个词分别在片段索引上召回 → 按排名融合打分（主题词权重 2）→ 去重、同一会话每页最多 3 段 → 返回片段原文供模型归纳。

## 可靠性

- 每次工具调用独立子进程，有超时、输出上限、可取消；引擎崩溃不影响 DSH。
- 索引在 `index.next.sqlite` 构建、校验通过后原子替换；失败时保留上一版索引。
- 单写者锁：刷新进行中再次刷新返回 `BUSY`；读请求不受影响。
- 索引损坏或 schema 版本变化会被检测并提示重建。
- 组件之间相互隔离，一个组件加载失败不影响其他组件，`wechat_one_status` 会报告各组件状态。

## 开发

```sh
npm test               # JS：组件契约、审批、流水线、子进程桥、真实引擎集成
npm run test:python    # Python：索引、检索、人格报告、setup、架构依赖方向
npm run manifest       # Python action 变更后重新生成 engine/manifest.json
npm run privacy        # 发布前检查：不含解密代码、密钥或数据
```

新增功能（如后续的聊天总结、高情商沟通、发消息）：在 `engine/wechat_one/features/` 用 `@action` 注册、取数只用 `shared/retrieval`；
在 `manifests/` 声明工具与风险等级，在 `components/` 用 `featureComponent(id)` 装配，在 `cordis.patch.yml` 加一行。

## 隐私

见 [PRIVACY.md](PRIVACY.md)。只处理你本人有权访问的数据；人格报告不是心理测评，请勿公开传播。

## 许可

MIT，第三方归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目与腾讯、DeepSeek 无关。
