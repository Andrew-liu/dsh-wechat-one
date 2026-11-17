"""setup 功能：接入状态、一键接入 wechat-decrypt、刷新镜像与索引。"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from ..core import locks
from ..core.dispatch import Ctx, Params, action
from ..core.errors import EngineError
from ..data.index import builder, store, vector
from ..data.sources import open_source
from ..integrations import decrypt_tool, embedding
from ..shared.fmt import fmt_ts

STALE_HOURS = 24


def _index_info(ctx: Ctx) -> dict[str, Any]:
    tz = ctx.settings.tz()
    info: dict[str, Any] = {"indexed": False, "rebuild_required": False}
    if ctx.state.index_path.exists():
        try:
            con = store.connect_ro(ctx.state)
            try:
                m = store.meta(con)
            finally:
                con.close()
            info.update(indexed=True, version=int(m.get("index_version") or 0),
                        built_at=fmt_ts(int(m["built_at"]), tz) if m.get("built_at") else None,
                        messages=int(m.get("messages") or 0), conversations=int(m.get("conversations") or 0),
                        chunks=int(m.get("chunks") or 0),
                        first_time=fmt_ts(int(m["first_ts"]), tz) if (m.get("first_ts") or "").isdigit() else None,
                        last_time=fmt_ts(int(m["last_ts"]), tz) if (m.get("last_ts") or "").isdigit() else None,
                        _last_ts=int(m["last_ts"]) if (m.get("last_ts") or "").isdigit() else None)
        except EngineError:
            info["rebuild_required"] = True
    info["building"] = locks.is_locked(ctx.state.locks_dir, "writer")
    if info["building"]:
        info["building_progress"] = locks.holder(ctx.state.locks_dir, "writer").get("progress") or ""
    info["last_error"] = ctx.state.status().get("last_error")
    return info


def _source_changed_since_index(ctx: Ctx) -> bool | None:
    """镜像文件签名与索引记录不一致时说明镜像已更新、需要重建索引。"""
    if not ctx.state.index_path.exists():
        return None
    try:
        source = open_source(ctx.settings)
    except EngineError:
        return None
    try:
        sig = builder.signature(source)
    finally:
        source.close()
    try:
        con = store.connect_ro(ctx.state)
        try:
            recorded = {r[0]: (r[1], r[2]) for r in con.execute("SELECT key, mtime, size FROM shards")}
        finally:
            con.close()
    except EngineError:
        return None
    return any(tuple(recorded.get(k, ())) != tuple(v) for k, v in sig.items())


@action("status", kind="read", timeout_ms=20_000)
def status(ctx: Ctx, params: Params) -> dict[str, Any]:
    s = ctx.settings
    tool, tool_origin = decrypt_tool.locate(s, ctx.state)
    index = _index_info(ctx)
    warnings: list[str] = []

    source_available = False
    if s.source_path:
        try:
            open_source(s).close()
            source_available = True
        except EngineError as exc:
            warnings.append("数据来源不可用：" + exc.public_message())
    last_ts = index.pop("_last_ts", None)
    age_hours = round(max(0.0, time.time() - last_ts) / 3600, 1) if last_ts else None
    changed = _source_changed_since_index(ctx) if source_available else None
    stale = bool(changed) or (age_hours is not None and age_hours > STALE_HOURS)
    if changed:
        warnings.append("镜像已更新但索引未同步，请调用 wechat_one_refresh(reindex_only=true)。")
    elif age_hours is not None and age_hours > STALE_HOURS:
        warnings.append(f"索引最新消息距今约 {age_hours:.0f} 小时，需要最新记录时请先 wechat_one_refresh。")

    key_ok = decrypt_tool.key_ready(tool)
    platform_ok = os.name == "nt"
    if not index["indexed"] and not source_available:
        if not tool:
            nxt = "setup_install" if platform_ok else "configure_source"
        elif not key_ok:
            nxt = "setup_extract_key"
        else:
            nxt = "setup_decrypt"
    elif not index["indexed"] or index["rebuild_required"]:
        nxt = "refresh"
    elif stale:
        nxt = "refresh"
    else:
        nxt = "ready"

    vec: dict[str, Any] = {"enabled": s.embedding_enabled(), "model": s.embedding_model if s.embedding_enabled() else None}
    if vec["enabled"]:
        vec["available"] = embedding.available(s)
        if index["indexed"]:
            try:
                con = store.connect_ro(ctx.state)
                try:
                    vec["indexed_chunks"] = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
                finally:
                    con.close()
            except EngineError:
                pass
        if not vec["available"]:
            warnings.append("已开启向量检索但本机 Ollama 或模型不可用，检索将降级为全文检索。")

    return {
        "ready": index["indexed"] and not index["rebuild_required"],
        "next_action": nxt,
        "warnings": warnings,
        "source": {"configured": bool(s.source_path), "type": s.source_type, "origin": s.origin.get("source_path"),
                   "available": source_available, "mirror_latest": index.get("last_time"),
                   "mirror_age_hours": age_hours, "stale": stale},
        "decrypt_tool": {"found": tool is not None, "origin": tool_origin or None, "key_ready": key_ok,
                         "install_allowed": s.allow_decrypt_install, "platform_supported": platform_ok},
        "index": index,
        "vector": vec,
    }


def _rebuild(ctx: Ctx, force: bool = False) -> dict[str, Any]:
    try:
        result = builder.build_index(ctx.state, ctx.settings, force=force, check=ctx.check_deadline,
                                     progress=ctx.progress)
    except Exception as exc:  # noqa: BLE001
        code = exc.code if isinstance(exc, EngineError) else "ENGINE_ERROR"
        ctx.state.update_status(last_error=code, last_error_at=int(time.time()))
        raise
    ctx.state.update_status(last_error=None, last_refresh_at=int(time.time()))
    return result


def _sync_vectors(ctx: Ctx, warnings: list[str]) -> int | None:
    s = ctx.settings
    if not s.embedding_enabled():
        return None
    if not embedding.available(s):
        warnings.append("本机 Ollama 或 embedding 模型不可用，跳过向量化。")
        return None
    budget = min(ctx.remaining_seconds() - 60, 20 * 60)
    if budget <= 5:
        return None
    try:
        con = store.connect_rw(ctx.state.index_path)
        try:
            result = vector.sync(con, s, budget, check=ctx.check_deadline)
            con.execute("PRAGMA journal_mode=DELETE")
        finally:
            con.close()
    except (EngineError, sqlite3.Error) as exc:
        ctx.log.error("vector_sync", error=repr(exc))
        warnings.append("向量化未完成，已保留全文索引。")
        return None
    if result["remaining"]:
        warnings.append(f"还有 {result['remaining']} 个片段未向量化，下次刷新继续。")
    return result["indexed_chunks"]


def _fmt_counts(ctx: Ctx, result: dict[str, Any]) -> dict[str, Any]:
    tz = ctx.settings.tz()
    out = {k: result.get(k) for k in ("messages", "conversations", "chunks")}
    out["first_time"] = fmt_ts(result.get("first_ts"), tz)
    out["last_time"] = fmt_ts(result.get("last_ts"), tz)
    return out


def _decrypt_and_point(ctx: Ctx, tool) -> dict[str, Any]:
    ctx.progress("解密微信数据库")
    stats = decrypt_tool.decrypt(tool, ctx.log, timeout=int(max(60, min(ctx.remaining_seconds() - 120, 1500))))
    mirror = decrypt_tool.mirror_dir(tool)
    if ctx.settings.origin.get("source_path") != "plugin" and mirror.is_dir():
        current = ctx.settings.source_path
        if not current or os.path.normcase(os.path.abspath(current)) != os.path.normcase(os.path.abspath(mirror)):
            ctx.state.update_settings(sourcePath=str(mirror), sourceType="decrypted_dir")
        ctx.reload_settings()
    return stats


@action("setup", kind="write", timeout_ms=30 * 60_000, writes_index=True)
def setup(ctx: Ctx, params: Params) -> dict[str, Any]:
    step = params.str("step", required=True, max_len=20)
    if step not in ("install", "extract_key", "decrypt"):
        raise EngineError("INVALID_ARGS", "step 只能是 install / extract_key / decrypt", public=True)
    if step == "install":
        ctx.progress("安装 wechat-decrypt")
        result = decrypt_tool.install(ctx.settings, ctx.state, ctx.log)
        ctx.reload_settings()
        tool, _ = decrypt_tool.locate(ctx.settings, ctx.state)
        nxt = "setup_decrypt" if decrypt_tool.key_ready(tool) else "setup_extract_key"
        return {"step": step, **result, "next_action": nxt}
    tool, _ = decrypt_tool.locate(ctx.settings, ctx.state)
    if tool is None:
        raise EngineError("DECRYPT_TOOL_MISSING")
    if step == "extract_key":
        ctx.progress("等待取钥（请从桌面重新打开微信）")
        ok = decrypt_tool.extract_key(tool, ctx.log, check=ctx.check_deadline)
        if not ok:
            raise EngineError("SETUP_FAILED", "没有捕获到密钥：请确认看到提示后从桌面双击打开微信并登录；"
                                              "仍失败时以管理员身份运行 DSH 后重试。", public=True)
        return {"step": step, "status": "ok", "detail": "已获取数据库密钥（未返回密钥内容）。", "next_action": "setup_decrypt"}
    _decrypt_and_point(ctx, tool)
    result = _rebuild(ctx)
    return {"step": step, "status": "ok", "detail": "已解密并更新索引。", "next_action": "ready",
            "index": _fmt_counts(ctx, result)}


@action("refresh", kind="write", timeout_ms=30 * 60_000, writes_index=True)
def refresh(ctx: Ctx, params: Params) -> dict[str, Any]:
    reindex_only = params.bool("reindex_only", False)
    warnings: list[str] = []
    decrypted = False
    if not reindex_only:
        tool, _ = decrypt_tool.locate(ctx.settings, ctx.state)
        if tool is None:
            warnings.append("未检测到 wechat-decrypt，仅按现有镜像更新索引。")
        elif not decrypt_tool.key_ready(tool):
            warnings.append("wechat-decrypt 尚无密钥，仅按现有镜像更新索引；需要最新记录请先 wechat_one_setup(step=extract_key)。")
        else:
            try:
                _decrypt_and_point(ctx, tool)
                decrypted = True
            except EngineError as exc:
                if exc.code == "TIMEOUT":
                    raise
                warnings.append("解密未完成（" + exc.public_message() + "），已按现有镜像更新索引。")
    result = _rebuild(ctx)
    vectors = _sync_vectors(ctx, warnings)
    return {"decrypted": decrypted, "reindexed": result.get("reindexed", False),
            "changed_shards": result.get("changed_shards", 0), **_fmt_counts(ctx, result),
            "vector_indexed": vectors, "duration_seconds": result.get("duration_seconds"), "warnings": warnings}
