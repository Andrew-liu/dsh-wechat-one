"""聊天统计（本地计算，不调用模型）。persona 与后续 summary 共用。

输入为按时间升序的消息行（需含 ts / is_self / type / text 字段）。
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

TEXTUAL = frozenset({"text", "quote", "link", "file", "forward", "miniprogram", "channels", "location", "card"})
SESSION_GAP = 6 * 3600
REPLY_WINDOW = 24 * 3600
PARTICLES = ("哈哈", "呢", "吧", "啊", "嗯", "哦", "呀", "嘛", "啦", "好的", "收到", "～", "~", "！", "？", "...", "。。")
WX_EMOJI_RE = re.compile(r"\[[\u4e00-\u9fffA-Za-z]{1,6}\]")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
STOP_GRAMS = frozenset("""
我们 你们 他们 她们 这个 那个 什么 怎么 没有 就是 还是 可以 一个 不是 知道 现在 时候 然后 因为 所以 但是 如果 已经 自己 这样 那样
觉得 一下 一点 东西 还有 而且 不过 这么 那么 为什么 的话 一起 今天 明天 昨天 晚上 早上 是不是 有没有 好像 应该 可能 感觉 真的
""".split())
POSITIVE = frozenset("开心 高兴 喜欢 哈哈 好棒 太好 厉害 感谢 谢谢 期待 舒服 漂亮 可爱 不错 优秀 爱你 加油 幸福 满意 有趣 好玩 赞 牛".split())
NEGATIVE = frozenset("难过 伤心 生气 烦 累 讨厌 无语 崩溃 郁闷 焦虑 害怕 失望 后悔 痛苦 委屈 哭 糟糕 麻烦 头疼 压力 难受 担心 烦死".split())
NEGATORS = ("不", "没", "别", "无")


def _dt(ts: int, tz) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)


def sentiment_score(text: str) -> float | None:
    """词典打分，0~1，0.5 为中性；无情感词返回 None。考虑紧邻否定词。"""
    pos = neg = 0
    for words, sign in ((POSITIVE, 1), (NEGATIVE, -1)):
        for w in words:
            start = 0
            while True:
                i = text.find(w, start)
                if i < 0:
                    break
                negated = i > 0 and text[i - 1] in NEGATORS
                if (sign > 0) != negated:
                    pos += 1
                else:
                    neg += 1
                start = i + len(w)
    total = pos + neg
    return None if total == 0 else pos / total


def ngram_candidates(texts: Iterable[tuple[str, str]], top: int = 15, min_days: int = 3) -> list[dict[str, Any]]:
    """口头禅候选：2~4 字片段，在至少 min_days 个不同日期出现，按出现次数排序并去掉被更长片段覆盖的短片段。"""
    counts: Counter = Counter()
    days: dict[str, set[str]] = defaultdict(set)
    for day, text in texts:
        seen: set[str] = set()
        for run in CJK_RUN_RE.findall(text):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if g in STOP_GRAMS or g in seen:
                        continue
                    seen.add(g)
                    counts[g] += 1
                    days[g].add(day)
    ranked = [(g, c) for g, c in counts.most_common(400) if len(days[g]) >= min_days]
    picked: list[tuple[str, int]] = []
    for g, c in ranked:
        if any(g in p and c <= pc * 1.2 for p, pc in picked):
            continue
        picked = [(p, pc) for p, pc in picked if not (p in g and pc <= c * 1.2)]
        picked.append((g, c))
        if len(picked) >= top:
            break
    return [{"phrase": g, "count": c, "days": len(days[g])} for g, c in picked]


def chat_stats(rows: list[Any], tz) -> dict[str, Any]:
    if not rows:
        return {"total": 0}
    them = [r for r in rows if not r["is_self"]]
    me = [r for r in rows if r["is_self"]]
    them_text = [r for r in them if r["type"] in TEXTUAL and (r["text"] or "").strip()]
    hourly = [0] * 24
    weekday = [0] * 7
    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"them": 0, "me": 0})
    active_days: set[str] = set()
    for r in rows:
        d = _dt(r["ts"], tz)
        monthly[d.strftime("%Y-%m")]["me" if r["is_self"] else "them"] += 1
        active_days.add(d.strftime("%Y-%m-%d"))
        if not r["is_self"]:
            hourly[d.hour] += 1
            weekday[d.weekday()] += 1
    night = sum(hourly[h] for h in (23, 0, 1, 2, 3, 4))

    # 主动开口：相邻消息间隔超过 6 小时视为新一段对话，看谁先说话
    starters = Counter()
    prev_ts = None
    for r in rows:
        if prev_ts is None or r["ts"] - prev_ts > SESSION_GAP:
            starters["me" if r["is_self"] else "them"] += 1
        prev_ts = r["ts"]

    # 回复时延：一方说完后另一方在 24 小时内的第一条回复
    them_delays, me_delays = [], []
    for prev, cur in zip(rows, rows[1:]):
        if prev["is_self"] != cur["is_self"] and 0 <= cur["ts"] - prev["ts"] <= REPLY_WINDOW:
            (me_delays if cur["is_self"] else them_delays).append((cur["ts"] - prev["ts"]) / 60)

    lengths = [len(r["text"]) for r in them_text]
    types = Counter(r["type"] for r in them)
    joined = "\n".join(r["text"] for r in them_text)
    per100 = (lambda n: round(n * 100 / len(them_text), 1)) if them_text else (lambda n: 0)
    particles = sorted(((p, joined.count(p)) for p in PARTICLES), key=lambda x: -x[1])
    wx_emoji = Counter(WX_EMOJI_RE.findall(joined))

    month_sent: dict[str, list[float]] = defaultdict(list)
    for r in them_text:
        s = sentiment_score(r["text"])
        if s is not None:
            month_sent[_dt(r["ts"], tz).strftime("%Y-%m")].append(s)
    all_scores = [s for v in month_sent.values() for s in v]

    first, last = _dt(rows[0]["ts"], tz), _dt(rows[-1]["ts"], tz)
    return {
        "total": len(rows), "them": len(them), "me": len(me), "them_text": len(them_text),
        "first_date": first.strftime("%Y-%m-%d"), "last_date": last.strftime("%Y-%m-%d"),
        "span_days": (last.date() - first.date()).days + 1, "active_days": len(active_days),
        "hourly_them": hourly, "weekday_them": weekday,
        "peak_hour": max(range(24), key=lambda h: hourly[h]) if them else None,
        "night_ratio": round(night / len(them), 3) if them else 0,
        "initiative": {"them": starters["them"], "me": starters["me"],
                       "them_ratio": round(starters["them"] / max(1, sum(starters.values())), 3)},
        "reply_minutes": {
            "them_median": round(statistics.median(them_delays), 1) if them_delays else None,
            "me_median": round(statistics.median(me_delays), 1) if me_delays else None,
        },
        "length": {"them_avg": round(sum(lengths) / len(lengths), 1) if lengths else 0,
                   "them_median": statistics.median(lengths) if lengths else 0,
                   "them_long_ratio": round(sum(1 for x in lengths if x > 50) / len(lengths), 3) if lengths else 0},
        "types_them": dict(types.most_common(8)),
        "particles_per100": [{"word": p, "per100": per100(n)} for p, n in particles if n][:8],
        "wx_emoji_top": [{"emoji": e, "count": n} for e, n in wx_emoji.most_common(6)],
        "emoji_messages_ratio": round(types.get("emoji", 0) / len(them), 3) if them else 0,
        "catchphrase_candidates": ngram_candidates(
            ((_dt(r["ts"], tz).strftime("%Y-%m-%d"), r["text"]) for r in them_text)),
        "monthly": [{"month": m, **v} for m, v in sorted(monthly.items())],
        "sentiment": {
            "overall": round(sum(all_scores) / len(all_scores), 3) if all_scores else None,
            "scored_messages": len(all_scores),
            "monthly": [{"month": m, "score": round(sum(v) / len(v), 3), "n": len(v)} for m, v in sorted(month_sent.items())],
        },
    }


# ------------------------------------------------------------------ 群聊/会话周期统计（summary 用）
NON_SPEECH = frozenset({"system", "revoke", "pat"})
TRACKING_PARAMS = ("utm_", "spm", "from=", "scene=", "share_", "chksm=", "sessionid", "clicktime", "enterid")


def clean_url(url: str) -> str:
    """去掉常见追踪参数。思路来自 Andrew-liu/wechat-summary（MIT）的 stats.py。"""
    from urllib.parse import urlsplit

    url = (url or "").strip().rstrip(".,;:!?)）】」")
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    kept = [p for p in parts.query.split("&") if p and not p.lower().startswith(TRACKING_PARAMS)]
    return parts._replace(query="&".join(kept)).geturl()


def collect_links(rows: list[Any], conv_names: dict[str, str], limit: int = 40) -> list[dict[str, Any]]:
    from urllib.parse import urlsplit

    links: dict[str, dict[str, Any]] = {}
    for r in rows:
        raw = r["url"]
        if not raw or not str(raw).lower().startswith(("http://", "https://")):
            continue
        url = clean_url(str(raw))
        title = (r["text"] or "").strip() if r["type"] == "link" else ""
        entry = links.get(url)
        if entry is None:
            try:
                domain = urlsplit(url).netloc
            except ValueError:
                domain = ""
            links[url] = {"url": url, "title": title[:120], "domain": domain, "first_msg_id": r["id"],
                          "conversation": conv_names.get(r["conv_id"], ""), "sharers": [r["sender"]],
                          "share_count": 1}
        else:
            entry["share_count"] += 1
            if r["sender"] not in entry["sharers"]:
                entry["sharers"].append(r["sender"])
            if title and not entry["title"]:
                entry["title"] = title[:120]
    return sorted(links.values(), key=lambda x: -x["share_count"])[:limit]


def period_stats(rows: list[Any], tz, days: list[Any], conv_names: dict[str, str], top_n: int = 10) -> dict[str, Any]:
    """一个时间窗内的确定性统计：数字全部由程序计算，不交给模型。"""
    speech = [r for r in rows if r["type"] not in NON_SPEECH]
    senders = Counter(r["sender"] for r in speech)
    hourly = [0] * 24
    daily = {d.isoformat(): {"date": d.isoformat(), "weekday": "一二三四五六日"[d.weekday()], "messages": 0, "senders": set()}
             for d in days}
    per_conv: dict[str, dict[str, Any]] = {}
    for r in speech:
        dt = _dt(r["ts"], tz)
        hourly[dt.hour] += 1
        bucket = daily.get(dt.date().isoformat())
        if bucket is not None:
            bucket["messages"] += 1
            bucket["senders"].add(r["sender"])
        c = per_conv.setdefault(r["conv_id"], {"messages": 0, "senders": Counter()})
        c["messages"] += 1
        c["senders"][r["sender"]] += 1
    total = len(speech)
    links = collect_links(rows, conv_names)
    return {
        "message_count": len(rows), "speech_count": total, "sender_count": len(senders),
        "top_senders": [{"name": n, "messages": c, "share": round(c / total, 3) if total else 0}
                        for n, c in senders.most_common(top_n)],
        "hourly": hourly, "peak_hour": max(range(24), key=lambda h: hourly[h]) if total else None,
        "daily": [{**v, "senders": len(v["senders"])} for v in daily.values()],
        "types": dict(Counter(r["type"] for r in rows).most_common(10)),
        "links": links, "link_count": len(links),
        "conversations": sorted(({"conversation_id": cid, "name": conv_names.get(cid, ""), "messages": v["messages"],
                                  "senders": len(v["senders"]),
                                  "top_senders": [n for n, _ in v["senders"].most_common(3)]}
                                 for cid, v in per_conv.items()), key=lambda x: -x["messages"]),
        "empty": total == 0,
    }


def mentions_of_me(rows: list[Any], me_names: list[str]) -> list[str]:
    """@我 或直呼我名字的他人消息 ID（只看文本类消息）。"""
    names = [n for n in me_names if n and len(n) >= 2]
    if not names:
        return []
    hits = []
    for r in rows:
        if r["is_self"] or r["type"] not in TEXTUAL:
            continue
        text = r["text"] or ""
        if any(f"@{n}" in text or n in text for n in names):
            hits.append(r["id"])
    return hits
