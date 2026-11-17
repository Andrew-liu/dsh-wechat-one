"""跨进程文件锁。

- writer：refresh / 建索引 / setup 互斥（单写者），拿不到立即返回 BUSY，不排队。
- ui：预留给后续 UI 自动化（同一时刻只允许一个窗口操作）。
锁文件里记录持有者 pid 与开始时间，持有进程已退出时视为陈旧锁并接管。
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import EngineError

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - 非 Windows
    import fcntl


def _lock_fd(fd: int) -> bool:
    try:
        if os.name == "nt":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_fd(fd: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def exclusive(locks_dir: Path, name: str, progress: str = "") -> Iterator[None]:
    locks_dir.mkdir(parents=True, exist_ok=True)
    path = locks_dir / f"{name}.lock"
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if not _lock_fd(fd):
            info = holder(locks_dir, name)
            raise EngineError("BUSY", details=info, public=True,
                              message="已有更新任务在进行中（" + (info.get("progress") or name) + "），请稍后查看状态。")
        meta = locks_dir / f"{name}.json"
        meta.write_text(json.dumps({"pid": os.getpid(), "since": int(time.time()), "progress": progress}), encoding="utf-8")
        try:
            yield
        finally:
            try:
                meta.unlink()
            except OSError:
                pass
            _unlock_fd(fd)
    finally:
        os.close(fd)


def set_progress(locks_dir: Path, name: str, progress: str) -> None:
    meta = locks_dir / f"{name}.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["progress"] = progress
        meta.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        pass


def holder(locks_dir: Path, name: str) -> dict:
    try:
        data = json.loads((locks_dir / f"{name}.json").read_text(encoding="utf-8"))
        return {"since": data.get("since"), "progress": str(data.get("progress") or "")}
    except (OSError, ValueError):
        return {}


def is_locked(locks_dir: Path, name: str) -> bool:
    path = locks_dir / f"{name}.lock"
    if not path.exists():
        return False
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        if _lock_fd(fd):
            _unlock_fd(fd)
            return False
        return True
    finally:
        os.close(fd)
