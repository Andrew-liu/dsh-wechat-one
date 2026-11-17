"""通用格式化与分页游标。"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.errors import EngineError


def fmt_ts(ts: int | None, tz, with_seconds: bool = False) -> str | None:
    if not ts:
        return None
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M")


def day_range(start_date: str | None, end_date: str | None, tz) -> tuple[int | None, int | None]:
    """日期（含当天）转换为 [start, end) 时间戳。"""
    def parse(d: str) -> datetime:
        try:
            return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError as exc:
            raise EngineError("INVALID_ARGS", "日期需为 YYYY-MM-DD", public=True) from exc

    start = int(parse(start_date).timestamp()) if start_date else None
    end = int((parse(end_date) + timedelta(days=1)).timestamp()) if end_date else None
    if start is not None and end is not None and start >= end:
        raise EngineError("INVALID_ARGS", "start_date 不能晚于 end_date", public=True)
    return start, end


def truncate(text: str, limit: int) -> tuple[str, bool]:
    text = text or ""
    return (text, False) if len(text) <= limit else (text[:limit] + "…", True)


def query_fingerprint(params: dict[str, Any]) -> str:
    raw = json.dumps({k: v for k, v in params.items() if k != "cursor"}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def encode_cursor(version: str, fingerprint: str, offset: int) -> str:
    raw = json.dumps({"v": version, "q": fingerprint, "o": offset}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, version: str, fingerprint: str) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if data.get("v") != version or data.get("q") != fingerprint:
            raise ValueError
        offset = int(data["o"])
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise EngineError("INVALID_CURSOR") from exc
