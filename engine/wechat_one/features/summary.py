"""summary 功能：群聊/会话日报、周报、月报（统计 + 分块素材 → 语义稿校验 → MD/HTML 报告）。

流程与语义稿结构参考 Andrew-liu/wechat-summary（MIT）：话题写结论、观点区分共识与分歧、只写聊天里真实出现的待办，
每条结论绑定 msg_id 证据。定时触发由 DSH 自带的定时任务完成，本模块只记录“已出到哪一期”，供补跑判断。
"""
from __future__ import annotations

import hashlib
import time
import unicodedata
from typing import Any

from ..core.dispatch import Ctx, Params, action
from ..core.errors import EngineError
from ..shared import evidence as ev
from ..shared import render
from ..shared import window as win
from ..shared.fmt import fmt_ts, truncate
from ..shared.retrieval import Index, open_index
from ..shared.stats import TEXTUAL, mentions_of_me, period_stats

MAX_CONVERSATIONS = 20
PART_MESSAGES = 180          # 结果白名单单个数组上限 200，留余量
PART_CHARS = 18_000
MAX_PARTS = 10
MATERIAL_TEXT = 300
MIN_INFO_CHARS = 3           # 素材过多时先去掉“哈哈”“好的”这类极短消息
LEDGER_KEEP = 90
PERIODS = ("daily", "weekly", "monthly", "custom")
STANCES = {"consensus": "共识", "debate": "分歧", "insight": "洞见", "warning": "风险"}
DISCLAIMER = ("本报告由 AI 基于本地聊天记录生成，统计数字由程序计算；话题与观点的归纳可能有偏差，"
              "引文已逐字核对，结论未经人工核实。仅供个人回顾，请勿外传群成员的发言。")


# ------------------------------------------------------------------ schema
def build_schema(period: str, me_configured: bool, multi: bool) -> ev.SemanticSchema:
    conv_field = {"conversation_id": ("str", False, "多会话报告时填写该条来自哪个会话（conversation_id 或会话名）")} if multi else {}
    multi_day = period in ("weekly", "monthly", "custom")
    return ev.SemanticSchema(
        name="summary",
        top={
            "one_liner": ("str", True, "一句话总结整期，30~60 字，有判断不空泛"),
            "mood": ("str", False, "氛围，2~6 字，如“热烈但务实”"),
            "editor_note": ("str", False, "编者按 1~3 句：本期观察或数据局限"),
        },
        sections=(
            ev.Section("for_me", "与我相关", 0, 8 if me_configured else 0, {
                "title": ("str", True, "需要我知道或处理的事"),
                "action": ("str", False, "建议动作：回复 / 确认 / 阅读 / 知悉"),
                **conv_field,
            }, quotes_min=0, quotes_max=1),
            ev.Section("topics", "主线话题", 1, 8, {
                "title": ("str", True, "结论式标题 12~28 字：发生了什么 / 得出了什么，不要写成“关于 X 的讨论”"),
                "summary": ("str", True, "2~4 句：谁提出、有什么分歧、最后落在哪"),
                "points": ("list[str]", False, "2~5 条要点，每条一句"),
                "people": ("list[str]", False, "主要参与者显示名"),
                **conv_field,
            }, quotes_min=0, quotes_max=3),
            ev.Section("insights", "观点与分歧", 0, 6, {
                "claim": ("str", True, "一句话观点或判断"),
                "stance": ("str", False, "consensus 共识 | debate 分歧 | insight 洞见 | warning 风险"),
                "detail": ("str", False, "补充说明，分歧要写出双方理由"),
                **conv_field,
            }, quotes_min=0, quotes_max=2),
            ev.Section("follow_ups", "值得跟进", 0, 8, {
                "title": ("str", True, "聊天里真实出现的待办、约定或截止时间"),
                "owner": ("str", False, "谁负责 / 谁提出"),
                "due": ("str", False, "时间点（只写聊天里出现过的）"),
                **conv_field,
            }, quotes_min=0, quotes_max=1),
            ev.Section("daily", "每日要点", 1 if multi_day else 0, 31 if multi_day else 0, {
                "date": ("str", True, "YYYY-MM-DD，必须在报告范围内"),
                "headline": ("str", True, "当天一句话"),
                "points": ("list[str]", False, "1~4 条当天要点"),
            }, quotes_min=0, quotes_max=0),
            ev.Section("links", "精选链接", 0, 10, {
                "url": ("str", True, "必须来自 stats.links 中的 url"),
                "note": ("str", True, "一句话说明为什么值得点开"),
            }, quotes_min=0, quotes_max=0),
        ),
        self_only_warning=False,
        extra_rules=(
            "topics 写结论而不是目录；同一件事的多轮讨论合并成一个话题，以最后结论为准",
            "follow_ups 只写聊天里真实出现的事，不要替人发明任务",
            "for_me 只基于 mentions_me 里的消息和明确点名我的内容；没有就留空" if me_configured
            else "未配置“我的身份”（meNames），for_me 不要写",
        ),
    )


