"""对话片段切分：同一会话内，相邻消息间隔超过 30 分钟就切开；单段最多 20 条或 1500 字。"""
from __future__ import annotations

from dataclasses import dataclass, field

GAP_SECONDS = 30 * 60
MAX_MESSAGES = 20
MAX_CHARS = 1500


@dataclass
class Item:
    rowid: int
    ts: int
    sender: str
    text: str
    searchable: bool


@dataclass
class Chunk:
    items: list[Item] = field(default_factory=list)
    chars: int = 0

    @property
    def start_ts(self) -> int:
        return self.items[0].ts

    @property
    def end_ts(self) -> int:
        return self.items[-1].ts

    def body(self) -> str:
        return "\n".join(f"{i.sender}: {i.text}" for i in self.items if i.searchable)


def split(items: list[Item], gap: int = GAP_SECONDS, max_messages: int = MAX_MESSAGES,
          max_chars: int = MAX_CHARS) -> list[Chunk]:
    """items 需按时间升序。只有可检索（文本类）消息计入字数上限，非文本消息也归入片段以保持上下文完整。"""
    chunks: list[Chunk] = []
    current = Chunk()
    for item in items:
        length = len(item.text) if item.searchable else 0
        if current.items and (
            item.ts - current.items[-1].ts > gap
            or len(current.items) >= max_messages
            or (current.chars + length > max_chars and current.chars > 0)
        ):
            chunks.append(current)
            current = Chunk()
        current.items.append(item)
        current.chars += length
    if current.items:
        chunks.append(current)
    return [c for c in chunks if any(i.searchable for i in c.items)]
