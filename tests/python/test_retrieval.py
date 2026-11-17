"""检索层：会话、精确检索、主题检索（RAG）、上下文、白名单、游标、向量融合。"""
from __future__ import annotations

import unittest
from unittest import mock

from helpers import EngineCase
from wechat_one.integrations import embedding

GROWTH_TERMS = ["起号", "完播率", "引流", "流量池", "推荐算法", "互动率", "买粉", "更新频率"]


class ContactsTest(EngineCase):
    def test_lists_friends_and_groups_without_official(self):
        data = self.ok("contacts")
        names = {c["name"] for c in data["conversations"]}
        self.assertIn("增长交流群", names)
        self.assertIn("舟舟", names)
        self.assertNotIn("某某资讯", names)
        for c in data["conversations"]:
            self.assertRegex(c["conversation_id"], r"^conv_[0-9a-f]{16}$")
            self.assertNotIn("wxid", str(c))

    def test_kind_filter_and_pagination(self):
        page1 = self.ok("contacts", {"kind": "friend", "limit": 1})
        self.assertTrue(page1["has_more"])
        page2 = self.ok("contacts", {"kind": "friend", "limit": 1, "cursor": page1["next_cursor"]})
        self.assertNotEqual(page1["conversations"][0]["conversation_id"], page2["conversations"][0]["conversation_id"])
        self.assertEqual(self.err("contacts", {"kind": "group", "limit": 1, "cursor": page1["next_cursor"]})["code"],
                         "INVALID_CURSOR")


class SearchTest(EngineCase):
    def test_filters(self):
        growth = self.conv_id("增长交流群")
        data = self.ok("search", {"query": "流量池", "conversation_ids": [growth], "sender": "舟舟"})
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["messages"][0]["sender"], "舟舟")
        none = self.ok("search", {"query": "流量池", "start_date": "2026-09-01"})
        self.assertEqual(none["total"], 0)
        self.assertIsNotNone(none["note"])

    def test_and_semantics(self):
        self.assertEqual(self.ok("search", {"query": "完播率 结论"})["total"], 1)

    def test_bad_query(self):
        self.assertEqual(self.err("search", {"query": "！！"})["code"], "INVALID_ARGS")


class TopicSearchTest(EngineCase):
    def test_recall_ranking_and_terms(self):
        data = self.ok("topic_search", {"topic": "涨粉技巧", "keywords": GROWTH_TERMS})
        self.assertGreaterEqual(data["total"], 3)
        top = data["chunks"][0]
        self.assertEqual(top["conversation"], "增长交流群")
        self.assertGreaterEqual(len(top["matched_terms"]), 3)
        texts = " ".join(m["text"] for c in data["chunks"] for m in c["messages"])
        for needle in ("前 3 秒", "置顶笔记", "僵尸粉", "晚上 8 点"):
            self.assertIn(needle, texts)
        self.assertIn("涨粉技巧", data["retrieval"]["terms_without_hits"])
        self.assertEqual(data["retrieval"]["mode"], "fts")
        for c in data["chunks"]:
            self.assertNotEqual(c["conversation"], "某某资讯")

    def test_per_conversation_cap_and_paging(self):
        lin = self.conv_id("舟舟")
        data = self.ok("topic_search", {"topic": "哈哈", "keywords": ["好的", "收到", "加班"], "limit": 10})
        per_conv = {}
        for c in data["chunks"]:
            per_conv[c["conversation_id"]] = per_conv.get(c["conversation_id"], 0) + 1
        self.assertLessEqual(per_conv.get(lin, 0), 3)
        self.assertTrue(data["has_more"])
        seen = {c["chunk_id"] for c in data["chunks"]}
        nxt = self.ok("topic_search", {"topic": "哈哈", "keywords": ["好的", "收到", "加班"], "limit": 10,
                                       "cursor": data["next_cursor"]})
        self.assertFalse(seen & {c["chunk_id"] for c in nxt["chunks"]})

    def test_kinds_and_dates(self):
        data = self.ok("topic_search", {"topic": "涨粉", "keywords": GROWTH_TERMS, "kinds": ["friend"]})
        self.assertEqual(data["total"], 0)
        self.assertIsNotNone(data["note"])
        data = self.ok("topic_search", {"topic": "涨粉", "keywords": GROWTH_TERMS, "start_date": "2026-08-10"})
        self.assertTrue(all(c["start_time"] >= "2026-08-10" for c in data["chunks"]))

    def test_vector_rrf_fusion_with_stub(self):
        config = {**self.config, "embeddingProvider": "ollama"}

        def fake_embed(texts, settings, timeout=120.0):
            return [[1.0 if "私域" in t or "引流" in t else 0.1, 0.5] for t in texts]

        with mock.patch.object(embedding, "embed", side_effect=fake_embed), \
                mock.patch.object(embedding, "available", return_value=True):
            res = self.ok("refresh", {"reindex_only": True}, config)
            self.assertGreater(res["vector_indexed"], 0)
            data = self.ok("topic_search", {"topic": "引流", "keywords": ["私域", "置顶"]}, config)
        self.assertEqual(data["retrieval"]["mode"], "hybrid")
        self.assertIn("引流", " ".join(m["text"] for m in data["chunks"][0]["messages"]))

    def test_vector_unavailable_degrades(self):
        config = {**self.config, "embeddingProvider": "ollama", "embeddingBaseUrl": "http://127.0.0.1:9"}
        data = self.ok("topic_search", {"topic": "涨粉", "keywords": GROWTH_TERMS}, config)
        self.assertEqual(data["retrieval"]["vector"], "unavailable")
        self.assertGreater(data["total"], 0)


class ContextAndAllowListTest(EngineCase):
    def test_context_window(self):
        msg = self.ok("search", {"query": "置顶笔记"})["messages"][0]
        data = self.ok("context", {"message_id": msg["message_id"], "before": 1, "after": 1})
        self.assertEqual(len(data["messages"]), 3)
        self.assertEqual(data["messages"][1]["message_id"], msg["message_id"])
        self.assertTrue(data["has_more_before"])
        self.assertEqual(self.err("context", {"message_id": "m0000000000000000"})["code"], "MESSAGE_NOT_FOUND")

    def test_allowed_conversations_restrict_everything(self):
        config = {**self.config, "allowedConversations": ["周末徒步小队"]}
        self.assertEqual({c["name"] for c in self.ok("contacts", None, config)["conversations"]}, {"周末徒步小队"})
        self.assertEqual(self.ok("search", {"query": "涨粉"}, config)["total"], 0)
        self.assertEqual(self.ok("topic_search", {"topic": "涨粉", "keywords": GROWTH_TERMS}, config)["total"], 0)
        msg = self.ok("search", {"query": "置顶笔记"})["messages"][0]
        self.assertEqual(self.err("context", {"message_id": msg["message_id"]}, config)["code"],
                         "CONVERSATION_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
