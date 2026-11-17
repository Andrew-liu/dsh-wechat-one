"""可选的本地向量服务（Ollama）。只允许本机回环地址，失败时由调用方降级为纯全文检索。"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from ..core.config import Settings, is_loopback
from ..core.errors import EngineError


def _post(url: str, payload: dict[str, Any], timeout: float) -> Any:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 仅本机地址
        return json.loads(resp.read().decode("utf-8"))


def available(settings: Settings, timeout: float = 2.0) -> bool:
    if not settings.embedding_enabled():
        return False
    try:
        with urllib.request.urlopen(settings.embedding_base_url.rstrip("/") + "/api/tags", timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        names = {str(m.get("name", "")).split(":")[0] for m in data.get("models", [])}
        return settings.embedding_model.split(":")[0] in names
    except Exception:  # noqa: BLE001
        return False


def embed(texts: list[str], settings: Settings, timeout: float = 120.0) -> list[list[float]]:
    if not settings.embedding_enabled() or not is_loopback(settings.embedding_base_url):
        raise EngineError("EMBEDDING_UNAVAILABLE")
    try:
        data = _post(settings.embedding_base_url.rstrip("/") + "/api/embed",
                     {"model": settings.embedding_model, "input": texts}, timeout)
    except Exception as exc:  # noqa: BLE001
        raise EngineError("EMBEDDING_UNAVAILABLE", repr(exc)) from exc
    vectors = data.get("embeddings") if isinstance(data, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EngineError("EMBEDDING_UNAVAILABLE", "unexpected embedding response")
    return [[float(x) for x in v] for v in vectors]
