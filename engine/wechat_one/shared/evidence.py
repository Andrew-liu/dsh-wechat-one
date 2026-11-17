"""证据流水线：prepare_material → validate_semantic → compose_report。

规则：语义稿的每个条目必须给出 evidence（至少 1 个本次素材里的 msg_id）与逐字 quotes；
引文必须是对应消息原文的连续片段（忽略空白与全半角差异）；不存在或不属于本次素材的 msg_id 一律拒绝。
persona 与后续 summary 共用。思路来自 Andrew-liu/wechat-summary（MIT）的 semantic.py。
"""
from __future__ import annotations

import json
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.errors import EngineError
from ..core.state import State

RUN_RE = re.compile(r"^run_[0-9a-z_]{8,60}$")
MAX_ERRORS = 30


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    min: int
    max: int
    fields: dict[str, tuple[str, bool, str]] = field(default_factory=dict)  # name -> (kind, required, desc)
    quotes_min: int = 1
    quotes_max: int = 3


@dataclass(frozen=True)
class SemanticSchema:
    name: str
    top: dict[str, tuple[str, bool, str]]
    sections: tuple[Section, ...]
    self_only_warning: bool = True
    extra_rules: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "top_fields": {k: {"type": t, "required": r, "desc": d} for k, (t, r, d) in self.top.items()},
            "sections": {s.key: {"label": s.label, "min_items": s.min, "max_items": s.max,
                                 "quotes": f"{s.quotes_min}~{s.quotes_max} 条" if s.quotes_max else "不需要",
                                 "item_fields": {k: {"type": t, "required": r, "desc": d} for k, (t, r, d) in s.fields.items()}}
                         for s in self.sections if s.max > 0},
            "rules": [
                "每个条目必须有 evidence：本次素材中出现的 msg_id 数组（至少 1 个）",
                "quotes 为 {msg_id, text}，text 必须从该消息原文逐字摘录（可截取连续片段），条数见各节 quotes 要求",
                "不得引用素材以外的消息；不确定的结论不要写",
                *self.extra_rules,
            ],
        }


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


# ------------------------------------------------------------------ 素材
def new_run_id(prefix: str) -> str:
    return f"run_{prefix}_{time.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}"


def save_material(state: State, run_id: str, material: dict[str, Any]) -> None:
    path = state.runs_dir / run_id
    path.mkdir(parents=True, exist_ok=True)
    state.write_json(path / "material.json", material)


def load_material(state: State, run_id: str) -> dict[str, Any]:
    if not RUN_RE.match(run_id or ""):
        raise EngineError("RUN_NOT_FOUND")
    data = state.read_json(state.runs_dir / run_id / "material.json")
    if not isinstance(data, dict) or not isinstance(data.get("evidence"), dict):
        raise EngineError("RUN_NOT_FOUND")
    return data


# ------------------------------------------------------------------ 校验
def _type_ok(value: Any, kind: str) -> bool:
    if kind == "str":
        return isinstance(value, str) and value.strip() != ""
    if kind == "list[str]":
        return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
    if kind == "list[quote]":
        return isinstance(value, list) and all(isinstance(v, dict) and isinstance(v.get("msg_id"), str)
                                               and isinstance(v.get("text"), str) for v in value)
    return True


ItemCheck = Callable[[str, str, dict[str, Any], list[str]], list[str]]


