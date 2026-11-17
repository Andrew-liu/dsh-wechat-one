"""从 Python 注册表生成 engine/manifest.json（JS 侧据此设置超时与风险等级校验）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from wechat_one import features  # noqa: E402,F401
from wechat_one.core.dispatch import manifest  # noqa: E402


def main() -> int:
    target = ROOT / "engine" / "manifest.json"
    text = json.dumps(manifest(), ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != text:
            print("engine/manifest.json 已过期，请运行 npm run manifest", file=sys.stderr)
            return 1
        print("manifest ok")
        return 0
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
