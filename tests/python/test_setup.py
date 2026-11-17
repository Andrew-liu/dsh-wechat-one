"""setup：wechat-decrypt 探测顺序、复用已有安装、开关、缺脚本、密钥不外泄。外部命令全部打桩。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from helpers import EngineCase
from wechat_one.core.config import resolve
from wechat_one.core.state import State
from wechat_one.integrations import decrypt_tool

KEY = "ab" * 32


def fake_tool(root: Path, with_key: bool = False) -> Path:
    for rel in (decrypt_tool.EXTRACT_SCRIPT, decrypt_tool.DECRYPT_SCRIPT):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("# stub", encoding="utf-8")
    if with_key:
        (root / decrypt_tool.KEY_FILE).write_text(KEY, encoding="ascii")
    return root


class LocateTest(EngineCase):
    build_index = False

    def test_order_plugin_then_default(self):
        state = State(self.state)
        plugin_dir = fake_tool(self.tmp / "plugin-tool")
        default = fake_tool(state.root / "wechat-decrypt")
        found, origin = decrypt_tool.locate(resolve({"decryptToolDir": str(plugin_dir)}, state), state)
        self.assertEqual((found, origin), (plugin_dir, "plugin"))
        found, origin = decrypt_tool.locate(resolve({"decryptToolDir": str(self.tmp / "missing")}, state), state)
        self.assertEqual((found, origin), (default, "default"))

    def test_wechat_report_config_is_used(self):
        state = State(self.state)
        tool = fake_tool(self.tmp / "report-tool")
        cfg = self.tmp / "report.yaml"
        cfg.write_text(f'source:\n  path: "{self.mirror.as_posix()}"\nrefresh:\n  cwd: "{tool.as_posix()}"\n', encoding="utf-8")
        with mock.patch.dict("os.environ", {"WS_CONFIG": str(cfg)}):
            s = resolve({}, state)
            self.assertEqual(s.origin["source_path"], "wechat-report")
            self.assertEqual(decrypt_tool.locate(s, state), (tool, "wechat-report"))


class SetupActionTest(EngineCase):
    build_index = False

    def test_install_reuses_existing_without_network(self):
        tool = fake_tool(self.tmp / "existing")
        with mock.patch.object(decrypt_tool, "ensure_venv") as venv, mock.patch.object(decrypt_tool, "_run") as run:
            data = self.ok("setup", {"step": "install"}, {**self.config, "decryptToolDir": str(tool)})
        self.assertEqual(data["status"], "ready")
        venv.assert_called_once()
        run.assert_not_called()
        self.assertEqual(data["next_action"], "setup_extract_key")

    def test_install_disabled(self):
        err = self.err("setup", {"step": "install"}, {**self.config, "allowDecryptInstall": False})
        self.assertEqual(err["code"], "DECRYPT_INSTALL_DISABLED")

    def test_decrypt_requires_tool_and_key(self):
        self.assertEqual(self.err("setup", {"step": "decrypt"})["code"], "DECRYPT_TOOL_MISSING")
        tool = fake_tool(self.tmp / "nokey")
        (tool / ".venv" / "Scripts").mkdir(parents=True)
        (tool / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
        err = self.err("setup", {"step": "decrypt"}, {**self.config, "decryptToolDir": str(tool)})
        self.assertEqual(err["code"], "DECRYPT_KEY_MISSING")

    def test_decrypt_points_source_and_indexes_without_leaking_key(self):
        tool = fake_tool(self.tmp / "withkey", with_key=True)
        (tool / "decrypted").mkdir()
        import shutil
        shutil.copytree(self.mirror, tool / "decrypted", dirs_exist_ok=True)

        def fake_decrypt(tool_dir, log, timeout=0):
            return {"decrypted": 3, "skipped": 0}

        with mock.patch.object(decrypt_tool, "decrypt", side_effect=fake_decrypt):
            data = self.ok("setup", {"step": "decrypt"}, {"decryptToolDir": str(tool)})
        self.assertEqual(data["next_action"], "ready")
        self.assertGreater(data["index"]["messages"], 0)
        settings = State(self.state).settings()
        self.assertEqual(Path(settings["sourcePath"]), tool / "decrypted")
        status = self.ok("status", None, {"decryptToolDir": str(tool)})
        self.assertTrue(status["decrypt_tool"]["key_ready"])
        dump = json.dumps(status) + json.dumps(data)
        self.assertNotIn(KEY, dump)
        self.assertNotIn(str(tool), dump)

    def test_bad_step(self):
        self.assertEqual(self.err("setup", {"step": "rm"})["code"], "INVALID_ARGS")


if __name__ == "__main__":
    unittest.main()
