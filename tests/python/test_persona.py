"""人格报告：素材准备、证据校验三种拒绝情形、渲染产物。"""
from __future__ import annotations

import unittest
from pathlib import Path

from helpers import EngineCase


class PersonaTest(EngineCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        res = cls.call("contacts", {"query": "舟舟"})
        cls.lin = res["data"]["conversations"][0]["conversation_id"]

    def prepare(self):
        return self.ok("persona_prepare", {"conversation_id": self.lin})

    def semantic_from(self, prep, them_only=True):
        msgs = [m for m in prep["material"]["messages"] if m["role"] == "对方" or not them_only]
        a, b, c = msgs[0], msgs[1], msgs[2]

        def item(m, title):
            return {"title": title, "detail": "根据聊天片段的观察。", "evidence": [m["id"]],
                    "quotes": [{"msg_id": m["id"], "text": m["text"][:6]}]}

        return {
            "one_liner": "爱笑、会主动分享生活的朋友",
            "summary": "聊天中常用“哈哈”，经常主动约饭约爬山，也会分享工作压力。",
            "caveats": ["素材只覆盖三个月"],
            "communication_style": [item(a, "语气轻松")],
            "interests": [item(b, "喜欢户外与拍照")],
            "emotional_expression": [item(c, "会直接表达疲惫")],
            "interaction_pattern": [item(a, "经常主动开口")],
            "catchphrases": [{"phrase": "哈哈", "detail": "表达认同", "evidence": [a["id"]],
                              "quotes": [{"msg_id": a["id"], "text": a["text"][:4]}]}],
        }

    def test_prepare_shape(self):
        prep = self.prepare()
        self.assertRegex(prep["run_id"], r"^run_[0-9a-z_]{8,60}$")
        stats = prep["stats"]
        self.assertEqual(stats["total"], 64)
        self.assertEqual(stats["them"] + stats["me"], 64)
        self.assertEqual(len(stats["hourly_them"]), 24)
        self.assertIn("them_ratio", stats["initiative"])
        self.assertTrue(prep["material"]["messages"])
        self.assertIn("communication_style", prep["schema"]["sections"])
        roles = {m["role"] for m in prep["material"]["messages"]}
        self.assertEqual(roles, {"对方", "我"})

    def test_rejects_group_and_insufficient(self):
        growth = self.conv_id("增长交流群")
        self.assertEqual(self.err("persona_prepare", {"conversation_id": growth})["code"], "NOT_PRIVATE_CHAT")
        chen = self.conv_id("陈雨")
        err = self.err("persona_prepare", {"conversation_id": chen})
        self.assertEqual(err["code"], "INSUFFICIENT_DATA")

    def test_validation_rejections(self):
        prep = self.prepare()
        good = self.semantic_from(prep)
        # 1) 伪造的 msg_id
        bad = dict(good, interests=[{**good["interests"][0], "evidence": ["mffffffffffffffff"]}])
        err = self.err("persona_render", {"run_id": prep["run_id"], "semantic": bad})
        self.assertEqual(err["code"], "SEMANTIC_INVALID")
        self.assertTrue(any("不在本次素材" in e for e in err["details"]["errors"]))
        # 2) 其他会话（不在本次素材）的真实 msg_id
        other = self.ok("search", {"query": "置顶笔记"})["messages"][0]["message_id"]
        bad = dict(good, interests=[{**good["interests"][0], "evidence": [other],
                                     "quotes": [{"msg_id": other, "text": "置顶笔记"}]}])
        err = self.err("persona_render", {"run_id": prep["run_id"], "semantic": bad})
        self.assertTrue(any("不在本次素材" in e for e in err["details"]["errors"]))
        # 3) 改写过的引文
        q = good["interests"][0]["quotes"][0]
        bad = dict(good, interests=[{**good["interests"][0], "quotes": [{**q, "text": q["text"] + "（改写）"}]}])
        err = self.err("persona_render", {"run_id": prep["run_id"], "semantic": bad})
        self.assertTrue(any("逐字" in e for e in err["details"]["errors"]))
        # 缺少必填章节
        bad = {k: v for k, v in good.items() if k != "communication_style"}
        self.assertEqual(self.err("persona_render", {"run_id": prep["run_id"], "semantic": bad})["code"],
                         "SEMANTIC_INVALID")

    def test_render_writes_reports(self):
        prep = self.prepare()
        for theme in ("newspaper", "claude"):
            data = self.ok("persona_render", {"run_id": prep["run_id"], "semantic": self.semantic_from(prep),
                                              "theme": theme})
            html = Path(data["report_html"]).read_text(encoding="utf-8")
            md = Path(data["report_md"]).read_text(encoding="utf-8")
            self.assertIn("舟舟", html)
            self.assertIn("<svg", html)
            self.assertNotIn("http://", html.replace('xmlns="http://www.w3.org/2000/svg"', ""))
            self.assertIn("not-verified", md)
            self.assertIn("不是心理测评", md)
            self.assertTrue(Path(data["report_html"]).is_relative_to(Path(self.state)))

    def test_unknown_run_and_theme(self):
        self.assertEqual(self.err("persona_render", {"run_id": "run_persona_x_000000", "semantic": {}})["code"],
                         "RUN_NOT_FOUND")
        prep = self.prepare()
        self.assertEqual(self.err("persona_render", {"run_id": prep["run_id"], "semantic": {}, "theme": "nope"})["code"],
                         "THEME_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
