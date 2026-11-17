"""本地审计日志：记录写操作与副作用操作，不记录聊天正文。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .log import redact


def record(path: Path, action: str, kind: str, outcome: str, **fields: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, "kind": kind, "outcome": outcome,
                 **redact(fields)}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - 审计失败不影响主流程
        pass
