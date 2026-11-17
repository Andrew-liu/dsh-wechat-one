"""有效配置解析。

优先级：插件配置（JS 传入、非空项） > 私有设置 settings.json（setup 写入） > ~/.wechat-report/config.yaml > 默认值。
引擎只信任这里给出的路径，不接受模型参数里的路径或命令。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .state import State
from .yamlish import parse_yaml

WECHAT_REPORT_CONFIG = Path("~/.wechat-report/config.yaml")
DEFAULT_DECRYPT_REPO = "https://github.com/WeinanHu/wechat-decrypt.git"
# 开发时锁定的上游版本（WeinanHu/wechat-decrypt main @ 2026-09-02）
DEFAULT_DECRYPT_REF = "ba47bbb17bc19066b4e453aa611f6554d0e0ebfe"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


@dataclass
class Settings:
    state_dir: Path
    source_type: str = "decrypted_dir"
    source_path: str = ""
    source_account: str = ""
    decrypt_tool_dir: str = ""
    decrypt_tool_ref: str = DEFAULT_DECRYPT_REF
    decrypt_repo: str = DEFAULT_DECRYPT_REPO
    allow_decrypt_install: bool = True
    timezone: str = "Asia/Shanghai"
    me_ids: list[str] = field(default_factory=list)
    me_names: list[str] = field(default_factory=list)
    allowed_conversations: list[str] = field(default_factory=list)
    embedding_provider: str = "none"
    embedding_model: str = "bge-m3"
    embedding_base_url: str = "http://127.0.0.1:11434"
    max_items: int = 30
    max_content_chars: int = 500
    group_sets: dict[str, list[str]] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)

    def tz(self):
        from zoneinfo import ZoneInfo

        try:
            return ZoneInfo(self.timezone)
        except Exception:  # noqa: BLE001
            return ZoneInfo("Asia/Shanghai")

    def embedding_enabled(self) -> bool:
        return self.embedding_provider == "ollama" and is_loopback(self.embedding_base_url)


def is_loopback(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and host in ("127.0.0.1", "localhost", "::1")


def load_wechat_report_config() -> dict[str, Any]:
    path = Path(os.environ.get("WS_CONFIG") or WECHAT_REPORT_CONFIG).expanduser()
    try:
        data = parse_yaml(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)] if str(value) else []


def _default_mirror(state: State) -> str | None:
    """插件一键安装的 wechat-decrypt 输出目录（存在时才作为兜底来源）。"""
    path = state.root / "wechat-decrypt" / "decrypted"
    return str(path) if path.is_dir() else None


def resolve(plugin: dict[str, Any] | None, state: State) -> Settings:
    plugin = plugin or {}
    local = state.settings()
    report = load_wechat_report_config()
    src = report.get("source") or {}
    refresh = report.get("refresh") or {}
    me = report.get("me") or {}
    s = Settings(state_dir=state.root)

    def pick(name: str, *candidates: tuple[str, Any]) -> Any:
        for origin, value in candidates:
            if value not in (None, "", []):
                s.origin[name] = origin
                return value
        return None

    s.source_type = str(pick("source_type", ("plugin", plugin.get("sourceType")), ("local", local.get("sourceType")),
                             ("wechat-report", src.get("type"))) or "decrypted_dir").lower()
    s.source_path = str(pick("source_path", ("plugin", plugin.get("sourcePath")), ("local", local.get("sourcePath")),
                             ("wechat-report", src.get("path")),
                             ("default", _default_mirror(state))) or "")
    s.source_account = str(pick("source_account", ("local", local.get("sourceAccount")),
                                ("wechat-report", src.get("account"))) or "")
    s.decrypt_tool_dir = str(pick("decrypt_tool_dir", ("plugin", plugin.get("decryptToolDir")),
                                  ("local", local.get("decryptToolDir")),
                                  ("wechat-report", refresh.get("cwd"))) or "")
    s.decrypt_tool_ref = str(plugin.get("decryptToolRef") or DEFAULT_DECRYPT_REF)
    s.allow_decrypt_install = plugin.get("allowDecryptInstall") is not False
    s.timezone = str(plugin.get("timezone") or report.get("timezone") or "Asia/Shanghai")
    s.me_ids = _as_list(plugin.get("meIds")) or _as_list(me.get("ids"))
    s.me_names = _as_list(plugin.get("meNames")) or _as_list(me.get("names"))
    s.allowed_conversations = _as_list(plugin.get("allowedConversations"))
    sets = plugin.get("groupSets") if isinstance(plugin.get("groupSets"), dict) and plugin.get("groupSets") \
        else report.get("group_sets")
    if isinstance(sets, dict):
        s.group_sets = {str(k): _as_list(v) for k, v in sets.items() if str(k).strip() and _as_list(v)}
    s.embedding_provider = str(plugin.get("embeddingProvider") or "none").lower()
    s.embedding_model = str(plugin.get("embeddingModel") or "bge-m3")
    s.embedding_base_url = str(plugin.get("embeddingBaseUrl") or "http://127.0.0.1:11434")
    try:
        s.max_items = max(1, min(100, int(plugin.get("maxItems") or 30)))
        s.max_content_chars = max(50, min(2000, int(plugin.get("maxContentChars") or 500)))
    except (TypeError, ValueError):
        pass
    return s
