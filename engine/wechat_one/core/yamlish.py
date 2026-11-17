"""YAML 子集解析（只用标准库）。

来源：Andrew-liu/wechat-summary（MIT）wechat_summary/config.py 的 parse_yaml 部分，原样复制。
"""
from __future__ import annotations

import json
import re
from typing import Any


class _YamlError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_inline(text: str) -> list[str]:
    parts, depth, quote, buf = [], 0, None, []
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    if text[0] == text[-1] and text[0] in "\"'" and len(text) >= 2:
        body = text[1:-1]
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError:
                return body
        return body.replace("''", "'")
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(p) for p in _split_inline(text[1:-1])]
    if text.startswith("{") and text.endswith("}"):
        result = {}
        for part in _split_inline(text[1:-1]):
            key, _, value = part.partition(":")
            result[_scalar(key)] = _scalar(value)
        return result
    lower = text.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False
    if lower in ("null", "~", "none"):
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?\d*\.\d+", text):
        return float(text)
    return text


def _split_key(content: str) -> tuple[str, str] | None:
    quote = None
    for i, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ":" and (i + 1 == len(content) or content[i + 1] in " \t"):
            return content[:i].strip(), content[i + 1:].strip()
    return None


def parse_yaml(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise _YamlError("缩进不能使用 Tab")
        stripped = _strip_comment(raw)
        if stripped.strip() in ("", "---"):
            continue
        lines.append((len(stripped) - len(stripped.lstrip(" ")), stripped.strip()))

    def block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines):
            return None, index
        if lines[index][1].startswith("- ") or lines[index][1] == "-":
            return seq(index, indent)
        return mapping(index, indent)

    def seq(index: int, indent: int) -> tuple[list, int]:
        items: list[Any] = []
        while index < len(lines):
            ind, content = lines[index]
            if ind < indent or not (content.startswith("- ") or content == "-"):
                break
            if ind > indent:
                raise _YamlError(f"列表缩进不一致：{content}")
            rest = content[1:].strip()
            index += 1
            if not rest:
                value, index = block(index, lines[index][0]) if index < len(lines) and lines[index][0] > ind else (None, index)
                items.append(value)
                continue
            kv = _split_key(rest) if not rest.startswith(("[", "{", '"', "'")) else None
            if kv is None:
                items.append(_scalar(rest))
                continue
            # "- key: value" 形式的映射项
            child_indent = ind + 2
            lines.insert(index, (child_indent, rest))
            value, index = mapping(index, child_indent)
            items.append(value)
        return items, index

    def mapping(index: int, indent: int) -> tuple[dict, int]:
        result: dict[str, Any] = {}
        while index < len(lines):
            ind, content = lines[index]
            if ind < indent:
                break
            if ind > indent:
                raise _YamlError(f"缩进不一致：{content}")
            if content.startswith("- "):
                break
            kv = _split_key(content)
            if kv is None:
                raise _YamlError(f"无法解析：{content}")
            key, rest = kv
            key = _scalar(key) if key[:1] in "\"'" else key
            index += 1
            if rest in ("", "|", ">"):
                if index < len(lines) and (lines[index][0] > ind or (lines[index][0] == ind and lines[index][1].startswith("- "))):
                    value, index = block(index, lines[index][0])
                else:
                    value = None
                result[str(key)] = value
            else:
                result[str(key)] = _scalar(rest)
        return result, index

    if not lines:
        return {}
    value, index = block(0, lines[0][0])
    if index != len(lines):
        raise _YamlError(f"无法解析：{lines[index][1]}")
    return value


