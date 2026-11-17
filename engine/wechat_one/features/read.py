"""read 功能：会话查找、精确检索、主题检索、上下文。参数在 JS 侧已严格校验，这里再兜底。"""
from __future__ import annotations

import re
from typing import Any

from ..core.dispatch import Ctx, Params, action
from ..core.errors import EngineError
from ..shared.fmt import day_range
from ..shared.retrieval import open_index

CONV_RE = re.compile(r"^conv_[0-9a-f]{16}$")
MSG_RE = re.compile(r"^m[0-9a-f]{16}$")


def _conv_ids(params: Params) -> list[str]:
    ids = params.str_list("conversation_ids", max_items=20, max_len=30)
    if any(not CONV_RE.match(i) for i in ids):
        raise EngineError("INVALID_ARGS", "conversation_id 格式无效", public=True)
    return ids


@action("contacts", kind="read")
def contacts(ctx: Ctx, params: Params) -> dict[str, Any]:
    kind = params.str("kind", max_len=10)
    if kind not in (None, "friend", "group"):
        raise EngineError("INVALID_ARGS", "kind 只能是 friend / group", public=True)
    index = open_index(ctx.state, ctx.settings)
    try:
        return index.contacts(params.str("query", max_len=50), kind, params.int("limit", default=20, lo=1, hi=50),
                              params.str("cursor", max_len=200))
    finally:
        index.con.close()


@action("search", kind="read")
def search(ctx: Ctx, params: Params) -> dict[str, Any]:
    tz = ctx.settings.tz()
    start, end = day_range(params.str("start_date", max_len=10), params.str("end_date", max_len=10), tz)
    index = open_index(ctx.state, ctx.settings)
    try:
        return index.search(params.str("query", required=True, max_len=100), _conv_ids(params),
                            params.str("sender", max_len=50), start, end,
                            params.int("limit", default=ctx.settings.max_items, lo=1, hi=100),
                            params.str("cursor", max_len=200))
    finally:
        index.con.close()


@action("topic_search", kind="read", timeout_ms=30_000)
def topic_search(ctx: Ctx, params: Params) -> dict[str, Any]:
    tz = ctx.settings.tz()
    start, end = day_range(params.str("start_date", max_len=10), params.str("end_date", max_len=10), tz)
    kinds = params.str_list("kinds", max_items=2, max_len=10)
    if any(k not in ("friend", "group") for k in kinds):
        raise EngineError("INVALID_ARGS", "kinds 只能包含 friend / group", public=True)
    keywords = params.str_list("keywords", required=True, max_items=20, max_len=30)
    if not keywords:
        raise EngineError("INVALID_ARGS", "keywords 至少 1 个", public=True)
    index = open_index(ctx.state, ctx.settings)
    try:
        return index.topic_search(params.str("topic", required=True, max_len=50), keywords, _conv_ids(params),
                                  kinds or None, start, end, params.int("limit", default=10, lo=1, hi=20),
                                  params.str("cursor", max_len=200))
    finally:
        index.con.close()


@action("context", kind="read")
def context(ctx: Ctx, params: Params) -> dict[str, Any]:
    message_id = params.str("message_id", required=True, max_len=20)
    if not MSG_RE.match(message_id):
        raise EngineError("INVALID_ARGS", "message_id 格式无效", public=True)
    index = open_index(ctx.state, ctx.settings)
    try:
        return index.context(message_id, params.int("before", default=5, lo=0, hi=20),
                             params.int("after", default=5, lo=0, hi=20))
    finally:
        index.con.close()
