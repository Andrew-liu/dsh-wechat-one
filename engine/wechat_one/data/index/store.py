"""索引库读写入口。读连接只读打开；索引缺失、版本不符或损坏时给出明确错误码。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ...core.errors import EngineError
from ...core.state import State
from . import schema


def connect_rw(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def connect_ro(state: State) -> sqlite3.Connection:
    path = state.index_path
    if not path.exists():
        raise EngineError("NOT_INDEXED")
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        if not schema.compatible(con):
            con.close()
            raise EngineError("INDEX_REBUILDING")
        return con
    except sqlite3.DatabaseError as exc:
        raise EngineError("INDEX_REBUILDING", f"index unreadable: {exc}") from exc


def meta(con: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}


def set_meta(con: sqlite3.Connection, **values: Any) -> None:
    con.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    [(k, "" if v is None else str(v)) for k, v in values.items()])


def quick_check(path: Path) -> bool:
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False
