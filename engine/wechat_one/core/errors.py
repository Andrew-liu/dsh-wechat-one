"""统一错误类型与错误码目录。

引擎内所有可预期失败都抛 EngineError；dispatch 会把它转换为
{"ok": false, "error": {"code", "message", "retryable"}}，未知异常统一为 ENGINE_ERROR。
"""
from __future__ import annotations

from typing import Any

# 可安全自动重试的错误码（仅对幂等读操作生效，由 JS toolkit 决定是否重试）
RETRYABLE = frozenset({"BUSY", "TIMEOUT", "INDEX_REBUILDING", "SOURCE_BUSY"})

MESSAGES: dict[str, str] = {
    "INVALID_ARGS": "参数不符合工具要求。",
    "UNKNOWN_ACTION": "未知的引擎操作。",
    "ENGINE_ERROR": "本地引擎内部错误，已记录日志。",
    "BUSY": "已有更新任务在进行中，请稍后查看状态。",
    "TIMEOUT": "本地操作超时，原有索引保留。",
    "NOT_INDEXED": "索引尚未建立，请先调用 wechat_one_refresh。",
    "INDEX_REBUILDING": "索引损坏或正在重建，请稍后重试或调用 wechat_one_refresh。",
    "SOURCE_NOT_CONFIGURED": "尚未配置数据来源，请先完成 wechat_one_setup 或在插件配置中填写 sourcePath。",
    "SOURCE_NOT_FOUND": "数据来源不存在或不完整，请检查解密镜像目录。",
    "LAYOUT_AMBIGUOUS": "镜像目录下有多个账号，请在配置中指定账号。",
    "DATABASE_UNREADABLE": "镜像数据库不是明文 SQLite 或无法读取。",
    "SCHEMA_UNRECOGNIZED": "镜像表结构无法识别，可能是微信版本变化。",
    "ZSTD_UNAVAILABLE": "需要 zstd 解压能力：Python 3.14+ 自带，或 pip install zstandard。",
    "CONVERSATION_NOT_FOUND": "未找到该会话。",
    "CONVERSATION_NOT_ALLOWED": "该会话不在允许查询的范围内。",
    "MESSAGE_NOT_FOUND": "未找到该消息。",
    "INVALID_CURSOR": "分页标记已失效（索引已更新），请重新开始查询。",
    "NOT_PRIVATE_CHAT": "人格报告只支持私聊好友。",
    "INSUFFICIENT_DATA": "该会话的可用文本消息太少，无法生成可靠报告。",
    "RUN_NOT_FOUND": "未找到对应的报告素材，请重新调用 prepare。",
    "SEMANTIC_INVALID": "语义稿未通过证据校验，请按 errors 逐条修正后重试。",
    "THEME_NOT_FOUND": "主题不存在。",
    "GROUP_SET_UNKNOWN": "未定义的群集合。",
    "CONVERSATION_UNRESOLVED": "有会话名称找不到或重名，请改用 conversation_id。",
    "NO_SELECTION": "请指定 group_set、chats 或 conversation_ids 之一。",
    "NO_MESSAGES": "该时间范围内这些会话没有可总结的消息。",
    "PART_NOT_FOUND": "素材分块不存在。",
    "DECRYPT_TOOL_MISSING": "未找到 wechat-decrypt，请先执行 wechat_one_setup(step=install)。",
    "DECRYPT_INSTALL_DISABLED": "插件配置禁止一键安装解密工具，请手动安装后配置 decryptToolDir。",
    "DECRYPT_KEY_MISSING": "还没有数据库密钥，请执行 wechat_one_setup(step=extract_key)。",
    "DECRYPT_FAILED": "解密未完成，原有镜像与索引保留；密钥可能已失效，可重新取钥。",
    "SETUP_FAILED": "接入步骤失败，请查看状态后重试。",
    "SETUP_CONFLICT": "目标目录已存在且不是 wechat-decrypt。",
    "PLATFORM_UNSUPPORTED": "该步骤仅支持 Windows。",
    "EMBEDDING_UNAVAILABLE": "本地向量服务不可用，已降级为全文检索。",
    "PRIVATE_STATE_REQUIRED": "本地数据目录必须在插件代码目录之外。",
}


class EngineError(Exception):
    """带错误码的业务异常。code 不区分大小写，输出时统一大写。

    public=False（默认）时，对外只返回错误码目录里的标准文案，原始 message 仅写本地日志，
    以免数据层报错里的本地路径、账号目录名泄露给模型。确知不含隐私的消息才设 public=True。
    """

    def __init__(self, code: str, message: str | None = None, hint: str | None = None,
                 details: Any = None, *, retryable: bool | None = None, public: bool = False):
        code = str(code).upper()
        super().__init__(message or MESSAGES.get(code, code))
        self.code = code
        self.message = message or MESSAGES.get(code, code)
        self.hint = hint
        self.details = details
        self.public = public
        self.retryable = (code in RETRYABLE) if retryable is None else retryable

    def public_message(self) -> str:
        if self.public:
            return self.message
        return MESSAGES.get(self.code, "本地操作未完成。")

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.public_message(), "retryable": self.retryable}
        if self.public and isinstance(self.details, (list, dict, str, int, float, bool)):
            error["details"] = self.details
        return {"ok": False, "error": error}


# 兼容从 wechat-summary 复用的数据层代码
WSError = EngineError