INSTRUCTIONS = (
    "material.messages 按时间排列，me=true 的是用户本人。消息分成 parts_total 块：先读完全部分块"
    "（用 wechat_one_summary_material 取第 2 块起），每块记下候选话题与证据 msg_id，全部读完再合并去重。"
    "按 schema 写语义稿：每条都要 evidence；quotes 必须从原文逐字摘录；links 只能从 stats.links 里挑。"
    "像熟悉这些群的编辑说话：具体、克制，不用“热烈讨论”之类空话。写完调用 wechat_one_summary_render(run_id, semantic)；"
    "校验失败按 errors 修正后重试一次，仍失败就用 stats_only=true 出统计版并告诉用户。"
)


# ------------------------------------------------------------------ 选择会话
def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def _selection(ctx: Ctx, params: Params, index: Index) -> tuple[list[Any], str]:
    group_set = params.str("group_set", max_len=40)
    chats = params.str_list("chats", max_items=MAX_CONVERSATIONS, max_len=80)
    conv_ids = params.str_list("conversation_ids", max_items=MAX_CONVERSATIONS, max_len=30)
    queries: list[str] = []
    label = ""
    if group_set:
        sets = ctx.settings.group_sets
        if group_set not in sets:
            raise EngineError("GROUP_SET_UNKNOWN", f"未定义的群集合：{group_set}", public=True,
                              details={"available": sorted(sets)})
        queries.extend(_norm(x) for x in sets[group_set])
        label = group_set
    queries.extend(_norm(x) for x in chats)
    queries.extend(conv_ids)
    if not queries:
        raise EngineError("NO_SELECTION", public=True, details={"group_sets": sorted(ctx.settings.group_sets)})
    found, problems = index.resolve_conversations(queries)
    if problems:
        raise EngineError("CONVERSATION_UNRESOLVED", public=True, details={"problems": problems})
    if len(found) > MAX_CONVERSATIONS:
        raise EngineError("INVALID_ARGS", f"一次最多 {MAX_CONVERSATIONS} 个会话", public=True)
    if not label:
        label = found[0]["name"] if len(found) == 1 else f"{found[0]['name']}等{len(found)}个会话"
    return found, label


def _selection_key(period: str, conv_ids: list[str]) -> str:
    raw = period + "|" + ",".join(sorted(conv_ids))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _window(params: Params, tz) -> win.Window:
    period = params.str("period", max_len=10, default="daily")
    if period not in PERIODS:
        raise EngineError("INVALID_ARGS", "period 取值无效", public=True)
    return win.compute(period, tz, date_text=params.str("date", max_len=10),
                       start_date=params.str("start_date", max_len=10), end_date=params.str("end_date", max_len=10))


# ------------------------------------------------------------------ 台账（补跑用）
def _ledger_path(ctx: Ctx):
    return ctx.state.root / "summary_ledger.json"


def _ledger(ctx: Ctx) -> dict[str, Any]:
    data = ctx.state.read_json(_ledger_path(ctx), {})
    return data if isinstance(data, dict) else {}


