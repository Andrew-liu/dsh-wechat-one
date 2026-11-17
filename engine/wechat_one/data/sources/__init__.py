"""数据源工厂：按有效配置打开只读数据源。"""
from __future__ import annotations

from pathlib import Path

from ...core.config import Settings
from ...core.errors import EngineError
from .base import Source


def open_source(settings: Settings) -> Source:
    tz = settings.tz()
    me = {"ids": settings.me_ids, "names": settings.me_names}
    if not settings.source_path:
        raise EngineError("SOURCE_NOT_CONFIGURED")
    if settings.source_type == "json":
        from .json_source import JsonSource

        if not Path(settings.source_path).expanduser().is_file():
            raise EngineError("SOURCE_NOT_FOUND")
        return JsonSource(settings.source_path, tz=tz, me=me)
    if settings.source_type == "decrypted_dir":
        from .decrypted_dir import DecryptedDirSource

        return DecryptedDirSource(settings.source_path, tz=tz, me=me, account=settings.source_account, paths={})
    raise EngineError("SOURCE_NOT_CONFIGURED", "不支持的 sourceType（可选 decrypted_dir / json）", public=True)


__all__ = ["Source", "open_source"]
