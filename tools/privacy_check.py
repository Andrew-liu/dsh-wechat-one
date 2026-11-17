"""发布前隐私与合规检查：发布清单里不得包含解密/取钥实现、密钥文件、镜像或真实数据。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CODE = [
    (re.compile(r"\bOpenProcess\b"), "进程打开"),
    (re.compile(r"\bReadProcessMemory\b"), "读进程内存"),
    (re.compile(r"\bVirtualQueryEx\b"), "进程内存扫描"),
    (re.compile(r"Config\.Cipher"), "WCDB 密钥锚点"),
    (re.compile(r"PRAGMA\s+key", re.I), "SQLCipher 密钥"),
    (re.compile(r"\bfrida\.(attach|spawn)"), "调试器注入"),
]
FORBIDDEN_FILES = [re.compile(p) for p in (r"key_windows\.txt$", r"all_keys\.json$", r"(^|/)decrypted/", r"\.db$",
                                           r"\.sqlite$", r"(^|/)\.dsh-wechat-one/", r"secret\.key$")]
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".json", ".md", ".yml", ".yaml", ".html", ".css", ".txt"}
ALLOW_MENTION = {"tools/privacy_check.py", "tests/python/test_architecture.py"}


def packaged_files() -> list[Path]:
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    out: set[Path] = set()
    for pattern in pkg["files"]:
        target = ROOT / pattern.rstrip("/")
        if pattern.endswith("/") and target.is_dir():
            out.update(p for p in target.rglob("*") if p.is_file())
        elif "*" in pattern:
            out.update(p for p in ROOT.glob(pattern) if p.is_file())
        elif target.is_file():
            out.add(target)
    out.add(ROOT / "package.json")
    return sorted(p for p in out if "__pycache__" not in p.parts)


def main() -> int:
    problems: list[str] = []
    files = packaged_files()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if any(p.search(rel) for p in FORBIDDEN_FILES):
            problems.append(f"{rel}: 不应进入发布包")
            continue
        if path.suffix not in TEXT_SUFFIXES or rel in ALLOW_MENTION:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in FORBIDDEN_CODE:
            if pattern.search(text):
                problems.append(f"{rel}: 包含 {label} 相关代码")
        if re.search(r"wxid_(?!demo|lin|chen|zhao|sun|he|abc|xxx|secret|device|1\b)[A-Za-z0-9]{6,}", text):
            problems.append(f"{rel}: 疑似包含真实 wxid")
    for required in ("LICENSE", "README.md", "PRIVACY.md", "THIRD_PARTY_NOTICES.md", "engine/manifest.json"):
        if not (ROOT / required).is_file():
            problems.append(f"缺少 {required}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"privacy check ok ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
