"""架构与可用性：依赖方向、manifest 同步、子进程入口、统计边界、日志脱敏。"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import ROOT, EngineCase
from wechat_one.core.dispatch import ACTIONS, manifest
from wechat_one.core.log import redact
from wechat_one.shared import stats

PKG = ROOT / "engine" / "wechat_one"
LAYERS = ("core", "data", "integrations", "shared", "features")


def imports_of(path: Path) -> set[str]:
    """返回该文件引用的 wechat_one 子包：顶层层名（core/data/...），功能模块记为 features.<name>。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = list(path.relative_to(PKG).parts[:-1])
    found = set()

    def add(parts: list[str]) -> None:
        if not parts:
            return
        found.add(f"features.{parts[1]}" if parts[0] == "features" and len(parts) > 1 else parts[0])

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = package[: len(package) - (node.level - 1)]
        elif node.module and node.module.startswith("wechat_one"):
            base = []
        else:
            continue
        module = [p for p in (node.module or "").split(".") if p]
        if not node.level:
            module = module[1:]
        target = base + module
        if node.module is None:
            for alias in node.names:
                add(target + [alias.name])
        else:
            add(target)
    return found


class ArchitectureTest(unittest.TestCase):
    def files(self, layer):
        return [p for p in (PKG / layer).rglob("*.py") if p.name != "__init__.py" or layer == "features"]

    def test_dependency_direction(self):
        allowed = {
            "core": {"core"},
            "integrations": {"core", "integrations"},
            "data": {"core", "data", "integrations"},
            "shared": {"core", "data", "integrations", "shared"},
        }
        for layer, ok in allowed.items():
            for path in self.files(layer):
                bad = {i for i in imports_of(path) if i.split(".")[0] not in ok}
                self.assertFalse(bad, f"{path.relative_to(PKG)} 不应依赖 {bad}")

    def test_features_do_not_import_each_other(self):
        for path in (PKG / "features").glob("*.py"):
            if path.name == "__init__.py":
                continue
            others = {i for i in imports_of(path) if i.startswith("features.") and i != f"features.{path.stem}"}
            self.assertFalse(others, f"{path.name} 引用了其他功能 {others}")

    def test_manifest_json_in_sync(self):
        on_disk = json.loads((ROOT / "engine" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, json.loads(json.dumps(manifest())))
        self.assertEqual(set(on_disk["actions"]), set(ACTIONS))

    def test_subprocess_entry_roundtrip(self):
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "wechat_one", "--state",
                               str(Path(ROOT.parent) / ".wechat-one-entry-test")],
                              input=json.dumps({"action": "nope"}), capture_output=True, text=True,
                              encoding="utf-8", cwd=ROOT / "engine", timeout=60)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "UNKNOWN_ACTION")
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "wechat_one", "--state", "x"],
                              input="not json", capture_output=True, text=True, encoding="utf-8",
                              cwd=ROOT / "engine", timeout=60)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "INVALID_ARGS")
        import shutil
        shutil.rmtree(Path(ROOT.parent) / ".wechat-one-entry-test", ignore_errors=True)
        shutil.rmtree(ROOT / "engine" / "x", ignore_errors=True)

    def test_state_inside_package_rejected(self):
        from wechat_one.core.dispatch import handle
        res = handle({"action": "status"}, str(ROOT / "engine" / "inner-state"))
        self.assertEqual(res["error"]["code"], "PRIVATE_STATE_REQUIRED")


class StatsAndRedactTest(unittest.TestCase):
    def test_sentiment_negation(self):
        self.assertEqual(stats.sentiment_score("今天很开心"), 1.0)
        self.assertEqual(stats.sentiment_score("不开心"), 0.0)
        self.assertIsNone(stats.sentiment_score("今天周二"))

    def test_ngram_needs_multiple_days(self):
        texts = [("2026-01-01", "这个电影绝绝子"), ("2026-01-02", "绝绝子，太好吃了"),
                 ("2026-01-03", "今天的晚霞绝绝子啊"), ("2026-01-04", "新耳机音质绝绝子"),
                 ("2026-01-01", "一次性短语出现")]
        phrases = [c["phrase"] for c in stats.ngram_candidates(texts, min_days=3)]
        self.assertIn("绝绝子", phrases)
        self.assertNotIn("一次性", phrases)

    def test_empty_stats(self):
        self.assertEqual(stats.chat_stats([], None), {"total": 0})

    def test_redact(self):
        text = redact(r"open C:\Users\me\wechat\db wxid_abc123 key " + "ab" * 20)
        self.assertNotIn("wxid_abc123", text)
        self.assertNotIn(r"C:\Users", text)
        self.assertNotIn("ab" * 20, text)


class EnvelopeTest(EngineCase):
    build_index = False

    def test_every_error_has_code_message_retryable(self):
        for action, params in (("search", {}), ("search", {"query": 1}), ("context", {"message_id": "bad"}),
                               ("topic_search", {"topic": "x"}), ("persona_prepare", {"conversation_id": "x"})):
            err = self.err(action, params)
            self.assertRegex(err["code"], r"^[A-Z_]+$")
            self.assertIsInstance(err["message"], str)
            self.assertIn("retryable", err)


if __name__ == "__main__":
    unittest.main()
