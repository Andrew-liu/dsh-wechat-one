"""片段向量：增量写入与候选集重排。向量以 float32 存 BLOB，检索时只在全文召回的候选集合上计算余弦。"""
from __future__ import annotations

import math
import sqlite3
import time
from array import array
from typing import Callable

from ...core.config import Settings
from ...integrations import embedding

BATCH = 32
MAX_TEXT = 1200


def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def chunk_text(con: sqlite3.Connection, chunk_rowid: int) -> str:
    row = con.execute("SELECT first_msg, last_msg FROM chunks WHERE rowid=?", (chunk_rowid,)).fetchone()
    if not row:
        return ""
    lines = [f"{r[0]}: {r[1]}" for r in con.execute(
        "SELECT sender, text FROM messages WHERE rowid BETWEEN ? AND ? AND type IN "
        "('text','quote','link','file','forward','miniprogram','channels','location','card') ORDER BY rowid",
        (row[0], row[1]))]
    return "\n".join(lines)[:MAX_TEXT]


def sync(con: sqlite3.Connection, settings: Settings, budget_seconds: float,
         check: Callable[[], None] = lambda: None) -> dict[str, int]:
    """为尚无向量（或模型变化）的片段补向量，直到预算用完；剩余部分下次刷新继续。"""
    model = settings.embedding_model
    con.execute("DELETE FROM vectors WHERE model <> ?", (model,))
    pending = [r[0] for r in con.execute(
        "SELECT c.rowid FROM chunks c LEFT JOIN vectors v ON v.chunk_rowid = c.rowid WHERE v.chunk_rowid IS NULL "
        "ORDER BY c.start_ts DESC")]
    done = 0
    deadline = time.monotonic() + budget_seconds
    for i in range(0, len(pending), BATCH):
        if time.monotonic() > deadline:
            break
        check()
        ids = pending[i:i + BATCH]
        texts = [chunk_text(con, cid) or " " for cid in ids]
        vectors = embedding.embed(texts, settings)
        con.executemany("INSERT OR REPLACE INTO vectors(chunk_rowid, model, dim, vec) VALUES (?, ?, ?, ?)",
                        [(cid, model, len(v), pack(v)) for cid, v in zip(ids, vectors)])
        con.commit()
        done += len(ids)
    remaining = len(pending) - done
    total = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    return {"embedded": done, "remaining": remaining, "indexed_chunks": total}


def rerank(con: sqlite3.Connection, settings: Settings, query: str, candidates: list[int]) -> list[int]:
    """返回按向量相似度排序的候选 rowid（只含已有向量的片段）。"""
    if not candidates:
        return []
    qvec = embedding.embed([query], settings, timeout=30)[0]
    scored = []
    for start in range(0, len(candidates), 500):
        part = candidates[start:start + 500]
        marks = ",".join("?" * len(part))
        for rowid, blob in con.execute(
                f"SELECT chunk_rowid, vec FROM vectors WHERE model=? AND chunk_rowid IN ({marks})",
                (settings.embedding_model, *part)):
            scored.append((cosine(qvec, unpack(blob)), rowid))
    scored.sort(reverse=True)
    return [rowid for _, rowid in scored]
