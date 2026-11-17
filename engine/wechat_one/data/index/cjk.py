"""中文二元组切分：让 FTS5 unicode61 支持两字词检索（如“涨粉”“起号”）。

- CJK 连续段 -> 相邻两字一组（长度为 1 的段保留单字）
- 拉丁字母 / 数字 -> 小写整词
- 其他符号丢弃
查询词用同样规则切分后组成短语查询，命中要求这些二元组在文本中连续出现。
"""
from __future__ import annotations

import re
import unicodedata

_CJK = (
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"  # 汉字
    "\u3040-\u30ff"                            # 日文假名
    "\uac00-\ud7af"                            # 韩文
)
_RUN_RE = re.compile(rf"[{_CJK}]+|[0-9a-z]+(?:[._+#-][0-9a-z]+)*")
_CJK_RE = re.compile(rf"[{_CJK}]")
MAX_TOKENS = 4000


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for run in _RUN_RE.findall(normalize(text)):
        if _CJK_RE.match(run):
            if len(run) == 1:
                out.append(run)
            else:
                out.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            out.extend(p for p in re.split(r"[._+#-]", run) if p)
        if len(out) >= MAX_TOKENS:
            break
    return out[:MAX_TOKENS]


def index_text(text: str) -> str:
    return " ".join(tokens(text))


def phrase(term: str) -> str | None:
    """把一个检索词转成 FTS5 短语查询；无有效字符时返回 None。"""
    toks = tokens(term)
    if not toks:
        return None
    if len(toks) == 1 and len(toks[0]) == 1 and _CJK_RE.match(toks[0]):
        return f"{toks[0]}*"  # 单个汉字：前缀匹配以它开头的二元组
    return '"' + " ".join(t.replace('"', "") for t in toks) + '"'


def all_of(query: str) -> str | None:
    """空格分隔的多个词：全部包含（AND）。"""
    parts = [p for p in (phrase(w) for w in query.split()) if p]
    return " AND ".join(parts) if parts else None
