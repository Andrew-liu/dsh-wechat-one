# 第三方归属

本项目整体采用 MIT 许可证。

| 组件 | 来源 | 使用方式 | 许可 |
| --- | --- | --- | --- |
| `engine/wechat_one/data/{models,parsing,nicknames}.py`、`data/sources/{base,decrypted_dir,json_source}.py` | [Andrew-liu/wechat-summary](https://github.com/Andrew-liu/wechat-summary) @1f81a80 | 复制，仅调整 import 路径（文件头已注明） | MIT |
| `engine/wechat_one/core/yamlish.py`、`shared/templating.py` | 同上 | 复制 | MIT |
| `integrations/decrypt_tool.py` 的编排流程、`shared/evidence.py` 的校验思路 | 同上 `setup_env.py`、`semantic.py` | 移植改写 | MIT |
| 组件拆分、子进程桥、审批粒度等设计 | [CH3SH-LC/dsh-better-wechat](https://github.com/CH3SH-LC/dsh-better-wechat) | 仅参考设计思路，未复制代码（含其 Apache-2.0 采集器） | — |
| 混合检索、画像维度的思路 | [runzhliu/welink](https://github.com/runzhliu/welink) | 仅参考思路，未复制任何 AGPL-3.0 代码 | — |

## 外部工具（不随本包分发）

[WeinanHu/wechat-decrypt](https://github.com/WeinanHu/wechat-decrypt)：取钥与解密由该工具完成。
本插件只会在你审批后，从其 GitHub 仓库克隆到你本机的私有目录并调用其脚本；
本插件仓库与发布包中不包含任何取钥、解密或进程内存访问代码。该工具的使用条款与风险以其仓库说明为准。

## 宿主

DeepSeek Harness（DSH）、`@deepseek-ai/dsh-tools`、`@deepseek-ai/schemastery` 由用户安装的 DSH 提供，本包不分发其实现。

“微信”“WeChat”“DeepSeek Harness”名称仅用于标识兼容对象，本项目与其权利人无隶属关系。