def validate(semantic: Any, schema: SemanticSchema, evidence: dict[str, dict[str, Any]],
             max_len: int = 600, item_check: ItemCheck | None = None) -> tuple[list[str], list[str], dict[str, Any]]:
    """返回 (errors, warnings, cleaned)。errors 非空即不通过。

    item_check(section_key, where, item, evidence_ids) 返回该条目的额外错误（如 chat_id 归属、日期范围）。
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(semantic, dict):
        return ["语义稿顶层必须是对象"], warnings, {}
    cleaned: dict[str, Any] = {}
    known = set(schema.top) | {s.key for s in schema.sections}
    for key in semantic:
        if key not in known:
            warnings.append(f"忽略未知字段 {key}")
    for key, (kind, required, _d) in schema.top.items():
        value = semantic.get(key)
        if value in (None, "", []):
            if required:
                errors.append(f"缺少顶层字段 {key}")
            continue
        if not _type_ok(value, kind):
            errors.append(f"顶层字段 {key} 类型应为 {kind}")
            continue
        if isinstance(value, str) and len(value) > max_len:
            errors.append(f"顶层字段 {key} 过长（最多 {max_len} 字）")
            continue
        cleaned[key] = value
    for sec in schema.sections:
        items = semantic.get(sec.key) or []
        if not isinstance(items, list):
            errors.append(f"{sec.key} 必须是数组")
            continue
        if len(items) < sec.min:
            errors.append(f"{sec.key}（{sec.label}）至少需要 {sec.min} 条，当前 {len(items)} 条")
        if len(items) > sec.max:
            errors.append(f"{sec.key}（{sec.label}）最多 {sec.max} 条，当前 {len(items)} 条")
        out_items = []
        for i, item in enumerate(items[: sec.max]):
            where = f"{sec.key}[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{where} 必须是对象")
                continue
            ok = True
            for name, (kind, required, _d) in sec.fields.items():
                value = item.get(name)
                if value in (None, "", []):
                    if required:
                        errors.append(f"{where} 缺少 {name}")
                        ok = False
                    continue
                if not _type_ok(value, kind):
                    errors.append(f"{where}.{name} 类型应为 {kind}")
                    ok = False
                elif isinstance(value, str) and len(value) > max_len:
                    errors.append(f"{where}.{name} 过长（最多 {max_len} 字）")
                    ok = False
            ev = item.get("evidence") if isinstance(item.get("evidence"), list) else []
            ev = [x for x in ev if isinstance(x, str)]
            if not ev:
                errors.append(f"{where} 缺少 evidence（至少 1 个 msg_id）")
                ok = False
            bad = [x for x in ev if x not in evidence]
            if bad:
                errors.append(f"{where} 的证据不在本次素材中：{', '.join(bad[:5])}")
                ok = False
            quotes = item.get("quotes") if isinstance(item.get("quotes"), list) else []
            if not sec.quotes_min <= len(quotes) <= sec.quotes_max:
                errors.append(f"{where} 需要 {sec.quotes_min}~{sec.quotes_max} 条 quotes，当前 {len(quotes)} 条")
                ok = False
            for qi, quote in enumerate(quotes[: sec.quotes_max]):
                if not isinstance(quote, dict):
                    errors.append(f"{where}.quotes[{qi}] 必须是 {{msg_id, text}}")
                    ok = False
                    continue
                qid, qtext = quote.get("msg_id"), quote.get("text")
                if qid not in evidence:
                    errors.append(f"{where}.quotes[{qi}] 的 msg_id 不在本次素材中：{qid}")
                    ok = False
                elif not norm(qtext).strip("…") or norm(qtext).strip("…") not in norm(evidence[qid]["text"]):
                    errors.append(f"{where}.quotes[{qi}] 不是该消息原文的连续片段，请逐字摘录")
                    ok = False
                elif qid not in ev:
                    ev.append(qid)
            if ok and item_check is not None:
                extra = item_check(sec.key, where, item, ev)
                if extra:
                    errors.extend(extra)
                    ok = False
            if ok:
                roles = {evidence[x].get("role") for x in ev if x in evidence}
                if schema.self_only_warning and roles == {"me"}:
                    warnings.append(f"{where} 的证据全部来自“我”的发言，结论可能不是关于对方")
                out_items.append({**{k: item[k] for k in sec.fields if k in item}, "evidence": ev,
                                  "quotes": [{"msg_id": q["msg_id"], "text": q["text"]}
                                             for q in quotes[: sec.quotes_max]]})
        cleaned[sec.key] = out_items
    return errors[:MAX_ERRORS], warnings[:MAX_ERRORS], cleaned


# ------------------------------------------------------------------ 合成
def compose(schema: SemanticSchema, cleaned: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """把校验通过的语义稿展开为可渲染的章节：引文附上时间与发言人。"""
    sections = []
    for sec in schema.sections:
        items = []
        for item in cleaned.get(sec.key) or []:
            quotes = []
            for q in item["quotes"]:
                e = evidence[q["msg_id"]]
                quotes.append({"msg_id": q["msg_id"], "text": q["text"], "time": e.get("time"),
                               "sender": e.get("sender"), "role": e.get("role"), "chat": e.get("chat")})
            items.append({**item, "quotes": quotes, "evidence_count": len(item["evidence"])})
        sections.append({"key": sec.key, "label": sec.label, "items": items})
    return sections


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
