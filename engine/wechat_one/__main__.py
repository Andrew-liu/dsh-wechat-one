"""引擎入口：读 stdin 一行 JSON 请求，写 stdout 一行 JSON 响应。

用法：python -X utf8 -B -m wechat_one --state <私有目录>
      python -m wechat_one --manifest   # 输出 action 清单（供生成 engine/manifest.json）
"""
from __future__ import annotations

import argparse
import json
import sys

from . import features  # noqa: F401 - 注册全部 action
from .core.dispatch import handle, manifest
from .core.errors import EngineError

MAX_REQUEST = 4 * 1024 * 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wechat_one")
    parser.add_argument("--state")
    parser.add_argument("--manifest", action="store_true")
    args = parser.parse_args(argv)
    if args.manifest:
        sys.stdout.write(json.dumps(manifest(), ensure_ascii=False, indent=2) + "\n")
        return 0
    if not args.state:
        response = EngineError("PRIVATE_STATE_REQUIRED").to_dict()
    else:
        raw = sys.stdin.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            response = EngineError("INVALID_ARGS", "请求过大", public=True).to_dict()
        else:
            try:
                request = json.loads(raw)
            except ValueError:
                request = None
            response = handle(request, args.state)
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