def _mark_done(ctx: Ctx, key: str, label: str, period: str, slug: str, report: str) -> None:
    ledger = _ledger(ctx)
    entry = ledger.get(key) if isinstance(ledger.get(key), dict) else {}
    done = [s for s in entry.get("done", []) if isinstance(s, str) and s != slug] + [slug]
    ledger[key] = {"label": label, "period": period, "done": sorted(done)[-LEDGER_KEEP:],
                   "last_report": report, "updated_at": int(time.time())}
    ctx.state.write_json(_ledger_path(ctx), ledger)


@action("summary_pending", kind="read", timeout_ms=20_000)
def summary_pending(ctx: Ctx, params: Params) -> dict[str, Any]:
    """列出该会话组合还没出的报告期（最多 max_catchup 期，从未出过时只给最近一期）。"""
    tz = ctx.settings.tz()
    period = params.str("period", max_len=10, default="daily")
    if period not in ("daily", "weekly", "monthly"):
        raise EngineError("INVALID_ARGS", "补跑只支持 daily / weekly / monthly", public=True)
    max_catchup = params.int("max_catchup", default=3, lo=1, hi=7)
    index = open_index(ctx.state, ctx.settings)
    try:
        convs, label = _selection(ctx, params, index)
        coverage = index.coverage()
        last_ts = index.last_ts()
    finally:
        index.con.close()
    key = _selection_key(period, [c["id"] for c in convs])
    entry = _ledger(ctx).get(key) or {}
    done = set(entry.get("done") or [])
    latest = win.compute(period, tz)
    candidates = [latest]
    if done:
        while len(candidates) < max_catchup:
            prev = win.previous(candidates[-1], tz)
            if prev.slug() in done:
                break
            candidates.append(prev)
    pending = [w.to_dict() for w in reversed(candidates) if w.slug() not in done]
    note = None
    if last_ts is None or (pending and last_ts < latest.end_ts - 3600):
        note = "本地索引的最新消息早于报告期结束时间，生成前先调用 wechat_one_refresh 刷新数据。"
    return {"label": label, "conversations": [{"conversation_id": c["id"], "name": c["name"]} for c in convs],
            "pending": pending, "recent_done": sorted(done)[-5:], "coverage": coverage, "note": note}


