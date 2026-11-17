"""persona 功能：好友证据化人格报告（统计 + 分层采样素材 → 语义稿校验 → MD/HTML 报告）。"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any

from ..core.dispatch import Ctx, Params, action
from ..core.errors import EngineError
from ..shared import evidence as ev
from ..shared import render
from ..shared.fmt import day_range, fmt_ts, truncate
from ..shared.retrieval import open_index
from ..shared.stats import TEXTUAL, chat_stats

CONV_RE = re.compile(r"^conv_[0-9a-f]{16}$")
MIN_TEXT_MESSAGES = 20
SAMPLE_TARGETS = (400, 300, 200, 120)
CHAR_BUDGET = 24_000
MAX_MATERIAL = 200           # JS 结果白名单单个数组最多 200 项，超出会被静默截断
MATERIAL_TEXT = 200
DISCLAIMER = ("本报告由 AI 基于本地聊天记录的抽样片段生成，只反映这些聊天中的表达印象，不是心理测评或诊断，"
              "也不代表对方的真实人格或意愿。仅供个人回顾，请勿公开传播或用于评判他人。")

_ITEM = {
    "title": ("str", True, "一句话结论"),
    "detail": ("str", True, "具体说明，基于素材，不夸大"),
    "evidence": ("list[str]", True, "支撑该结论的 msg_id 列表（至少 1 个）"),
    "quotes": ("list[quote]", True, "1~3 条逐字原话 {msg_id, text}"),
}
SCHEMA = ev.SemanticSchema(
    name="persona",
    top={
        "one_liner": ("str", True, "一句话画像（不超过 40 字，避免诊断性标签）"),
        "summary": ("str", True, "整体印象 100~300 字"),
        "caveats": ("list[str]", False, "局限与不确定之处，例如素材时间跨度、话题偏向"),
    },
    sections=(
        ev.Section("communication_style", "沟通风格", 1, 4, _ITEM),
        ev.Section("interests", "兴趣与常聊话题", 1, 6, _ITEM),
        ev.Section("emotional_expression", "情绪表达", 1, 4, _ITEM),
        ev.Section("interaction_pattern", "与我的互动模式", 1, 4, _ITEM),
        ev.Section("values", "态度与价值观倾向", 0, 4, _ITEM),
        ev.Section("catchphrases", "口头禅", 0, 8, {
            "phrase": ("str", True, "口头禅原文"),
            "detail": ("str", True, "使用场景与语气"),
            "evidence": ("list[str]", True, "msg_id 列表"),
            "quotes": ("list[quote]", True, "1~3 条逐字原话"),
        }),
    ),
)

INSTRUCTIONS = (
    "material.messages 中 role=对方 的是好友发言，role=我 的是用户本人。请结合 stats（本地精确统计）与素材，"
    "按 schema 写语义稿：每个条目必须引用本素材中的 msg_id，quotes 从原文逐字摘录；"
    "结论要描述可观察的表达习惯与互动方式，不要做心理诊断、不要给负面人格标签；素材不足的维度宁可少写。"
    "写完调用 wechat_one_persona_render(run_id, semantic)。"
)


def _sample(rows: list[Any], tz) -> tuple[list[int], bool]:
    """按月分层均匀采样对方发言，附前后文，控制总字数。返回 (行下标列表, 是否截断)。"""
    candidates = [i for i, r in enumerate(rows)
                  if not r["is_self"] and r["type"] in TEXTUAL and len((r["text"] or "").strip()) >= 2]
    by_month: dict[str, list[int]] = defaultdict(list)
    for i in candidates:
        by_month[fmt_ts(rows[i]["ts"], tz)[:7]].append(i)
    for width in (2, 1, 0):
        for target in SAMPLE_TARGETS:
            picked: list[int] = []
            if len(candidates) <= target:
                picked = candidates
            else:
                for idxs in by_month.values():
                    quota = max(3, round(target * len(idxs) / len(candidates)))
                    if quota >= len(idxs):
                        picked.extend(idxs)
                    else:
                        step = len(idxs) / quota
                        picked.extend(idxs[int(k * step)] for k in range(quota))
            chosen = sorted({j for i in picked for j in range(max(0, i - width), min(len(rows), i + width + 1))})
            chars = sum(min(len(rows[j]["text"] or ""), MATERIAL_TEXT) + 30 for j in chosen)
            if chars <= CHAR_BUDGET and len(chosen) <= MAX_MATERIAL:
                return chosen, len(picked) < len(candidates)
    return chosen[: min(MAX_MATERIAL, CHAR_BUDGET // 120)], True


@action("persona_prepare", kind="read", timeout_ms=60_000)
def persona_prepare(ctx: Ctx, params: Params) -> dict[str, Any]:
    conv_id = params.str("conversation_id", required=True, max_len=30)
    if not CONV_RE.match(conv_id):
        raise EngineError("INVALID_ARGS", "conversation_id 格式无效", public=True)
    tz = ctx.settings.tz()
    start, end = day_range(params.str("start_date", max_len=10), params.str("end_date", max_len=10), tz)
    index = open_index(ctx.state, ctx.settings)
    try:
        conv = index.conversation(conv_id)
        if conv["kind"] != "friend":
            raise EngineError("NOT_PRIVATE_CHAT")
        rows = list(index.iter_messages(conv_id, start, end))
    finally:
        index.con.close()
    text_count = sum(1 for r in rows if not r["is_self"] and r["type"] in TEXTUAL and (r["text"] or "").strip())
    if text_count < MIN_TEXT_MESSAGES:
        raise EngineError("INSUFFICIENT_DATA", f"该时间范围内对方只有 {text_count} 条文字消息（至少需要 "
                                               f"{MIN_TEXT_MESSAGES} 条），无法生成可靠报告。", public=True)
    stats = chat_stats(rows, tz)
    chosen, truncated = _sample(rows, tz)
    evidence_map: dict[str, dict[str, Any]] = {}
    material = []
    for j in chosen:
        r = rows[j]
        role = "me" if r["is_self"] else "them"
        full, _ = truncate(r["text"] or "", 2000)
        shown, _ = truncate(r["text"] or "", MATERIAL_TEXT)
        when = fmt_ts(r["ts"], tz)
        evidence_map[r["id"]] = {"text": full, "time": when, "sender": r["sender"], "role": role}
        material.append({"id": r["id"], "time": when, "role": "我" if role == "me" else "对方",
                         "sender": r["sender"], "text": shown})
    run_id = ev.new_run_id("persona")
    period = {"start": stats["first_date"], "end": stats["last_date"]}
    ev.save_material(ctx.state, run_id, {
        "kind": "persona", "conversation": {"id": conv_id, "name": conv["name"]}, "period": period,
        "stats": stats, "evidence": evidence_map, "created_at": int(time.time()),
    })
    return {"run_id": run_id, "conversation": conv["name"], "period": period, "stats": stats,
            "material": {"messages": material, "count": len(material), "truncated": truncated},
            "schema": SCHEMA.describe(), "instructions": INSTRUCTIONS}


def _cards(stats: dict[str, Any]) -> list[dict[str, str]]:
    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x * 100:.0f}%"

    reply = stats["reply_minutes"].get("them_median")
    return [
        {"label": "消息总数", "value": f"{stats['total']:,}", "note": f"对方 {stats['them']:,} · 我 {stats['me']:,}"},
        {"label": "相识跨度", "value": f"{stats['span_days']} 天", "note": f"其中 {stats['active_days']} 天有聊天"},
        {"label": "对方主动开口", "value": pct(stats["initiative"]["them_ratio"]),
         "note": f"{stats['initiative']['them']} / {stats['initiative']['them'] + stats['initiative']['me']} 段对话"},
        {"label": "对方回复中位时延", "value": "—" if reply is None else (f"{reply:.0f} 分钟" if reply < 120 else f"{reply / 60:.1f} 小时"),
         "note": ""},
        {"label": "平均消息长度", "value": f"{stats['length']['them_avg']:.0f} 字", "note": f"长消息占 {pct(stats['length']['them_long_ratio'])}"},
        {"label": "深夜发言", "value": pct(stats["night_ratio"]), "note": "23:00–05:00"},
    ]


def _habits(stats: dict[str, Any]) -> str:
    parts = []
    if stats.get("particles_per100"):
        parts.append("常用语气：" + "、".join(f"「{p['word']}」每百条 {p['per100']} 次" for p in stats["particles_per100"][:4]))
    if stats.get("wx_emoji_top"):
        parts.append("常用表情：" + " ".join(e["emoji"] for e in stats["wx_emoji_top"][:5]))
    if stats.get("sentiment", {}).get("overall") is not None:
        parts.append(f"词典情感倾向 {stats['sentiment']['overall']:.2f}（0.5 为中性）")
    return "；".join(parts)


def _markdown(name: str, period: dict[str, str], semantic: dict[str, Any], sections: list[dict[str, Any]],
              cards: list[dict[str, str]], habits: str) -> str:
    lines = [f"# {name} · 好友人格画像", "", f"> {semantic.get('one_liner', '')}", "",
             f"时间范围：{period['start']} ~ {period['end']}", "", "## 总览", "", semantic.get("summary", ""), "",
             "## 数据概览", ""]
    lines += [f"- {c['label']}：{c['value']}" + (f"（{c['note']}）" if c["note"] else "") for c in cards]
    if habits:
        lines += ["", habits]
    for sec in sections:
        if not sec["items"]:
            continue
        lines += ["", f"## {sec['label']}", ""]
        for item in sec["items"]:
            lines += [f"### {item.get('title') or '「' + item.get('phrase', '') + '」'}", "", item.get("detail", ""), ""]
            for q in item["quotes"]:
                lines.append(f"> {q['text']} —— {q['sender']} · {q['time']} · `{q['msg_id']}`")
            lines.append("")
    if semantic.get("caveats"):
        lines += ["## 局限与不确定", ""] + [f"- {c}" for c in semantic["caveats"]]
    lines += ["", "---", "", DISCLAIMER, "", "semantic_truth: not-verified"]
    return "\n".join(lines) + "\n"


@action("persona_render", kind="write", timeout_ms=60_000)
def persona_render(ctx: Ctx, params: Params) -> dict[str, Any]:
    run_id = params.str("run_id", required=True, max_len=80)
    theme = params.str("theme", max_len=20, default="newspaper")
    if theme not in render.themes():
        raise EngineError("THEME_NOT_FOUND")
    material = ev.load_material(ctx.state, run_id)
    if material.get("kind") != "persona":
        raise EngineError("RUN_NOT_FOUND")
    semantic = params.obj("semantic", required=True)
    errors, warnings, cleaned = ev.validate(semantic, SCHEMA, material["evidence"])
    if errors:
        raise EngineError("SEMANTIC_INVALID", "语义稿未通过证据校验，请按 errors 逐条修正后重试。",
                          details={"errors": errors, "warnings": warnings}, public=True)
    sections = ev.compose(SCHEMA, cleaned, material["evidence"])
    stats = material["stats"]
    name = material["conversation"]["name"]
    period = material["period"]
    palette = render.theme_info(theme).get("palette", {})
    hourly = stats["hourly_them"]
    months = stats["monthly"][-36:]
    charts = {
        "hourly": render.bar_chart(hourly, [f"{h}" for h in range(24)], palette,
                                   highlight=stats.get("peak_hour"), label_every=3),
        "monthly": render.bar_chart([m["them"] for m in months], [m["month"][2:] for m in months], palette,
                                    label_every=max(1, len(months) // 8)) if len(months) > 1 else "",
    }
    cards = _cards(stats)
    habits = _habits(stats)
    context = {
        "title": f"{name} · 好友人格画像", "name": name, "period": f"{period['start']} ~ {period['end']}",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"), "one_liner": cleaned.get("one_liner"),
        "summary": cleaned.get("summary"), "caveats": cleaned.get("caveats") or [], "stats": stats,
        "cards": cards, "charts": charts, "habits": habits, "sections": sections, "disclaimer": DISCLAIMER,
    }
    html_text = render.render_html("persona.html", context, theme)
    markdown = _markdown(name, period, cleaned, sections, cards, habits)
    directory = ctx.state.reports_dir / f"persona-{render.safe_slug(name)}-{time.strftime('%Y%m%d-%H%M%S')}"
    paths = render.write_report(directory, markdown, html_text, theme)
    return {**paths, "theme": theme, "warnings": warnings,
            "sections": [{"key": s["key"], "label": s["label"], "count": len(s["items"])} for s in sections]}
