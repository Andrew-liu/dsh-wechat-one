"""测试工具：临时状态目录 + 合成镜像 + 直接调用引擎 dispatch。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["WS_CONFIG"] = str(Path(tempfile.gettempdir()) / "dsh-wechat-one-test-no-config.yaml")

from wechat_one import features  # noqa: E402,F401
from wechat_one.core.dispatch import handle  # noqa: E402

import fixtures  # noqa: E402


class EngineCase(unittest.TestCase):
    """每个测试类共用一份镜像与已建好的索引（setUpClass），测试之间只读。"""

    build_index = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wechat-one-test-"))
        cls.mirror = fixtures.build(cls.tmp / "decrypted")
        cls.state = cls.tmp / "state"
        cls.config = {"sourceType": "decrypted_dir", "sourcePath": str(cls.mirror)}
        if cls.build_index:
            res = cls.call("refresh", {"reindex_only": True})
            assert res["ok"], res

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def call(cls, action, params=None, config=None):
        return handle({"action": action, "params": params or {}, "config": config or cls.config}, str(cls.state))

    def ok(self, action, params=None, config=None):
        res = self.call(action, params, config)
        self.assertTrue(res["ok"], res)
        return res["data"]

    def err(self, action, params=None, config=None):
        res = self.call(action, params, config)
        self.assertFalse(res["ok"], res)
        return res["error"]

    def conv_id(self, name):
        data = self.ok("contacts", {"query": name})
        matches = [c for c in data["conversations"] if c["name"] == name]
        self.assertEqual(len(matches), 1, data)
        return matches[0]["conversation_id"]
