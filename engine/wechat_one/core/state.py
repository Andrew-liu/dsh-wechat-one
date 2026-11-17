"""私有状态目录：索引、报告素材、日志、审计、本地设置与 HMAC 密钥。

目录必须在插件代码目录之外（JS 侧也会校验）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

from .errors import EngineError

PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class State:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        try:
            self.root.relative_to(PACKAGE_ROOT)
            raise EngineError("PRIVATE_STATE_REQUIRED")
        except ValueError:
            pass
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 路径
    @property
    def index_path(self) -> Path:
        return self.root / "index.sqlite"

    @property
    def index_next_path(self) -> Path:
        return self.root / "index.next.sqlite"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def log_path(self) -> Path:
        return self.root / "logs" / "engine.jsonl"

    @property
    def audit_path(self) -> Path:
        return self.root / "logs" / "audit.jsonl"

    @property
    def locks_dir(self) -> Path:
        return self.root / "locks"

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.json"

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    # ---- 本地设置（setup 写入的 sourcePath 等）
    def read_json(self, path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write_json(self, path: Path, value: Any) -> None:
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))

    def settings(self) -> dict[str, Any]:
        value = self.read_json(self.settings_path, {})
        return value if isinstance(value, dict) else {}

    def update_settings(self, **updates: Any) -> dict[str, Any]:
        current = self.settings()
        current.update({k: v for k, v in updates.items() if v is not None})
        self.write_json(self.settings_path, current)
        return current

    def status(self) -> dict[str, Any]:
        value = self.read_json(self.status_path, {})
        return value if isinstance(value, dict) else {}

    def update_status(self, **updates: Any) -> None:
        current = self.status()
        current.update(updates)
        self.write_json(self.status_path, current)

    # ---- 稳定匿名标识
    def secret(self) -> bytes:
        path = self.root / "secret.key"
        try:
            data = path.read_bytes()
            if len(data) == 32:
                return data
        except OSError:
            pass
        data = secrets.token_bytes(32)
        path.write_bytes(data)
        return data

    def hmac_id(self, prefix: str, value: str, length: int = 16) -> str:
        digest = hmac.new(self.secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{prefix}_{digest[:length]}"
