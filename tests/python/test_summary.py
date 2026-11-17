"""日报：会话选择、素材分块、证据与归属校验、统计版兜底、补跑台账。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import EngineCase
from wechat_one.features import summary as feature


class SummaryTest(EngineCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = {**cls.config, "meNames": ["阿栗"],
                      "groupSets": {"增长": ["增长交流群"], "两个群": ["增长交流群", "周末徒步小队"]}}

    def daily(self, **extra):
        return self.ok("summary_prepare", {"group_set": "增长", "period": "daily", "date": "2026-08-03", **extra})

    @staticmethod
    def topic(msg, **extra):
        return {"title": "起号先做垂直，前三秒给结论能拉完播率", "summary": "舟舟给出两条经验，大家认同。",
                "points": ["账号要垂直", "开头先放成品"], "evidence": [msg["id"]],
                "quotes": [{"msg_id": msg["id"], "text": msg["text"][:8]}], **extra}

    def test_prepare_daily_shape(self):
        prep = self.daily()
        self.assertEqual(prep["label"], "增长")
        self.assertEqual(prep["window"]["slug"], "2026-08-03")
        stats = prep["stats"]
        self.assertEqual(stats["speech_count"], 6)
        self.assertEqual(stats["sender_count"], 4)
        material = prep["material"]
        self.assertEqual((material["part"], material["parts_total"], material["count"]), (1, 1, 6))
        self.assertTrue(any(m.get("me") for m in material["messages"]))
        self.assertNotIn("daily", prep["schema"]["sections"])
        self.assertIn("for_me", prep["schema"]["sections"])
        self.assertFalse(any("早于报告结束时间" in w for w in prep["warnings"]))

    def test_render_validates_and_writes(self):
        prep = self.daily()
        msg = prep["material"]["messages"][1]
        good = {"one_liner": "起号经验集中在垂直定位与完播率", "mood": "务实", "topics": [self.topic(msg)],
                "insights": [{"claim": "开头决定完播率", "stance": "consensus", "evidence": [msg["id"]]}]}
        data = self.ok("summary_render", {"run_id": prep["run_id"], "semantic": good})
        self.assertTrue(data["recorded"])
        html = Path(data["report_html"]).read_text(encoding="utf-8")
        md = Path(data["report_md"]).read_text(encoding="utf-8")
        self.assertIn("起号先做垂直", html)
        self.assertIn("共识", html)
        self.assertIn("not-verified", md)
        self.assertTrue(Path(data["report_html"]).is_relative_to(Path(self.state)))
        self.assertNotIn("<script", html)
        ledger = json.loads((Path(self.state) / "summary_ledger.json").read_text(encoding="utf-8"))
        self.assertTrue(any("2026-08-03" in e["done"] for e in ledger.values()))

    def test_rejections(self):
        prep = self.ok("summary_prepare", {"group_set": "两个群", "period": "weekly", "date": "2026-08-03"})
        self.assertIn("daily", prep["schema"]["sections"])
        msgs = prep["material"]["messages"]
        growth = next(m for m in msgs if m.get("chat") == "增长交流群")
        hike = next(m for m in msgs if m.get("chat") == "周末徒步小队")
        hike_id = next(c["conversation_id"] for c in prep["conversations"] if c["name"] == "周末徒步小队")
        base = {"one_liner": "两个群各聊各的", "topics": [self.topic(growth)],
                "daily": [{"date": "2026-08-03", "headline": "聊起号", "evidence": [growth["id"]]}]}
        run = prep["run_id"]

        def errors(semantic):
            err = self.err("summary_render", {"run_id": run, "semantic": semantic})
            self.assertEqual(err["code"], "SEMANTIC_INVALID")
            return " ".join(err["details"]["errors"])

        self.assertIn("证据不全来自", errors({**base, "topics": [self.topic(growth, conversation_id=hike_id)]}))
        self.assertIn("不属于本报告", errors({**base, "topics": [self.topic(growth, conversation_id="别的群")]}))
        self.assertIn("不在报告范围", errors({**base, "daily": [{"date": "2026-08-20", "headline": "x",
                                                                "evidence": [growth["id"]]}]}))
        self.assertIn("stats.links", errors({**base, "links": [{"url": "https://evil.example", "note": "x",
                                                                "evidence": [growth["id"]]}]}))
        self.assertIn("stance", errors({**base, "insights": [{"claim": "x", "stance": "maybe", "evidence": [growth["id"]]}]}))
        self.assertIn("逐字", errors({**base, "topics": [self.topic(growth, quotes=[{"msg_id": growth["id"], "text": "编的"}])]}))
        self.assertIn("至少需要 1 条", errors({"one_liner": "x", "topics": [self.topic(growth)]}))
        # 名称形式的 conversation_id 会被规范化；链接来自 stats.links
        link = prep["stats"]["links"][0]
        ok = {**base, "topics": [self.topic(hike, conversation_id="周末徒步小队")],
              "links": [{"url": link["url"], "note": "标题公式合集", "evidence": [link["msg_id"]]}]}
        data = self.ok("summary_render", {"run_id": run, "semantic": ok, "theme": "newspaper"})
        html = Path(data["report_html"]).read_text(encoding="utf-8")
        self.assertIn("周末徒步小队", html)
        self.assertIn("爆款标题的 7 个公式", html)

    def test_stats_only_and_mentions(self):
        prep = self.ok("summary_prepare", {"chats": ["增长交流群"], "period": "daily", "date": "2026-08-13"})
        self.assertEqual(len(prep["mentions_me"]), 1)
        flagged = [m for m in prep["material"]["messages"] if m.get("mentions_me")]
        self.assertEqual(flagged[0]["id"], prep["mentions_me"][0])
        self.assertEqual(prep["stats"]["links"][0]["url"], "https://example.com/plan")
        data = self.ok("summary_render", {"run_id": prep["run_id"], "stats_only": True})
        md = Path(data["report_md"]).read_text(encoding="utf-8")
        self.assertIn("stats-only", md)
        self.assertIn("https://example.com/plan", md)
        self.assertNotIn("utm_source", md)
        self.assertTrue(data["stats_only"])
        self.assertTrue(data["recorded"])
        custom = self.ok("summary_prepare", {"chats": ["增长交流群"], "period": "custom",
                                             "start_date": "2026-08-03", "end_date": "2026-08-13"})
        self.assertFalse(self.ok("summary_render", {"run_id": custom["run_id"], "stats_only": True})["recorded"])
        self.assertEqual(self.err("summary_render", {"run_id": prep["run_id"]})["code"], "INVALID_ARGS")

    def test_no_me_means_no_for_me(self):
        prep = self.ok("summary_prepare", {"chats": ["增长交流群"], "date": "2026-08-13"},
                       config={**self.config, "meNames": []})
        self.assertNotIn("for_me", prep["schema"]["sections"])
        self.assertTrue(any("我的身份" in w for w in prep["warnings"]))

    def test_selection_errors(self):
        err = self.err("summary_prepare", {"chats": ["增长"], "date": "2026-08-03"})
        self.assertEqual(err["code"], "CONVERSATION_UNRESOLVED")
        cands = err["details"]["problems"][0]["candidates"]
        self.assertEqual(cands[0]["name"], "增长交流群")
        self.assertEqual(self.err("summary_prepare", {"group_set": "nope"})["code"], "GROUP_SET_UNKNOWN")
        self.assertEqual(self.err("summary_prepare", {})["code"], "NO_SELECTION")
        self.assertEqual(self.err("summary_prepare", {"group_set": "增长", "date": "2026-09-01"})["code"], "NO_MESSAGES")
        self.assertEqual(self.err("summary_prepare", {"group_set": "增长", "period": "custom",
                                                      "start_date": "2026-01-01", "end_date": "2026-08-01"})["code"],
                         "INVALID_ARGS")

    def test_material_parts(self):
        prep = self.daily()
        self.assertEqual(self.ok("summary_material", {"run_id": prep["run_id"], "part": 1})["messages"],
                         prep["material"]["messages"])
        self.assertEqual(self.err("summary_material", {"run_id": prep["run_id"], "part": 2})["code"], "PART_NOT_FOUND")
        items = [{"id": f"m{i}", "text": "x" * 50} for i in range(500)]
        parts = feature._split_parts(items)
        self.assertTrue(all(len(p) <= feature.PART_MESSAGES for p in parts))
        self.assertEqual(sum(len(p) for p in parts), 500)

    def test_pending_ledger(self):
        sel = {"chats": ["周末徒步小队"], "period": "daily"}
        first = self.ok("summary_pending", sel)
        self.assertEqual(len(first["pending"]), 1)
        self.assertIsNotNone(first["note"])
        prep = self.ok("summary_prepare", {**sel, "date": "2026-08-04"})
        self.ok("summary_render", {"run_id": prep["run_id"], "stats_only": True})
        after = self.ok("summary_pending", {**sel, "max_catchup": 3})
        self.assertEqual(len(after["pending"]), 3)
        self.assertIn("2026-08-04", after["recent_done"])
        slugs = [p["slug"] for p in after["pending"]]
        self.assertEqual(slugs, sorted(slugs))


if __name__ == "__main__":
    unittest.main()