# ------------------------------------------------------------------ 素材
def _split_parts(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parts, current, chars = [], [], 0
    for item in items:
        size = len(item["text"]) + 40
        if current and (len(current) >= PART_MESSAGES or chars + size > PART_CHARS):
            parts.append(current)
            current, chars = [], 0
        current.append(item)
        chars += size
    if current:
        parts.append(current)
    return parts


def _build_material(rows: list[Any], multi: bool, conv_names: dict[str, str], tz, period: str,
                    mentions: set[str]) -> tuple[list[list[dict[str, Any]]], dict[str, dict[str, Any]], dict[str, Any]]:
    usable = [r for r in rows if r["type"] in TEXTUAL and (r["text"] or "").strip()]
    reduction: dict[str, Any] = {"dropped_short": 0, "sampled": False, "text_limit": MATERIAL_TEXT}
    limit = MATERIAL_TEXT

    def compact(r) -> dict[str, Any]:
        stamp = fmt_ts(r["ts"], tz) or ""
        item = {"id": r["id"], "t": stamp[11:] if period == "daily" else stamp[5:], "from": r["sender"],
                "text": truncate(r["text"] or "", limit)[0]}
        if r["is_self"]:
            item["me"] = True
        if multi:
            item["chat"] = conv_names.get(r["conv_id"], "")
        if r["url"]:
            item["url"] = r["url"]
        if r["id"] in mentions:
            item["mentions_me"] = True
        return item

    items = [compact(r) for r in usable]
    parts = _split_parts(items)
    if len(parts) > MAX_PARTS:
        keep = [r for r in usable if len(_norm(r["text"])) >= MIN_INFO_CHARS or r["id"] in mentions or r["url"]]
        reduction["dropped_short"] = len(usable) - len(keep)
        usable = keep
        limit = 150
        reduction["text_limit"] = limit
        items = [compact(r) for r in usable]
        parts = _split_parts(items)
    if len(parts) > MAX_PARTS:
        budget = MAX_PARTS * PART_MESSAGES
        step = len(items) / budget
        must = {i for i, it in enumerate(items) if it.get("mentions_me") or it.get("url")}
        picked = sorted({int(k * step) for k in range(budget)} | must)
        items = [items[i] for i in picked]
        usable = [usable[i] for i in picked]
        reduction["sampled"] = True
        parts = _split_parts(items)[:MAX_PARTS]
    kept_ids = {it["id"] for part in parts for it in part}
    evidence_map = {}
    for r in usable:
        if r["id"] not in kept_ids:
            continue
        evidence_map[r["id"]] = {"text": truncate(r["text"] or "", 2000)[0], "time": fmt_ts(r["ts"], tz),
                                 "date": (fmt_ts(r["ts"], tz) or "")[:10], "sender": r["sender"],
                                 "role": "me" if r["is_self"] else "them",
                                 "chat": conv_names.get(r["conv_id"], ""), "conversation_id": r["conv_id"]}
    return parts, evidence_map, reduction


@action("summary_prepare", kind="read", timeout_ms=60_000)
def summary_prepare(ctx: Ctx, params: Params) -> dict[str, Any]:
    tz = ctx.settings.tz()
    window = _window(params, tz)
    index = open_index(ctx.state, ctx.settings)
    try:
        convs, label = _selection(ctx, params, index)
        rows: list[Any] = []
        for c in convs:
            ctx.check_deadline()
            rows.extend(index.iter_messages(c["id"], window.start_ts, window.end_ts))
        last_ts = index.last_ts()
    finally:
        index.con.close()
    rows.sort(key=lambda r: (r["ts"], r["rowid"]))
    conv_names = {c["id"]: c["name"] for c in convs}
    multi = len(convs) > 1
    stats = period_stats(rows, tz, window.days, conv_names)
    if stats["empty"]:
        raise EngineError("NO_MESSAGES", f"{label} 在 {window.range_text()} 没有可总结的消息。", public=True)
    me_configured = bool(ctx.settings.me_names)
    mentions = set(mentions_of_me(rows, ctx.settings.me_names)) if me_configured else set()
    parts, evidence_map, reduction = _build_material(rows, multi, conv_names, tz, window.period, mentions)
    warnings: list[str] = []
    stale = last_ts is None or last_ts < window.end_ts - 3600
    unfinished = window.end_ts > time.time()
    if stale:
        latest = fmt_ts(last_ts, tz) or "未知"
        warnings.append(f"本地索引最新消息时间为 {latest}，早于报告结束时间，内容可能不完整；可先 wechat_one_refresh 再重出。")
    if unfinished:
        warnings.append("报告期尚未结束，内容只到当前时间。")
    if not me_configured:
        warnings.append("未配置“我的身份”（插件配置 meNames 或 ~/.wechat-report/config.yaml 的 me.names），不生成“与我相关”。")
    if reduction["dropped_short"]:
        warnings.append(f"消息较多，已省略 {reduction['dropped_short']} 条三字以内的短消息。")
    if reduction["sampled"]:
        warnings.append("消息量超过素材上限，已均匀抽样；结论可能遗漏细节。")
    schema = build_schema(window.period, me_configured, multi)
    run_id = ev.new_run_id("summary")
    meta = {"label": label, "window": window.to_dict(), "multi": multi, "me_configured": me_configured,
            "conversations": [{"conversation_id": c["id"], "name": c["name"]} for c in convs],
            "selection_key": _selection_key(window.period, [c["id"] for c in convs]), "warnings": warnings,
            "complete": not stale and not unfinished and window.period != "custom"}
    ev.save_material(ctx.state, run_id, {
        "kind": "summary", "meta": meta, "stats": stats, "evidence": evidence_map, "parts": parts,
        "mentions_me": sorted(mentions & set(evidence_map)), "created_at": int(time.time()),
    })
    return {"run_id": run_id, **{k: meta[k] for k in ("label", "window", "conversations", "warnings")},
            "stats": _stats_for_model(stats), "mentions_me": sorted(mentions & set(evidence_map)),
            "material": {"part": 1, "parts_total": len(parts), "messages": parts[0],
                         "count": sum(len(p) for p in parts)},
            "schema": schema.describe(), "instructions": INSTRUCTIONS}


def _stats_for_model(stats: dict[str, Any]) -> dict[str, Any]:
    return {k: stats[k] for k in ("message_count", "speech_count", "sender_count", "top_senders", "peak_hour",
                                  "daily", "conversations", "link_count")} | {
        "links": [{"url": l["url"], "title": l["title"], "sharers": l["sharers"], "msg_id": l["first_msg_id"]}
                  for l in stats["links"][:30]]}


@action("summary_material", kind="read", timeout_ms=20_000)
def summary_material(ctx: Ctx, params: Params) -> dict[str, Any]:
    run_id = params.str("run_id", required=True, max_len=80)
    part = params.int("part", default=2, lo=1, hi=MAX_PARTS)
    material = ev.load_material(ctx.state, run_id)
    if material.get("kind") != "summary":
        raise EngineError("RUN_NOT_FOUND")
    parts = material.get("parts") or []
    if part > len(parts):
        raise EngineError("PART_NOT_FOUND", f"只有 {len(parts)} 块素材", public=True)
    return {"run_id": run_id, "part": part, "parts_total": len(parts), "messages": parts[part - 1]}


# ------------------------------------------------------------------ 渲染
def _item_checker(material: dict[str, Any]):
    meta, evidence = material["meta"], material["evidence"]
    by_id = {c["conversation_id"]: c["name"] for c in meta["conversations"]}
    by_name = {_norm(c["name"]): c["conversation_id"] for c in meta["conversations"]}
    link_urls = {l["url"] for l in material["stats"]["links"]}
    days = {d["date"] for d in material["stats"]["daily"]}

    def check(section: str, where: str, item: dict[str, Any], ids: list[str]) -> list[str]:
        errors = []
        cid = item.get("conversation_id")
        if cid:
            cid = cid if cid in by_id else by_name.get(_norm(cid))
            if not cid:
                errors.append(f"{where}.conversation_id 不属于本报告的会话：{item.get('conversation_id')}")
            else:
                item["conversation_id"] = cid
                foreign = [x for x in ids if evidence[x].get("conversation_id") != cid]
                if foreign:
                    errors.append(f"{where} 的证据不全来自 {by_id[cid]}：{', '.join(foreign[:3])}")
        if section == "insights" and item.get("stance") and item["stance"] not in STANCES:
            errors.append(f"{where}.stance 只能是 {'/'.join(STANCES)}")
        if section == "links" and item.get("url") not in link_urls:
            errors.append(f"{where}.url 不在 stats.links 中")
        if section == "daily" and item.get("date") not in days:
            errors.append(f"{where}.date 不在报告范围内：{item.get('date')}")
        return errors

    return check


def _infer_conversation(item: dict[str, Any], evidence: dict[str, Any], names: dict[str, str]) -> str:
    cid = item.get("conversation_id")
    if not cid:
        counts: dict[str, int] = {}
        for x in item.get("evidence", []):
            c = evidence.get(x, {}).get("conversation_id")
            if c:
                counts[c] = counts.get(c, 0) + 1
        cid = max(counts, key=counts.get) if counts else None
    return names.get(cid, "") if cid else ""


def _decorate(sections: list[dict[str, Any]], material: dict[str, Any]) -> list[dict[str, Any]]:
    meta, evidence = material["meta"], material["evidence"]
    names = {c["conversation_id"]: c["name"] for c in meta["conversations"]}
    links = {l["url"]: l for l in material["stats"]["links"]}
    for sec in sections:
        for item in sec["items"]:
            item["chat"] = _infer_conversation(item, evidence, names) if meta["multi"] else ""
            if sec["key"] == "insights":
                item["stance_label"] = STANCES.get(item.get("stance") or "", "")
            if sec["key"] == "links":
                src = links.get(item.get("url"), {})
                item["title"] = src.get("title") or src.get("domain") or item.get("url")
                item["sharers"] = "、".join(src.get("sharers", []))
            first = evidence.get((item.get("evidence") or [""])[0], {})
            item["when"] = first.get("time") or ""
        if sec["key"] == "daily":
            sec["items"].sort(key=lambda x: x.get("date", ""))
    return sections


def _auto_links(material: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"url": l["url"], "title": l["title"] or l["domain"] or l["url"], "note": "",
             "sharers": "、".join(l["sharers"]), "chat": l["conversation"] if material["meta"]["multi"] else "",
             "quotes": [], "evidence": [l["first_msg_id"]], "evidence_count": l["share_count"]}
            for l in material["stats"]["links"][:10]]


