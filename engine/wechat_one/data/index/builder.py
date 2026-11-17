"""索引构建：在 index.next.sqlite 中增量构建，校验通过后原子替换 index.sqlite。

增量规则：按镜像各数据库文件的 (mtime, size) 判断变化；只重新导入表位于变化分片中的会话，
其余会话直接沿用（从当前索引复制）。联系人库变化时只刷新会话名。
构建失败或超时：丢弃 next 文件，保留上一版可用索引。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ...core.errors import EngineError
from ...core.state import State
from ..models import Chat, Message
from ..parsing import disambiguate_names
from ..sources import Source, open_source
from . import chunker, cjk, schema, store

TEXTUAL = frozenset({"text", "quote", "link", "file", "forward", "miniprogram", "channels", "location", "card"})
EPOCH = datetime(1990, 1, 1, tzinfo=timezone.utc)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)


def kind_of(raw_id: str) -> str:
    if raw_id.endswith("@chatroom"):
        return "group"
    if raw_id.startswith("gh_"):
        return "official"
    return "friend"


def message_key(raw_chat_id: str, m: Message) -> str:
    seed = f"{raw_chat_id}|{m.msg_id}|{int(m.timestamp.timestamp())}"
    return "m" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 分片签名
def _shard_files(source: Source) -> dict[str, Path]:
    layout = getattr(source, "layout", None)
    if layout is not None:
        files: dict[str, Path] = {}
        if layout.contact:
            files["contact"] = layout.contact
        for path in layout.messages:
            files["message/" + path.name] = path
        return files
    path = getattr(source, "path", None)
    return {"json": Path(path)} if path else {}


def signature(source: Source) -> dict[str, tuple[float, int]]:
    sig: dict[str, tuple[float, int]] = {}
    for key, path in _shard_files(source).items():
        try:
            st = path.stat()
            sig[key] = (round(st.st_mtime, 3), int(st.st_size))
        except OSError:
            continue
    return sig


def conversation_shards(source: Source) -> dict[str, set[str]]:
    name2table = getattr(source, "_name2table", None)
    if callable(name2table):
        return {user: {"message/" + p.name for p, _ in locs} for user, locs in name2table().items()}
    return {}


# ------------------------------------------------------------------ 构建
class Builder:
    def __init__(self, state: State, source: Source, *, check: Callable[[], None] = lambda: None,
                 progress: Callable[[str], None] = lambda _t: None):
        self.state = state
        self.source = source
        self.check = check
        self.progress = progress

    def _previous(self) -> tuple[dict[str, tuple[float, int]], int] | None:
        path = self.state.index_path
        if not path.exists():
            return None
        try:
            con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                if not schema.compatible(con):
                    return None
                shards = {r[0]: (r[1], r[2]) for r in con.execute("SELECT key, mtime, size FROM shards")}
                row = con.execute("SELECT value FROM meta WHERE key='index_version'").fetchone()
                return shards, int(row[0]) if row and row[0] else 0
            finally:
                con.close()
        except sqlite3.Error:
            return None

    def run(self, force: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        sig = signature(self.source)
        previous = None if force else self._previous()
        if previous is not None:
            prev_sig, prev_version = previous
            changed = {k for k in set(sig) | set(prev_sig) if tuple(prev_sig.get(k, ())) != tuple(sig.get(k, ()))}
            if not changed:
                return {"reindexed": False, "changed_shards": 0, **self._counts_from(self.state.index_path)}
        else:
            prev_version, changed = 0, set(sig)
        fresh = previous is None
        next_path = self.state.index_next_path
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                Path(str(next_path) + suffix).unlink()
            except FileNotFoundError:
                pass
        if not fresh:
            src = sqlite3.connect(self.state.index_path.resolve().as_uri() + "?mode=ro", uri=True)
            dst = sqlite3.connect(str(next_path))
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
        con = store.connect_rw(next_path)
        try:
            schema.create(con)
            stats = self._ingest(con, changed, fresh)
            store.set_meta(con, index_version=prev_version + 1, built_at=int(time.time()),
                           messages=stats["messages"], conversations=stats["conversations"], chunks=stats["chunks"],
                           first_ts=stats.get("first_ts") or "", last_ts=stats.get("last_ts") or "")
            con.execute("DELETE FROM shards")
            con.executemany("INSERT INTO shards(key, mtime, size) VALUES (?, ?, ?)",
                            [(k, v[0], v[1]) for k, v in sig.items()])
            con.commit()
            con.execute("PRAGMA journal_mode=DELETE")
        except BaseException:
            con.close()
            self._discard(next_path)
            raise
        con.close()
        if not store.quick_check(next_path):
            self._discard(next_path)
            raise EngineError("INDEX_REBUILDING", "new index failed quick_check")
        self._swap(next_path)
        return {"reindexed": True, "changed_shards": len(changed),
                "duration_seconds": round(time.monotonic() - started, 1), **stats}

    # -------------------------------------------------------------- 内部
    @staticmethod
    def _discard(path: Path) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                Path(str(path) + suffix).unlink()
            except OSError:
                pass

    def _swap(self, next_path: Path) -> None:
        target = self.state.index_path
        for attempt in range(40):
            try:
                os.replace(next_path, target)
                return
            except PermissionError:
                time.sleep(0.25)  # Windows：有读请求正在打开旧索引
        raise EngineError("BUSY", "索引正被读取，新索引已保留，稍后再次刷新即可替换。", public=True)

    @staticmethod
    def _counts_from(path: Path) -> dict[str, Any]:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            m = {r[0]: r[1] for r in con.execute("SELECT key, value FROM meta")}
        finally:
            con.close()
        return {k: int(m[k]) if (m.get(k) or "").isdigit() else None
                for k in ("messages", "conversations", "chunks", "first_ts", "last_ts")}

    def _ingest(self, con: sqlite3.Connection, changed: set[str], fresh: bool) -> dict[str, Any]:
        chats = self.source.list_chats(include_private=True)
        shards = conversation_shards(self.source)
        message_changes = {k for k in changed if k != "contact"}
        live_ids: set[str] = set()
        affected: list[Chat] = []
        for chat in chats:
            conv_id = self.state.hmac_id("conv", chat.id)
            live_ids.add(conv_id)
            con.execute(
                "INSERT INTO conversations(id, raw_id, name, kind) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind",
                (conv_id, chat.id, chat.name, kind_of(chat.id)))
            touched = shards.get(chat.id)
            if fresh or "json" in message_changes or touched is None or touched & message_changes:
                affected.append(chat)
        stale = [r[0] for r in con.execute("SELECT id FROM conversations")] if not fresh else []
        for conv_id in stale:
            if conv_id not in live_ids:
                self._delete_conversation(con, conv_id)
                con.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        next_msg = (con.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0] or 0) + 1
        next_chunk = (con.execute("SELECT COALESCE(MAX(rowid), 0) FROM chunks").fetchone()[0] or 0) + 1
        total = len(affected)
        for i, chat in enumerate(affected, 1):
            self.check()
            if i == 1 or i % 20 == 0:
                self.progress(f"索引会话 {i}/{total}")
            next_msg, next_chunk = self._reingest(con, chat, shards.get(chat.id, set()), next_msg, next_chunk)
            if i % 50 == 0:
                con.commit()
        con.commit()
        row = con.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM messages").fetchone()
        convs = con.execute("SELECT COUNT(*) FROM conversations WHERE message_count > 0").fetchone()[0]
        chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"messages": row[0], "conversations": convs, "chunks": chunks, "first_ts": row[1], "last_ts": row[2],
                "reingested_conversations": total}

    @staticmethod
    def _delete_conversation(con: sqlite3.Connection, conv_id: str) -> None:
        con.execute("DELETE FROM messages_fts WHERE rowid IN (SELECT rowid FROM messages WHERE conv_id=?)", (conv_id,))
        con.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT rowid FROM chunks WHERE conv_id=?)", (conv_id,))
        con.execute("DELETE FROM vectors WHERE chunk_rowid IN (SELECT rowid FROM chunks WHERE conv_id=?)", (conv_id,))
        con.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        con.execute("DELETE FROM chunks WHERE conv_id=?", (conv_id,))
        con.execute("DELETE FROM conv_shards WHERE conv_id=?", (conv_id,))

    def _reingest(self, con: sqlite3.Connection, chat: Chat, shard_keys: set[str],
                  next_msg: int, next_chunk: int) -> tuple[int, int]:
        conv_id = self.state.hmac_id("conv", chat.id)
        self._delete_conversation(con, conv_id)
        messages = self.source.messages(chat, EPOCH, FAR_FUTURE)
        if chat.id.endswith("@chatroom"):
            disambiguate_names(messages)
        rows, fts_rows, items = [], [], []
        seen: set[str] = set()
        for m in messages:
            key = message_key(chat.id, m)
            if key in seen:
                continue
            seen.add(key)
            ts = int(m.timestamp.timestamp())
            sender = "我" if m.is_self else (m.sender_name or "未知")
            searchable = m.type in TEXTUAL and bool(m.text.strip())
            extra = m.extra or {}
            url = extra.get("url") or next(iter(extra.get("urls") or []), None)
            rows.append((next_msg, key, conv_id, ts, sender, 1 if m.is_self else 0, m.type, m.text,
                         str(url)[:1000] if url else None))
            if searchable:
                body = cjk.index_text(m.text)
                if body:
                    fts_rows.append((next_msg, body))
            items.append(chunker.Item(next_msg, ts, sender, m.text, searchable))
            next_msg += 1
        con.executemany("INSERT INTO messages(rowid, id, conv_id, ts, sender, is_self, type, text, url) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        con.executemany("INSERT INTO messages_fts(rowid, body) VALUES (?, ?)", fts_rows)
        chunk_rows, chunk_fts, links = [], [], []
        for chunk in chunker.split(items):
            chunk_rows.append((next_chunk, conv_id, chunk.start_ts, chunk.end_ts,
                               chunk.items[0].rowid, chunk.items[-1].rowid, len(chunk.items)))
            body = cjk.index_text(chunk.body())
            if body:
                chunk_fts.append((next_chunk, body))
            links.append((next_chunk, chunk.items[0].rowid, chunk.items[-1].rowid))
            next_chunk += 1
        con.executemany("INSERT INTO chunks(rowid, conv_id, start_ts, end_ts, first_msg, last_msg, n) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", chunk_rows)
        con.executemany("INSERT INTO chunks_fts(rowid, body) VALUES (?, ?)", chunk_fts)
        con.executemany("UPDATE messages SET chunk_rowid=? WHERE rowid BETWEEN ? AND ?", links)
        first = min((r[3] for r in rows), default=None)
        last = max((r[3] for r in rows), default=None)
        con.execute("UPDATE conversations SET message_count=?, first_ts=?, last_ts=? WHERE id=?",
                    (len(rows), first, last, conv_id))
        con.executemany("INSERT OR IGNORE INTO conv_shards(conv_id, shard) VALUES (?, ?)",
                        [(conv_id, s) for s in sorted(shard_keys)])
        return next_msg, next_chunk


def build_index(state: State, settings, *, force: bool = False, check=lambda: None,
                progress=lambda _t: None) -> dict[str, Any]:
    source = open_source(settings)
    try:
        result = Builder(state, source, check=check, progress=progress).run(force=force)
    finally:
        source.close()
    return result
