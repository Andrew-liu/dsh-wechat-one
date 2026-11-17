"""报告时间窗：一律左闭右开 [since, until)，按配置时区计算。

思路与周期规则来自 Andrew-liu/wechat-summary（MIT）的 window.py：
不指定日期时出“上一个完整周期”（日报=昨天，周报=上周，月报=上月）；指定日期时出该日期所在周期。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from ..core.errors import EngineError

PERIOD_LABELS = {"daily": "日报", "weekly": "周报", "monthly": "月报", "custom": "专题报告"}
WEEKDAY_NAMES = "一二三四五六日"


@dataclass(frozen=True)
class Window:
    period: str
    since: datetime
    until: datetime

    @property
    def label(self) -> str:
        return PERIOD_LABELS[self.period]

    @property
    def days(self) -> list[date]:
        result, day = [], self.since.date()
        last = (self.until - timedelta(microseconds=1)).date()
        while day <= last:
            result.append(day)
            day += timedelta(days=1)
        return result

    @property
    def start_ts(self) -> int:
        return int(self.since.timestamp())

    @property
    def end_ts(self) -> int:
        return int(self.until.timestamp())

    def range_text(self) -> str:
        last = (self.until - timedelta(days=1)).date()
        if last == self.since.date():
            return f"{self.since:%Y-%m-%d}（周{WEEKDAY_NAMES[self.since.weekday()]}）"
        return f"{self.since:%Y-%m-%d} 至 {last:%Y-%m-%d}"

    def slug(self) -> str:
        if self.period == "daily":
            return f"{self.since:%Y-%m-%d}"
        if self.period == "weekly":
            iso = self.since.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        if self.period == "monthly":
            return f"{self.since:%Y-%m}"
        return f"{self.since:%Y%m%d}-{(self.until - timedelta(days=1)):%Y%m%d}"

    def to_dict(self) -> dict[str, Any]:
        return {"period": self.period, "label": self.label, "start_date": f"{self.since:%Y-%m-%d}",
                "end_date": f"{(self.until - timedelta(days=1)):%Y-%m-%d}", "range_text": self.range_text(),
                "slug": self.slug()}


def _parse(text: str, tz: tzinfo) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise EngineError("INVALID_ARGS", "日期需为 YYYY-MM-DD", public=True) from exc


def _midnight(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def compute(period: str, tz: tzinfo, *, date_text: str | None = None, start_date: str | None = None,
            end_date: str | None = None, now: datetime | None = None, max_days: int = 93) -> Window:
    if period not in PERIOD_LABELS:
        raise EngineError("INVALID_ARGS", f"未知周期：{period}", public=True)
    if period == "custom" or start_date or end_date:
        if not (start_date and end_date) or date_text:
            raise EngineError("INVALID_ARGS", "自定义范围需要同时提供 start_date 与 end_date，且不能再给 date", public=True)
        a, b = _parse(start_date, tz), _parse(end_date, tz)
        if b < a:
            raise EngineError("INVALID_ARGS", "start_date 不能晚于 end_date", public=True)
        if (b - a).days + 1 > max_days:
            raise EngineError("INVALID_ARGS", f"自定义范围最多 {max_days} 天", public=True)
        return Window("custom", _midnight(a, tz), _midnight(b + timedelta(days=1), tz))
    now = (now or datetime.now(tz)).astimezone(tz)
    explicit = date_text is not None
    anchor = _parse(date_text, tz) if explicit else now.date()
    if period == "daily":
        day = anchor if explicit else anchor - timedelta(days=1)
        return Window("daily", _midnight(day, tz), _midnight(day + timedelta(days=1), tz))
    if period == "weekly":
        start = anchor - timedelta(days=anchor.weekday())
        if not explicit:
            start -= timedelta(days=7)
        return Window("weekly", _midnight(start, tz), _midnight(start + timedelta(days=7), tz))
    first = anchor.replace(day=1)
    if not explicit:
        first = (first - timedelta(days=1)).replace(day=1)
    return Window("monthly", _midnight(first, tz), _midnight(_next_month(first), tz))


def previous(window: Window, tz: tzinfo) -> Window:
    """同周期的上一期（用于补跑）。"""
    day = (window.since - timedelta(days=1)).date()
    return compute(window.period, tz, date_text=f"{day:%Y-%m-%d}")
