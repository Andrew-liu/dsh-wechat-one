"""Action 注册与分发。

请求（stdin 一行 JSON）：{"action", "params", "request_id", "deadline_ms", "config"}
响应（stdout 一行 JSON）：{"ok": true, "data": {...}} 或 {"ok": false, "error": {"code", "message", "retryable"}}

- 每个 action 声明 kind（read / write / side_effect）与 timeout_ms；JS 侧按 engine/manifest.json 设置超时，
  测试保证 manifest.json 与这里的注册表一致。
- writes_index=True 的 action 在单写者锁内执行，拿不到锁立即返回 BUSY。
- write / side_effect 写本地审计日志。
- 未知异常统一转为 ENGINE_ERROR，堆栈只进脱敏日志。
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from . import audit, locks
from .config import Settings, resolve
from .errors import EngineError
from .log import Logger
from .state import State

KINDS = ("read", "write", "side_effect")


@dataclass(frozen=True)
class ActionSpec:
    name: str
    kind: str
    timeout_ms: int
    writes_index: bool
    fn: Callable[["Ctx", "Params"], dict[str, Any]]


ACTIONS: dict[str, ActionSpec] = {}


def action(name: str, kind: str = "read", timeout_ms: int = 20_000, writes_index: bool = False):
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind}")

    def deco(fn):
        if name in ACTIONS:
            raise ValueError(f"duplicate action {name}")
        ACTIONS[name] = ActionSpec(name, kind, timeout_ms, writes_index, fn)
        return fn

    return deco


def manifest() -> dict[str, Any]:
    return {"actions": {n: {"kind": a.kind, "timeout_ms": a.timeout_ms, "writes_index": a.writes_index}
                        for n, a in sorted(ACTIONS.items())}}


class Ctx:
    def __init__(self, state: State, settings: Settings, logger: Logger, deadline: float | None, request_id: str,
                 plugin_config: dict[str, Any] | None = None):
        self.state = state
        self.settings = settings
        self.log = logger
        self.deadline = deadline
        self.request_id = request_id
        self.plugin_config = plugin_config or {}

    def reload_settings(self) -> Settings:
        self.settings = resolve(self.plugin_config, self.state)
        return self.settings

    def remaining_seconds(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - time.monotonic()

    def check_deadline(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise EngineError("TIMEOUT")

    def progress(self, text: str) -> None:
        locks.set_progress(self.state.locks_dir, "writer", text)


class Params:
    """参数读取助手：JS 侧已做严格 schema 校验，这里再做一次类型与范围兜底。"""

    def __init__(self, raw: Any):
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise EngineError("INVALID_ARGS")
        self.raw = raw

    def _get(self, key: str, required: bool):
        value = self.raw.get(key)
        if value is None and required:
            raise EngineError("INVALID_ARGS", f"缺少参数 {key}", public=True)
        return value

    def str(self, key: str, *, required: bool = False, max_len: int = 500, default: str | None = None) -> str | None:
        value = self._get(key, required)
        if value is None:
            return default
        if not isinstance(value, str) or len(value) > max_len:
            raise EngineError("INVALID_ARGS", f"参数 {key} 无效", public=True)
        return value

    def int(self, key: str, *, default: int, lo: int, hi: int) -> int:
        value = self.raw.get(key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise EngineError("INVALID_ARGS", f"参数 {key} 必须是整数", public=True)
        return max(lo, min(hi, int(value)))

    def bool(self, key: str, default: bool = False) -> bool:
        value = self.raw.get(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise EngineError("INVALID_ARGS", f"参数 {key} 必须是布尔值", public=True)
        return value

    def str_list(self, key: str, *, max_items: int = 100, max_len: int = 200, required: bool = False) -> list[str]:
        value = self._get(key, required)
        if value is None:
            return []
        if (not isinstance(value, list) or len(value) > max_items
                or any(not isinstance(v, str) or not v or len(v) > max_len for v in value)):
            raise EngineError("INVALID_ARGS", f"参数 {key} 无效", public=True)
        return list(dict.fromkeys(value))

    def obj(self, key: str, *, required: bool = False) -> Any:
        return self._get(key, required)


def handle(request: Any, state_dir: str) -> dict[str, Any]:
    if not isinstance(request, dict):
        return EngineError("INVALID_ARGS").to_dict()
    name = request.get("action")
    spec = ACTIONS.get(name) if isinstance(name, str) else None
    if spec is None:
        return EngineError("UNKNOWN_ACTION").to_dict()
    try:
        state = State(state_dir)
    except EngineError as exc:
        return exc.to_dict()
    logger = Logger(state.log_path)
    request_id = str(request.get("request_id") or "")[:64]
    deadline_ms = request.get("deadline_ms")
    budget = deadline_ms if isinstance(deadline_ms, (int, float)) and deadline_ms > 0 else spec.timeout_ms
    deadline = time.monotonic() + budget / 1000.0
    started = time.monotonic()
    try:
        plugin_config = request.get("config") if isinstance(request.get("config"), dict) else {}
        settings = resolve(plugin_config, state)
        ctx = Ctx(state, settings, logger, deadline, request_id, plugin_config)
        params = Params(request.get("params"))
        if spec.writes_index:
            with locks.exclusive(state.locks_dir, "writer", progress=spec.name):
                data = spec.fn(ctx, params)
        else:
            data = spec.fn(ctx, params)
        if not isinstance(data, dict):
            raise EngineError("ENGINE_ERROR", "action returned non-object")
        if spec.kind != "read":
            audit.record(state.audit_path, spec.name, spec.kind, "ok")
        logger.info("action", action=spec.name, ms=int((time.monotonic() - started) * 1000), ok=True)
        return {"ok": True, "data": data}
    except EngineError as exc:
        logger.error("action", action=spec.name, code=exc.code, detail=exc.message,
                     ms=int((time.monotonic() - started) * 1000))
        if spec.kind != "read":
            audit.record(state.audit_path, spec.name, spec.kind, "error", code=exc.code)
        return exc.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.error("action_crash", action=spec.name, error=repr(exc), trace=traceback.format_exc()[-2000:])
        if spec.kind != "read":
            audit.record(state.audit_path, spec.name, spec.kind, "crash")
        return EngineError("ENGINE_ERROR").to_dict()
