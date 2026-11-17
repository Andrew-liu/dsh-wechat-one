"""测试用合成镜像（虚构数据），模拟 WeinanHu/wechat-decrypt 的 decrypted/<account>/db_storage 布局。

包含：
- 增长交流群：多天讨论涨粉 / 起号 / 完播率 / 引流，会被切成多个片段（用于主题检索）
- 周末徒步小队：无关话题（放在 message_1 分片，测试跨分片）
- 私聊 林一舟：三个月 60+ 条往来（用于人格报告）
- 私聊 陈雨：少量消息（用于 INSUFFICIENT_DATA）
结构参考 Andrew-liu/wechat-summary（MIT）的 demo_data.py。
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from wechat_one.data.nicknames import encode_chatroom_ext  # noqa: E402
from wechat_one.data.parsing import zstd_compress  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
SELF = "wxid_demo_self"
ACCOUNT_DIR = "wxid_demo_self_a1b2"
GROWTH = "48010001@chatroom"
HIKE = "48020002@chatroom"
LIN = "wxid_lin"
CHEN = "wxid_chen"

PEOPLE = {SELF: ("我", ""), LIN: ("林一舟", "舟舟"), CHEN: ("Chen Yu", "陈雨"), "wxid_zhao": ("赵小满", ""),
          "wxid_sun": ("孙悦", "悦悦"), "wxid_he": ("何川", ""), "gh_news": ("某某资讯", "")}
GROUPS = {GROWTH: ("增长交流群", {SELF: "阿栗", LIN: "舟舟", "wxid_zhao": "小满", "wxid_he": "何川"}),
          HIKE: ("周末徒步小队", {SELF: "阿栗", "wxid_sun": "悦悦队长", "wxid_zhao": "小满"})}
START = datetime(2026, 8, 3, tzinfo=TZ)


def _appmsg(inner: str) -> str:
    return f'<?xml version="1.0"?><msg><appmsg appid="" sdkver="0">{inner}</appmsg></msg>'


def rows() -> list[tuple]:
    """(会话, 天偏移, 时:分, 发送者, local_type, 内容, 是否zstd)"""
    out = [
        (GROWTH, 0, "09:00", "wxid_he", 1, "大家最近涨粉怎么样？我小红书卡在 300 粉不动了", False),
        (GROWTH, 0, "09:05", LIN, 1, "起号阶段最重要的是垂直，别什么都发，账号标签乱了推荐算法就不知道推给谁", False),
        (GROWTH, 0, "09:07", "wxid_zhao", 1, "同意，我前两周换成只发露营装备，涨粉明显快了", False),
        (GROWTH, 0, "09:10", LIN, 1, ("另外完播率很关键：视频前 3 秒要直接给结论或冲突，"
                                      "我把开头改成先放成品，完播率从 18% 涨到 35%，流量池就上去了"), True),
        (GROWTH, 0, "09:12", "wxid_he", 1, "学到了，前三秒给结论", False),
        (GROWTH, 0, "09:30", SELF, 1, "我补充一个：评论区要主动回，互动率高了会进下一级流量池", False),
        (GROWTH, 3, "21:00", "wxid_zhao", 1, "有人试过私域引流吗？主页留微信号被限流了", False),
        (GROWTH, 3, "21:04", LIN, 1, "别直接留联系方式，用置顶笔记或者群聊入口引流，平台不会判违规", False),
        (GROWTH, 3, "21:06", "wxid_he", 1, "我反对买粉，买来的僵尸粉会拉低互动率，反而影响推荐", False),
        (GROWTH, 3, "21:08", "wxid_zhao", 49, _appmsg("<title>爆款标题的 7 个公式</title><type>5</type>"
                                                       "<url>https://example.com/title-formula</url>"), False),
        (GROWTH, 10, "08:30", SELF, 1, "亲测：固定每天晚上 8 点更新，一个月从 500 涨到 3000 粉", False),
        (GROWTH, 10, "08:35", LIN, 1, "稳定更新频率确实有用，算法喜欢活跃的账号", False),
        (GROWTH, 10, "08:40", "wxid_zhao", 1, "@阿栗 你的排期表能发我一份吗 https://example.com/plan?utm_source=wx", False),
        (GROWTH, 10, "12:00", "wxid_he", 1, "中午吃什么", False),
        (HIKE, 1, "19:00", "wxid_sun", 1, "周六去凤凰岭，早上 7 点地铁站集合", False),
        (HIKE, 1, "19:05", "wxid_zhao", 1, "报名！要带登山杖吗", False),
        (HIKE, 1, "19:10", SELF, 1, "我也去，帮我带瓶水", False),
        (CHEN, 2, "10:00", CHEN, 1, "周末有空吗", False),
        (CHEN, 2, "10:30", SELF, 1, "有，怎么了", False),
        ("gh_news", 2, "11:00", "gh_news", 1, "今日资讯：涨粉神器限时优惠", False),
    ]
    lin_lines = [
        "哈哈哈哈你又熬夜了吧", "今天加班到十点，累死了", "周末去爬山吗？我想去香山", "这家咖啡店不错，下次带你去",
        "最近在学剪视频，感觉好难呢", "哈哈好的好的", "你推荐的那本书我看完了，结尾太好哭了", "收到~",
        "明天早上九点见吧", "我觉得还是先别辞职，稳一点", "嗯嗯，有道理", "最近压力有点大，项目一直延期",
        "哈哈哈笑死我了", "下班一起吃火锅？", "好呀好呀", "我又去拍了日落，发你看看",
    ]
    me_lines = ["还行吧", "好啊", "哈哈", "去哪？", "可以", "加油", "辛苦了", "嗯"]
    for k in range(64):
        day = 15 + (k // 2) * 2
        hour = 22 if k % 5 == 0 else (12 + k % 9)
        sender = LIN if k % 2 == 0 else SELF
        text = lin_lines[(k // 2) % len(lin_lines)] if sender == LIN else me_lines[k % len(me_lines)]
        out.append((LIN, day, f"{hour:02d}:{(k * 7) % 60:02d}", sender, 1, text, False))
    return out


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _msg_db(path: Path, users: list[str]) -> dict[str, int]:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE Name2Id (user_name TEXT PRIMARY KEY, is_session INTEGER)")
    ids = {u: con.execute("INSERT INTO Name2Id(user_name, is_session) VALUES (?, 1)", (u,)).lastrowid for u in users}
    con.commit()
    con.close()
    return ids


def _table(con: sqlite3.Connection, table: str) -> None:
    con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (local_id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER, '
                "local_type INTEGER, sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, status INTEGER, "
                "message_content TEXT, WCDB_CT_message_content INTEGER DEFAULT NULL)")


def build(out: Path, extra: list[tuple] | None = None) -> Path:
    """生成镜像，返回 decrypted 根目录（out）。extra 追加到 message_1（用于测试增量刷新）。"""
    storage = out / ACCOUNT_DIR / "db_storage"
    for sub in ("contact", "session", "message"):
        (storage / sub).mkdir(parents=True, exist_ok=True)
    for f in storage.rglob("*.db"):
        f.unlink()
    con = sqlite3.connect(storage / "contact" / "contact.db")
    con.execute("CREATE TABLE contact (id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, alias TEXT, "
                "remark TEXT, nick_name TEXT)")
    con.execute("CREATE TABLE chat_room (id INTEGER PRIMARY KEY, username TEXT, owner TEXT, ext_buffer BLOB)")
    con.execute("CREATE TABLE chatroom_member (room_id INTEGER, member_id INTEGER)")
    cids = {}
    for u, (nick, remark) in PEOPLE.items():
        cids[u] = con.execute("INSERT INTO contact(username, local_type, alias, remark, nick_name) VALUES (?,?,?,?,?)",
                              (u, 1, "", remark, nick)).lastrowid
    for gid, (name, members) in GROUPS.items():
        con.execute("INSERT INTO contact(username, local_type, alias, remark, nick_name) VALUES (?,?,?,?,?)",
                    (gid, 2, "", "", name))
        room = con.execute("INSERT INTO chat_room(username, owner, ext_buffer) VALUES (?,?,?)",
                           (gid, next(iter(members)), encode_chatroom_ext(members))).lastrowid
        for m in members:
            con.execute("INSERT INTO chatroom_member(room_id, member_id) VALUES (?,?)", (room, cids[m]))
    con.commit()
    con.close()
    sqlite3.connect(storage / "session" / "session.db").close()
    users = list(PEOPLE) + list(GROUPS)
    ids = {0: _msg_db(storage / "message" / "message_0.db", users), 1: _msg_db(storage / "message" / "message_1.db", users)}
    cons = {i: sqlite3.connect(storage / "message" / f"message_{i}.db") for i in ids}
    svr = 7_000_000
    for r in rows() + list(extra or []):
        conv, day, hm, sender, ltype, content, compress = r
        svr += 1
        shard = 1 if conv == HIKE or r in (extra or []) else 0
        con = cons[shard]
        table = f"Msg_{_md5(conv)}"
        _table(con, table)
        hh, mm = map(int, hm.split(":"))
        ts = int((START + timedelta(days=day, hours=hh, minutes=mm)).timestamp())
        body = content if (sender == SELF or not conv.endswith("@chatroom")) else f"{sender}:\n{content}"
        value, flag = (zstd_compress(body.encode("utf-8")), 4) if compress else (body, None)
        con.execute(f'INSERT INTO "{table}"(server_id, local_type, sort_seq, real_sender_id, create_time, status, '
                    "message_content, WCDB_CT_message_content) VALUES (?,?,?,?,?,?,?,?)",
                    (svr, ltype, ts * 1000, ids[shard].get(sender, 0), ts, 2, value, flag))
    for con in cons.values():
        con.commit()
        con.close()
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
