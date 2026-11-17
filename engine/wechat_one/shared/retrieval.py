"""检索的唯一实现：会话查找、精确检索、主题检索（RAG）、上下文、按会话取消息。

persona / summary / eq 等功能都通过这里取数，不直接写 SQL。
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Iterator

from ..core.config import Settings
from ..core.errors import EngineError
from ..data.index import cjk, store, vector
from .fmt import decode_cursor, encode_cursor, fmt_ts, query_fingerprint, truncate

DEFAULT_KINDS = ("friend", "group")
TERM_CANDIDATES = 300
RRF_K = 60
PER_CONVERSATION_PER_PAGE = 3
PAGE_CHAR_BUDGET = 12_000
STALE_HOURS = 24


class Index:
    """一次请求内的只读索引视图。"""

    def __init__(self, con: sqlite3.Connection, settings: Settings):
        self.con = con
        self.settings = settings
        self.tz = settings.tz()
        self.meta = store.meta(con)
        self.version = self.meta.get("index_version", "0")
        self._allowed: set[str] | None | bool = False

    # ---------------------------------------------------------- 公共
    def coverage(self) -> dict[str, Any]:
        last = self.meta.get("last_ts") or ""
        first = self.meta.get("first_ts") or ""
        last_ts = int(last) if last.isdigit() else None
        return {"first_time": fmt_ts(int(first), self.tz) if first.isdigit() else None,
                "last_time": fmt_ts(last_ts, self.tz),
                "stale": bool(last_ts and time.time() - last_ts > STALE_HOURS * 3600)}

    def allowed(self) -> set[str] | None:
        """allowedConversations 配置解析为 conversation_id 集合；未配置返回 None（不限制）。"""
        if self._allowed is False:
            names = self.settings.allowed_conversations
            if not names:
                self._allowed = None
            else:
                marks = ",".join("?" * len(names))
                self._allowed = {r[0] for r in self.con.execute(
                    f"SELECT id FROM conversations WHERE raw_id IN ({marks}) OR name IN ({marks})", (*names, *names))}
        return self._allowed  # type: ignore[return-value]

    def check_allowed(self, conv_id: str) -> None:
        allowed = self.allowed()
        if allowed is not None and conv_id not in allowed:
            raise EngineError("CONVERSATION_NOT_ALLOWED")

    def _scope(self, alias: str, conversation_ids: list[str] | None, kinds: tuple[str, ...] | list[str] | None,
               conv_col: str) -> tuple[list[str], list[Any]]:
        where, args = [], []
        kinds = tuple(kinds) if kinds else DEFAULT_KINDS
        where.append(f"{alias}.kind IN ({','.join('?' * len(kinds))})")
        args.extend(kinds)
        allowed = self.allowed()
        ids = list(conversation_ids or [])
        if allowed is not None:
            ids = [i for i in ids if i in allowed] if ids else sorted(allowed)
            if not ids:
                ids = ["__none__"]
        if ids:
            where.append(f"{conv_col} IN ({','.join('?' * len(ids))})")
            args.extend(ids)
        return where, args

    def message_row(self, row: sqlite3.Row, limit: int, with_conv: bool = False) -> dict[str, Any]:
        text, cut = truncate(row["text"] or "", limit)
        out = {"message_id": row["id"], "time": fmt_ts(row["ts"], self.tz), "sender": row["sender"],
               "is_self": bool(row["is_self"]), "type": row["type"], "text": text, "truncated": cut}
        if with_conv:
            out.update(conversation_id=row["conv_id"], conversation=row["conv_name"], kind=row["conv_kind"])
        return out

    def conversation(self, conv_id: str) -> sqlite3.Row:
        row = self.con.execute("SELECT id, raw_id, name, kind, message_count, first_ts, last_ts FROM conversations "
                               "WHERE id=?", (conv_id,)).fetchone()
        if not row:
            raise EngineError("CONVERSATION_NOT_FOUND")
        self.check_allowed(conv_id)
        return row

    # ---------------------------------------------------------- 会话
    def contacts(self, query: str | None, kind: str | None, limit: int, cursor: str | None) -> dict[str, Any]:
        where, args = self._scope("c", None, (kind,) if kind else None, "c.id")
        where.append("c.message_count > 0")
        if query:
            where.append("(c.name LIKE ? ESCAPE '\\')")
            args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        clause = " AND ".join(where)
        fp = query_fingerprint({"a": "contacts", "q": query, "k": kind, "l": limit})
        offset = decode_cursor(cursor, self.version, fp)
        total = self.con.execute(f"SELECT COUNT(*) FROM conversations c WHERE {clause}", args).fetchone()[0]
        rows = self.con.execute(
            f"SELECT c.id, c.name, c.kind, c.message_count, c.last_ts FROM conversations c WHERE {clause} "
            "ORDER BY c.last_ts DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        items = [{"conversation_id": r["id"], "name": r["name"], "kind": r["kind"],
                  "message_count": r["message_count"], "last_time": fmt_ts(r["last_ts"], self.tz)} for r in rows]
        has_more = offset + len(items) < total
        return {"conversations": items, "total": total, "returned": len(items), "has_more": has_more,
                "next_cursor": encode_cursor(self.version, fp, offset + len(items)) if has_more else None}

    # ---------------------------------------------------------- 精确检索
    def search(self, query: str, conversation_ids: list[str], sender: str | None, start: int | None,
               end: int | None, limit: int, cursor: str | None) -> dict[str, Any]:
        match = cjk.all_of(query)
        if not match:
            raise EngineError("INVALID_ARGS", "检索词中没有可检索的文字。", public=True)
        where, args = self._scope("c", conversation_ids, None, "m.conv_id")
        if sender:
            where.append("m.sender LIKE ?")
            args.append(f"%{sender}%")
        if start is not None:
            where.append("m.ts >= ?")
            args.append(start)
        if end is not None:
            where.append("m.ts < ?")
            args.append(end)
        clause = " AND ".join(where)
        base = ("FROM messages_fts f JOIN messages m ON m.rowid = f.rowid JOIN conversations c ON c.id = m.conv_id "
                f"WHERE messages_fts MATCH ? AND {clause}")
        fp = query_fingerprint({"a": "search", "q": query, "c": conversation_ids, "s": sender, "t": [start, end], "l": limit})
        offset = decode_cursor(cursor, self.version, fp)
        try:
            total = self.con.execute(f"SELECT COUNT(*) {base}", (match, *args)).fetchone()[0]
            rows = self.con.execute(
                f"SELECT m.rowid, m.id, m.conv_id, m.ts, m.sender, m.is_self, m.type, m.text, "
                f"c.name AS conv_name, c.kind AS conv_kind {base} ORDER BY m.ts DESC LIMIT ? OFFSET ?",
                (match, *args, limit, offset)).fetchall()
        except sqlite3.OperationalError as exc:
            raise EngineError("INVALID_ARGS", "检索词无法解析，请换个说法。", public=True) from exc
        limit_chars = self.settings.max_content_chars
        items = [self.message_row(r, limit_chars, with_conv=True) for r in rows]
        has_more = offset + len(items) < total
        return {"messages": items, "total": total, "returned": len(items), "has_more": has_more,
                "next_cursor": encode_cursor(self.version, fp, offset + len(items)) if has_more else None,
                "coverage": self.coverage(),
                "note": None if total else "在本地索引覆盖范围内未检索到；不代表聊天中一定不存在。"}

    # ---------------------------------------------------------- 主题检索
    def _term_hits(self, term: str, where: list[str], args: list[Any]) -> list[int]:
        match = cjk.phrase(term)
        if not match:
            return []
        clause = " AND ".join(where)
        try:
            rows = self.con.execute(
                "SELECT f.rowid FROM chunks_fts f JOIN chunks k ON k.rowid = f.rowid "
                "JOIN conversations c ON c.id = k.conv_id "
                f"WHERE chunks_fts MATCH ? AND {clause} ORDER BY bm25(chunks_fts) LIMIT ?",
                (match, *args, TERM_CANDIDATES)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r[0] for r in rows]

    def rank_chunks(self, topic: str, keywords: list[str], conversation_ids: list[str],
                    kinds: list[str] | None, start: int | None, end: int | None) -> tuple[list[tuple[int, float, list[str]]], dict]:
        where, args = self._scope("c", conversation_ids, kinds, "k.conv_id")
        if start is not None:
            where.append("k.end_ts >= ?")
            args.append(start)
        if end is not None:
            where.append("k.start_ts < ?")
            args.append(end)
        terms: list[tuple[str, float]] = [(topic, 2.0)]
        for k in keywords:
            if k.strip() and k.strip() != topic.strip():
                terms.append((k.strip(), 1.0))
        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        missing: list[str] = []
        for term, weight in terms:
            hits = self._term_hits(term, where, args)
            if not hits:
                missing.append(term)
            for rank, rowid in enumerate(hits):
                scores[rowid] = scores.get(rowid, 0.0) + weight / (RRF_K + rank)
                matched.setdefault(rowid, []).append(term)
        info: dict[str, Any] = {"mode": "fts", "vector": "off", "terms_without_hits": missing}
        if scores and self.settings.embedding_enabled():
            try:
                order = vector.rerank(self.con, self.settings, topic + " " + " ".join(keywords), list(scores))
                for rank, rowid in enumerate(order):
                    scores[rowid] += 2.0 / (RRF_K + rank)
                info.update(mode="hybrid", vector="on" if order else "no_vectors")
            except EngineError:
                info["vector"] = "unavailable"
        if scores:
            spans = dict(self.con.execute(
                f"SELECT rowid, end_ts FROM chunks WHERE rowid IN ({','.join('?' * len(scores))})", list(scores)).fetchall())
            lo, hi = min(spans.values()), max(spans.values())
            for rowid in scores:
                recency = (spans[rowid] - lo) / (hi - lo) if hi > lo else 0.0
                scores[rowid] += 0.003 * recency
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(rowid, score, matched.get(rowid, [])) for rowid, score in ranked], info

    def _paginate(self, ranked: list[tuple[int, float, list[str]]], size: int) -> list[list[tuple[int, float, list[str]]]]:
        conv_of = {}
        ids = [r[0] for r in ranked]
        for start in range(0, len(ids), 900):
            part = ids[start:start + 900]
            conv_of.update(dict(self.con.execute(
                f"SELECT rowid, conv_id FROM chunks WHERE rowid IN ({','.join('?' * len(part))})", part).fetchall()))
        pages, remaining = [], list(ranked)
        while remaining and len(pages) < 50:
            page, counts, rest = [], {}, []
            for item in remaining:
                conv = conv_of.get(item[0])
                if len(page) < size and counts.get(conv, 0) < PER_CONVERSATION_PER_PAGE:
                    page.append(item)
                    counts[conv] = counts.get(conv, 0) + 1
                else:
                    rest.append(item)
            pages.append(page)
            remaining = rest
        return pages

    def topic_search(self, topic: str, keywords: list[str], conversation_ids: list[str], kinds: list[str] | None,
                     start: int | None, end: int | None, limit: int, cursor: str | None) -> dict[str, Any]:
        fp = query_fingerprint({"a": "topic", "t": topic, "k": keywords, "c": conversation_ids, "n": kinds,
                                "d": [start, end], "l": limit})
        page_no = decode_cursor(cursor, self.version, fp)
        ranked, info = self.rank_chunks(topic, keywords, conversation_ids, kinds, start, end)
        pages = self._paginate(ranked, limit)
        page = pages[page_no] if page_no < len(pages) else []
        rowids = [r[0] for r in page]
        chunk_rows = {}
        if rowids:
            for r in self.con.execute(
                    "SELECT k.rowid, k.conv_id, k.start_ts, k.end_ts, k.first_msg, k.last_msg, k.n, "
                    "c.name, c.kind FROM chunks k JOIN conversations c ON c.id = k.conv_id "
                    f"WHERE k.rowid IN ({','.join('?' * len(rowids))})", rowids):
                chunk_rows[r["rowid"]] = r
        total_msgs = sum(chunk_rows[r]["n"] for r in rowids if r in chunk_rows) or 1
        per_msg = max(80, min(self.settings.max_content_chars, 300, PAGE_CHAR_BUDGET // total_msgs))
        chunks = []
        for rowid, score, terms in page:
            c = chunk_rows.get(rowid)
            if c is None:
                continue
            msgs = self.con.execute(
                "SELECT rowid, id, conv_id, ts, sender, is_self, type, text FROM messages "
                "WHERE rowid BETWEEN ? AND ? ORDER BY rowid", (c["first_msg"], c["last_msg"])).fetchall()
            chunks.append({
                "chunk_id": f"k{rowid}", "conversation_id": c["conv_id"], "conversation": c["name"], "kind": c["kind"],
                "start_time": fmt_ts(c["start_ts"], self.tz), "end_time": fmt_ts(c["end_ts"], self.tz),
                "matched_terms": terms, "score": round(score * 1000, 2),
                "messages": [self.message_row(m, per_msg) for m in msgs],
            })
        has_more = page_no + 1 < len(pages)
        note = None
        if not ranked:
            note = "在本地索引覆盖范围内未检索到相关片段；可换一组检索词再试，或确认索引是否需要刷新。"
        elif len(ranked) < 3:
            note = "相关片段较少，可补充同义词、口语或圈内说法再检索一轮。"
        return {"chunks": chunks, "total": len(ranked), "returned": len(chunks), "has_more": has_more,
                "next_cursor": encode_cursor(self.version, fp, page_no + 1) if has_more else None,
                "retrieval": info, "coverage": self.coverage(), "note": note}

    # ---------------------------------------------------------- 上下文
    def context(self, message_id: str, before: int, after: int) -> dict[str, Any]:
        target = self.con.execute("SELECT rowid, id, conv_id, ts, sender, is_self, type, text FROM messages WHERE id=?",
                                  (message_id,)).fetchone()
        if not target:
            raise EngineError("MESSAGE_NOT_FOUND")
        conv = self.conversation(target["conv_id"])
        cols = "rowid, id, conv_id, ts, sender, is_self, type, text"
        prev = self.con.execute(f"SELECT {cols} FROM messages WHERE conv_id=? AND rowid < ? ORDER BY rowid DESC LIMIT ?",
                                (conv["id"], target["rowid"], before + 1)).fetchall()
        nxt = self.con.execute(f"SELECT {cols} FROM messages WHERE conv_id=? AND rowid > ? ORDER BY rowid LIMIT ?",
                               (conv["id"], target["rowid"], after + 1)).fetchall()
        limit = self.settings.max_content_chars
        msgs = [self.message_row(r, limit) for r in reversed(prev[:before])]
        msgs.append(self.message_row(target, max(limit, 2000)))
        msgs.extend(self.message_row(r, limit) for r in nxt[:after])
        return {"conversation_id": conv["id"], "conversation": conv["name"], "kind": conv["kind"],
                "target": message_id, "messages": msgs,
                "has_more_before": len(prev) > before, "has_more_after": len(nxt) > after}

    # ---------------------------------------------------------- 给其他功能用
    MESSAGE_COLS = "rowid, id, conv_id, ts, sender, is_self, type, text, url"

    def iter_messages(self, conv_id: str, start: int | None = None, end: int | None = None) -> Iterator[sqlite3.Row]:
        sql = f"SELECT {self.MESSAGE_COLS} FROM messages WHERE conv_id=?"
        args: list[Any] = [conv_id]
        if start is not None:
            sql += " AND ts >= ?"
            args.append(start)
        if end is not None:
            sql += " AND ts < ?"
            args.append(end)
        yield from self.con.execute(sql + " ORDER BY rowid", args)

    def last_ts(self) -> int | None:
        value = self.meta.get("last_ts") or ""
        return int(value) if value.isdigit() else None

    def resolve_conversations(self, queries: list[str]) -> tuple[list[sqlite3.Row], list[dict[str, Any]]]:
        """把 conversation_id / 原始 ID / 精确名称解析为会话。返回 (会话列表, 问题列表)。

        名称必须精确且唯一；重名或找不到时给出候选（只含显示名与 conversation_id），由用户确认。
        """
        cols = "id, raw_id, name, kind, message_count, first_ts, last_ts"
        found: list[sqlite3.Row] = []
        problems: list[dict[str, Any]] = []
        seen: set[str] = set()
        allowed = self.allowed()
        for raw in queries:
            q = (raw or "").strip()
            rows = self.con.execute(f"SELECT {cols} FROM conversations WHERE id=? OR raw_id=?", (q, q)).fetchall()
            if not rows:
                rows = self.con.execute(f"SELECT {cols} FROM conversations WHERE name=?", (q,)).fetchall()
            if allowed is not None:
                rows = [r for r in rows if r["id"] in allowed]
            if len(rows) == 1:
                if rows[0]["id"] not in seen:
                    seen.add(rows[0]["id"])
                    found.append(rows[0])
                continue
            if rows:
                problems.append({"query": q, "problem": "ambiguous",
                                 "candidates": [{"conversation_id": r["id"], "name": r["name"], "kind": r["kind"]}
                                                for r in rows[:10]]})
                continue
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            near = self.con.execute(f"SELECT {cols} FROM conversations WHERE name LIKE ? ESCAPE '\\' "
                                    "AND message_count > 0 ORDER BY last_ts DESC LIMIT 10", (like,)).fetchall() if q else []
            if allowed is not None:
                near = [r for r in near if r["id"] in allowed]
            problems.append({"query": q, "problem": "not_found",
                             "candidates": [{"conversation_id": r["id"], "name": r["name"], "kind": r["kind"]}
                                            for r in near]})
        return found, problems


def open_index(state, settings: Settings) -> Index:
    return Index(store.connect_ro(state), settings)
