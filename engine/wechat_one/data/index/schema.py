"""索引库 schema 与迁移。schema_version 记录在 meta 表；版本不匹配时整库重建。"""
from __future__ import annotations

import sqlite3

# v2：messages 增加 url（链接卡片 / 正文里的第一个链接），供日报“精选链接”使用
SCHEMA_VERSION = 2

DDL = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, raw_id TEXT UNIQUE, name TEXT, kind TEXT,
        message_count INTEGER DEFAULT 0, first_ts INTEGER, last_ts INTEGER)""",
    """CREATE TABLE IF NOT EXISTS messages (
        rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, conv_id TEXT NOT NULL, ts INTEGER NOT NULL,
        sender TEXT, is_self INTEGER, type TEXT, text TEXT, chunk_rowid INTEGER, url TEXT)""",
    "CREATE INDEX IF NOT EXISTS messages_conv_ts ON messages(conv_id, ts, rowid)",
    "CREATE INDEX IF NOT EXISTS messages_ts ON messages(ts)",
    """CREATE TABLE IF NOT EXISTS chunks (
        rowid INTEGER PRIMARY KEY, conv_id TEXT NOT NULL, start_ts INTEGER, end_ts INTEGER,
        first_msg INTEGER, last_msg INTEGER, n INTEGER)""",
    "CREATE INDEX IF NOT EXISTS chunks_conv ON chunks(conv_id, start_ts)",
    "CREATE INDEX IF NOT EXISTS chunks_ts ON chunks(start_ts)",
    "CREATE TABLE IF NOT EXISTS shards (key TEXT PRIMARY KEY, mtime REAL, size INTEGER)",
    "CREATE TABLE IF NOT EXISTS conv_shards (conv_id TEXT, shard TEXT, PRIMARY KEY (conv_id, shard))",
    "CREATE TABLE IF NOT EXISTS vectors (chunk_rowid INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB)",
]

FTS_TABLES = ("messages_fts", "chunks_fts")
TOKENIZE = "unicode61 remove_diacritics 2"


def _create_fts(con: sqlite3.Connection, name: str) -> None:
    try:
        con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {name} USING fts5(body, content='', "
                    f"contentless_delete=1, tokenize='{TOKENIZE}')")
    except sqlite3.OperationalError:
        # SQLite < 3.43 不支持 contentless_delete：退回普通 FTS5 表（存储正文二元组，可按 rowid 删除）
        con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {name} USING fts5(body, tokenize='{TOKENIZE}')")


def create(con: sqlite3.Connection) -> None:
    for stmt in DDL:
        con.execute(stmt)
    for name in FTS_TABLES:
        _create_fts(con, name)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def version(con: sqlite3.Connection) -> int | None:
    try:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.Error:
        return None
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def compatible(con: sqlite3.Connection) -> bool:
    return version(con) == SCHEMA_VERSION