def _cards(stats: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, str]]:
    peak = stats.get("peak_hour")
    busiest = max(stats["daily"], key=lambda d: d["messages"]) if len(stats["daily"]) > 1 else None
    cards = [
        {"label": "发言", "value": f"{stats['speech_count']:,}", "note": f"共 {stats['message_count']:,} 条消息"},
        {"label": "发言人", "value": str(stats["sender_count"]), "note": ""},
        {"label": "最活跃时段", "value": "—" if peak is None else f"{peak:02d}:00", "note": ""},
        {"label": "分享链接", "value": str(stats["link_count"]), "note": ""},
    ]
    if busiest and busiest["messages"]:
        cards.append({"label": "最热的一天", "value": busiest["date"][5:], "note": f"{busiest['messages']} 条发言"})
    if meta["multi"]:
        cards.append({"label": "会话数", "value": str(len(meta["conversations"])), "note": ""})
    return cards


def _markdown(title: str, meta: dict[str, Any], semantic: dict[str, Any], sections: list[dict[str, Any]],
              cards: list[dict[str, str]], stats: dict[str, Any], stats_only: bool) -> str:
    lines = [f"# {title}", "", f"范围：{meta['window']['range_text']}"]
    if semantic.get("one_liner"):
        lines += ["", f"> {semantic['one_liner']}" + (f"（{semantic['mood']}）" if semantic.get("mood") else "")]
    if semantic.get("editor_note"):
        lines += ["", semantic["editor_note"]]
    for w in meta.get("warnings", []):
        lines += [f"", f"注意：{w}"]
    lines += ["", "## 数据", ""] + [f"- {c['label']}：{c['value']}" + (f"（{c['note']}）" if c["note"] else "") for c in cards]
    if stats["top_senders"]:
        lines.append("- 发言最多：" + "、".join(f"{s['name']} {s['messages']}" for s in stats["top_senders"][:8]))
    for sec in sections:
        if not sec["items"]:
            continue
        lines += ["", f"## {sec['label']}", ""]
        for item in sec["items"]:
            head = item.get("title") or item.get("claim") or item.get("headline") or item.get("url")
            if sec["key"] == "daily":
                head = f"{item['date']} {item['headline']}"
            if item.get("stance_label"):
                head = f"［{item['stance_label']}］{head}"
            chat = f"〔{item['chat']}〕" if item.get("chat") else ""
            if sec["key"] == "links":
                lines.append(f"- [{item['title']}]({item['url']}) {chat}{item.get('note') or ''}（{item.get('sharers', '')} 分享）")
                continue
            lines.append(f"### {chat}{head}" if sec["key"] in ("topics",) else f"- {chat}{head}")
            body = item.get("summary") or item.get("detail") or ""
            extra = [x for x in (item.get("action"), item.get("owner"), item.get("due")) if x]
            if body:
                lines.append(("" if sec["key"] == "topics" else "  ") + body)
            if extra:
                lines.append("  " + " · ".join(extra))
            for p in item.get("points") or []:
                lines.append(f"  - {p}")
            for q in item.get("quotes") or []:
                lines.append(f"  > {q['text']} —— {q['sender']} · {q['time']} · `{q['msg_id']}`")
            if sec["key"] == "topics":
                lines.append("")
    lines += ["", "---", "", DISCLAIMER, "", "semantic_truth: " + ("stats-only" if stats_only else "not-verified")]
    return "\n".join(lines) + "\n"


