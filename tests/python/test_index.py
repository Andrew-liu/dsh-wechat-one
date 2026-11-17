"""索引层：切片、二元组全文检索、增量刷新、原子替换、损坏自愈、单写者锁。"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from helpers import EngineCase, fixtures
from wechat_one.core import locks
from wechat_one.core.state import State
from wechat_one.data.index import builder, chunker, cjk


class CjkAndChunkerTest(unittest.TestCase):
    def test_bigrams_and_latin(self):
        self.assertEqual(cjk.tokens("涨粉技巧SEO"), ["涨粉", "粉技", "技巧", "seo"])
        self.assertEqual(cjk.tokens("粉"), ["粉"])
        self.assertEqual(cjk.phrase("涨粉"), '"涨粉"')
        self.assertEqual(cjk.phrase("粉"), "粉*")
        self.assertIsNone(cjk.phrase("！？"))
        self.assertEqual(cjk.all_of("涨粉 起号"), '"涨粉" AND "起号"')

    def test_chunk_split_by_gap_and_size(self):
        items = [chunker.Item(i, i * 60, "a", "你好", True) for i in range(25)]
        items.append(chunker.Item(99, 25 * 60 + 31 * 60, "a", "隔了很久", True))
        chunks = chunker.split(items)
        self.assertEqual([len(c.items) for c in chunks], [20, 5, 1])

    def test_non_searchable_only_chunks_dropped(self):
        items = [chunker.Item(1, 0, "a", "[图片]", False)]
        self.assertEqual(chunker.split(items), [])


class IndexTest(EngineCase):
    def test_counts_and_two_char_hit(self):
        data = self.ok("search", {"query": "涨粉"})
        self.assertGreaterEqual(data["total"], 2)
        for m in data["messages"]:
            self.assertIn("涨粉", m["text"])

    def test_official_accounts_excluded(self):
        data = self.ok("search", {"query": "涨粉神器"})
        self.assertEqual(data["total"], 0)

    def test_cross_shard_conversation_present(self):
        hike = self.conv_id("周末徒步小队")
        data = self.ok("search", {"query": "登山杖", "conversation_ids": [hike]})
        self.assertEqual(data["total"], 1)

    def test_noop_refresh_keeps_version(self):
        before = self.ok("status")["index"]["version"]
        res = self.ok("refresh", {"reindex_only": True})
        self.assertFalse(res["reindexed"])
        self.assertEqual(self.ok("status")["index"]["version"], before)


class IncrementalTest(EngineCase):
    def test_only_changed_shard_reingested_and_version_bumps(self):
        v1 = self.ok("status")["index"]["version"]
        extra = [(fixtures.HIKE, 5, "08:00", "wxid_sun", 1, "改到周日出发，记得带雨衣", False)]
        fixtures.build(self.mirror.parent / "decrypted", extra=extra)
        # 重建 contact.db 也会变，但 message_0 内容不变：增长交流群消息 ID 应保持稳定
        before = self.ok("search", {"query": "完播率"})["messages"][0]["message_id"]
        res = self.ok("refresh", {"reindex_only": True})
        self.assertTrue(res["reindexed"])
        self.assertEqual(self.ok("status")["index"]["version"], v1 + 1)
        self.assertEqual(self.ok("search", {"query": "雨衣"})["total"], 1)
        self.assertEqual(self.ok("search", {"query": "完播率"})["messages"][0]["message_id"], before)


class FailureTest(EngineCase):
    def test_failed_build_keeps_previous_index(self):
        state = State(self.state)
        fixtures.build(self.mirror.parent / "decrypted", extra=[(fixtures.HIKE, 6, "08:00", "wxid_sun", 1, "新消息", False)])
        with mock.patch.object(builder.Builder, "_ingest", side_effect=RuntimeError("boom")):
            err = self.err("refresh", {"reindex_only": True})
        self.assertEqual(err["code"], "ENGINE_ERROR")
        self.assertFalse(state.index_next_path.exists())
        self.assertGreater(self.ok("search", {"query": "涨粉"})["total"], 0)
        self.assertEqual(self.ok("status")["index"]["last_error"], "ENGINE_ERROR")
        self.ok("refresh", {"reindex_only": True})
        self.assertIsNone(self.ok("status")["index"]["last_error"])

    def test_busy_when_writer_locked(self):
        state = State(self.state)
        with locks.exclusive(state.locks_dir, "writer", progress="测试占用"):
            err = self.err("refresh", {"reindex_only": True})
            self.assertEqual(err["code"], "BUSY")
            self.assertTrue(err["retryable"])
            self.assertTrue(self.ok("status")["index"]["building"])
        self.assertFalse(self.ok("status")["index"]["building"])


class CorruptionTest(EngineCase):
    def test_corrupt_index_detected_and_rebuilt(self):
        state = State(self.state)
        state.index_path.write_bytes(b"not a database at all" * 100)
        err = self.err("search", {"query": "涨粉"})
        self.assertEqual(err["code"], "INDEX_REBUILDING")
        status = self.ok("status")
        self.assertTrue(status["index"]["rebuild_required"])
        self.assertEqual(status["next_action"], "refresh")
        self.ok("refresh", {"reindex_only": True})
        self.assertGreater(self.ok("search", {"query": "涨粉"})["total"], 0)

    def test_schema_version_mismatch_triggers_rebuild(self):
        state = State(self.state)
        con = sqlite3.connect(state.index_path)
        con.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        con.commit()
        con.close()
        self.assertEqual(self.err("contacts")["code"], "INDEX_REBUILDING")
        self.ok("refresh", {"reindex_only": True})
        self.assertTrue(self.ok("contacts")["conversations"])


class NotIndexedTest(EngineCase):
    build_index = False

    def test_not_indexed_and_next_action(self):
        self.assertEqual(self.err("search", {"query": "涨粉"})["code"], "NOT_INDEXED")
        self.assertEqual(self.ok("status")["next_action"], "refresh")

    def test_missing_source_error_does_not_leak_path(self):
        err = self.err("refresh", {"reindex_only": True}, {"sourcePath": str(self.tmp / "nope")})
        self.assertEqual(err["code"], "SOURCE_NOT_FOUND")
        self.assertNotIn(str(self.tmp), err["message"])


if __name__ == "__main__":
    unittest.main()
