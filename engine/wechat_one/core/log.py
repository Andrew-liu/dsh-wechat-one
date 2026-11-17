"""脱敏结构化日志（JSONL，按大小滚动）。

日志只写到私有状态目录，不回传给模型。写入前会脱敏：本地路径、wxid、长十六进制串（疑似密钥）。
日志失败永远不影响主流程。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
KEEP = 3

_PATTERNS = (
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[hex]"),
    (re.compile(r"wxid_[A-Za-z0-9_\-]+"), "[wxid]"),
    (re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]*"), "[path]"),
    (re.compile(r"(?<![\w.])/(?:home|Users|root|tmp|var|mnt)/[^\s\"'<>|]*"), "[path]"),
)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, repl in _PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class Logger:
    def __init__(self, path: Path):
        self.path = path

    def _rotate(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                for i in range(KEEP - 1, 0, -1):
                    src = self.path.with_suffix(f".{i}.jsonl")
                    dst = self.path.with_suffix(f".{i + 1}.jsonl")
                    if src.exists():
                        os.replace(src, dst)
                os.replace(self.path, self.path.with_suffix(".1.jsonl"))
        except OSError:
            pass

    def write(self, level: str, event: str, **fields: Any) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate()
            record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **redact(fields)}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - 日志永不抛出
            pass

    def info(self, event: str, **fields: Any) -> None:
        self.write("info", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self.write("error", event, **fields)