@action("summary_render", kind="write", timeout_ms=60_000)
def summary_render(ctx: Ctx, params: Params) -> dict[str, Any]:
    run_id = params.str("run_id", required=True, max_len=80)
    theme = params.str("theme", max_len=20, default="claude")
    if theme not in render.themes():
        raise EngineError("THEME_NOT_FOUND")
    stats_only = params.bool("stats_only", False)
    material = ev.load_material(ctx.state, run_id)
    if material.get("kind") != "summary":
        raise EngineError("RUN_NOT_FOUND")
    meta, stats = material["meta"], material["stats"]
    schema = build_schema(meta["window"]["period"], meta["me_configured"], meta["multi"])
    warnings: list[str] = []
    cleaned: dict[str, Any] = {}
    sections: list[dict[str, Any]] = []
    if not stats_only:
        semantic = params.obj("semantic")
        if semantic is None:
            raise EngineError("INVALID_ARGS", "缺少 semantic；如需只出统计版请传 stats_only=true", public=True)
        errors, warnings, cleaned = ev.validate(semantic, schema, material["evidence"], item_check=_item_checker(material))
        if errors:
            raise EngineError("SEMANTIC_INVALID", "语义稿未通过证据校验，请按 errors 逐条修正后重试。",
                              details={"errors": errors, "warnings": warnings}, public=True)
        sections = _decorate(ev.compose(schema, cleaned, material["evidence"]), material)
    if not any(s["key"] == "links" and s["items"] for s in sections):
        sections = [s for s in sections if s["key"] != "links"]
        if stats["links"]:
            sections.append({"key": "links", "label": "链接清单" if stats_only else "精选链接", "items": _auto_links(material)})
    label = meta["label"]
    window = meta["window"]
    title = f"{label} · {window['label']}"
    palette = render.theme_info(theme).get("palette", {})
    daily = stats["daily"]
    charts = {
        "hourly": render.bar_chart(stats["hourly"], [f"{h}" for h in range(24)], palette,
                                   highlight=stats.get("peak_hour"), label_every=3),
        "daily": render.bar_chart([d["messages"] for d in daily], [d["date"][5:] for d in daily], palette,
                                  label_every=max(1, len(daily) // 10)) if len(daily) > 1 else "",
    }
    cards = _cards(stats, meta)
    senders = [{"name": s["name"], "messages": s["messages"], "share": f"{s['share'] * 100:.0f}%",
                "width": max(2, round(s["share"] / max(stats["top_senders"][0]["share"], 0.001) * 100))}
               for s in stats["top_senders"][:10]] if stats["top_senders"] else []
    context = {
        "title": title, "label": label, "kind": window["label"], "range": window["range_text"],
        "generated_at": time.strftime("%Y-%m-%d %H:%M"), "one_liner": cleaned.get("one_liner"),
        "mood": cleaned.get("mood"), "editor_note": cleaned.get("editor_note"), "warnings": meta.get("warnings", []),
        "cards": cards, "charts": charts, "senders": senders, "sections": sections, "stats_only": stats_only,
        "conversations": stats["conversations"] if meta["multi"] else [], "disclaimer": DISCLAIMER,
        "truth": "stats-only · 统计版，未生成话题归纳" if stats_only
        else "not-verified · 证据已校验（msg_id 与逐字引文），归纳与观点未经人工核实",
    }
    html_text = render.render_html("summary.html", context, theme)
    markdown = _markdown(title, meta, cleaned, sections, cards, stats, stats_only)
    directory = (ctx.state.reports_dir / "summary" / render.safe_slug(label, 40)
                 / f"{window['period']}-{window['slug']}")
    paths = render.write_report(directory, markdown, html_text, theme)
    recorded = bool(meta.get("complete"))
    if recorded:
        _mark_done(ctx, meta["selection_key"], label, window["period"], window["slug"], paths["report_md"])
    return {**paths, "theme": theme, "stats_only": stats_only, "warnings": warnings,
            "label": label, "window": window, "recorded": recorded,
            "sections": [{"key": s["key"], "label": s["label"], "count": len(s["items"])} for s in sections]}
