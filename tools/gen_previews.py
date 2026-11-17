"""生成 README 预览图：用测试里的虚构数据跑一遍日报与人格报告，再用本机 Chrome / Edge 无头截图。

用法：
    python -X utf8 tools/gen_previews.py            # 生成 docs/images/preview-*.png
    python -X utf8 tools/gen_previews.py --inspect  # 只打印素材，便于调整示例语义稿

全部数据来自 tests/python/fixtures.py 的虚构会话，不读取任何真实微信数据。
示例语义稿里的结论和引文都取自虚构素材本身，渲染前同样要过证据校验。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "tests" / "python"))

# 与测试一致：不读取本机 wechat-summary 的真实配置
os.environ["WS_CONFIG"] = str(Path(tempfile.gettempdir()) / "dsh-wechat-one-previews-no-config.yaml")

import fixtures  # noqa: E402
from wechat_one import features  # noqa: E402,F401
from wechat_one.core.dispatch import handle  # noqa: E402

OUT = ROOT / "docs" / "images"
BROWSERS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


class Engine:
    def __init__(self, tmp: Path):
        mirror = fixtures.build(tmp / "decrypted")
        self.state = str(tmp / "state")
        self.config = {"sourceType": "decrypted_dir", "sourcePath": str(mirror), "meNames": ["阿栗"],
                       "groupSets": {"增长": ["增长交流群"]}}
        self.ok("refresh", {"reindex_only": True})

    def ok(self, action: str, params: dict) -> dict:
        res = handle({"action": action, "params": params, "config": self.config}, self.state)
        if not res["ok"]:
            raise SystemExit(f"{action} 失败：{json.dumps(res['error'], ensure_ascii=False)}")
        return res["data"]


def pick(messages: list[dict], needle: str) -> dict:
    for m in messages:
        if needle in m["text"]:
            return m
    raise SystemExit(f"虚构素材里找不到：{needle}")


def quote(m: dict, needle: str) -> dict:
    return {"msg_id": m["id"], "text": needle}


def summary_semantic(prep: dict) -> dict:
    msgs = prep["material"]["messages"]
    vertical = pick(msgs, "起号阶段最重要的是垂直")
    camping = pick(msgs, "只发露营装备")
    hook = pick(msgs, "前 3 秒要直接给结论")
    reply = pick(msgs, "评论区要主动回")
    lead = pick(msgs, "别直接留联系方式")
    fans = pick(msgs, "我反对买粉")
    plan = pick(msgs, "排期表能发我一份")
    cadence = pick(msgs, "固定每天晚上 8 点更新")
    semantic = {
        "one_liner": "本月围绕起号展开：垂直定位、前三秒给结论、稳定更新三条经验得到多人验证。",
        "mood": "务实互助",
        "editor_note": "以下内容来自虚构的示例数据，仅用于展示报告版式。",
        "for_me": [{"title": "小满想要你的更新排期表", "action": "回复", "evidence": [plan["id"]],
                    "quotes": [quote(plan, "你的排期表能发我一份吗")]}],
        "topics": [
            {"title": "起号先做垂直，账号标签清晰才有推荐", "summary": "舟舟提出起号阶段最重要的是垂直，小满用两周只发露营装备的经历佐证。",
             "points": ["内容方向保持单一", "标签乱了算法不知道推给谁"], "people": ["舟舟", "小满"],
             "evidence": [vertical["id"], camping["id"]],
             "quotes": [quote(vertical, "起号阶段最重要的是垂直"), quote(camping, "涨粉明显快了")]},
            {"title": "前三秒放成品，完播率从 18% 涨到 35%", "summary": "舟舟分享把开头改成先放成品后的数据变化，何川当场记下。",
             "points": ["开头直接给结论或冲突", "完播率提升带来更大流量池"], "people": ["舟舟", "何川"],
             "evidence": [hook["id"]], "quotes": [quote(hook, "完播率从 18% 涨到 35%")]},
            {"title": "固定晚 8 点更新，一个月涨到 3000 粉", "summary": "阿栗分享稳定更新频率的效果，舟舟补充算法偏好活跃账号。",
             "points": ["固定时间更新", "评论区主动回复提升互动率"], "people": ["阿栗", "舟舟"],
             "evidence": [cadence["id"], reply["id"]], "quotes": [quote(cadence, "一个月从 500 涨到 3000 粉")]},
        ],
        "insights": [
            {"claim": "私域引流别在主页直接留联系方式", "stance": "warning", "detail": "改用置顶笔记或群聊入口，避免被判违规限流。",
             "evidence": [lead["id"]], "quotes": [quote(lead, "别直接留联系方式")]},
            {"claim": "买粉会拉低互动率，得不偿失", "stance": "consensus", "evidence": [fans["id"]],
             "quotes": [quote(fans, "我反对买粉")]},
        ],
        "follow_ups": [{"title": "把更新排期表发给小满", "owner": "阿栗", "evidence": [plan["id"]]}],
        "links": [{"url": l["url"], "note": "标题公式合集，适合对照改自己的笔记标题", "evidence": [l["msg_id"]]}
                  for l in prep["stats"]["links"][:1]],
    }
    if "daily" in prep["schema"]["sections"]:
        semantic["daily"] = [
            {"date": "2026-08-03", "headline": "垂直定位与完播率", "points": ["只发一个方向", "前三秒给结论"],
             "evidence": [vertical["id"], hook["id"]]},
            {"date": "2026-08-06", "headline": "私域引流与买粉之争", "points": ["别在主页留联系方式", "买粉拉低互动率"],
             "evidence": [lead["id"], fans["id"]]},
            {"date": "2026-08-13", "headline": "稳定更新频率", "points": ["固定晚 8 点更新"], "evidence": [cadence["id"]]},
        ]
    return semantic


def persona_semantic(prep: dict) -> dict:
    msgs = [m for m in prep["material"]["messages"] if m["role"] == "对方"]

    def item(needle: str, title: str, detail: str, cut: str) -> dict:
        m = pick(msgs, needle)
        return {"title": title, "detail": detail, "evidence": [m["id"]], "quotes": [quote(m, cut)]}

    laugh = pick(msgs, "哈哈哈笑死我了")
    return {
        "one_liner": "爱笑、爱分享生活，会坦率说累的老朋友",
        "summary": "舟舟在聊天里语气轻松，常用“哈哈”接话；经常主动约爬山、约吃饭、分享日落照片。"
                   "工作压力大时会直接说出来，在辞职这类决定上倾向稳妥。以下为虚构示例数据。",
        "caveats": ["示例数据只覆盖约两个月", "只看文字消息，未包含语音和图片"],
        "communication_style": [item("哈哈哈哈你又熬夜了吧", "轻松调侃", "用玩笑开启话题，关心藏在调侃里。", "你又熬夜了吧")],
        "interests": [item("周末去爬山吗", "户外与拍照", "多次主动提出爬山，也会分享拍到的日落。", "周末去爬山吗"),
                      item("在学剪视频", "在学新技能", "提到正在学剪视频，觉得难但在坚持。", "最近在学剪视频")],
        "emotional_expression": [item("累死了", "坦率表达疲惫", "加班、项目延期带来的压力会直接说出来。", "今天加班到十点")],
        "interaction_pattern": [item("下班一起吃火锅", "主动发起邀约", "约饭、约爬山多由舟舟先开口。", "下班一起吃火锅")],
        "catchphrases": [{"phrase": "哈哈", "detail": "表达认同或缓和语气", "evidence": [laugh["id"]],
                          "quotes": [quote(laugh, "哈哈哈笑死我了")]}],
    }


def browser() -> Path:
    for b in BROWSERS:
        if b.exists():
            return b
    raise SystemExit("没找到 Chrome 或 Edge，无法截图")


def shoot(html: Path, png: Path, width: int, height: int) -> None:
    profile = Path(tempfile.mkdtemp(prefix="wo-shot-"))
    try:
        subprocess.run([str(browser()), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={profile}", "--force-device-scale-factor=1",
                        f"--window-size={width},{height}", f"--screenshot={png}", html.as_uri()],
                       check=True, capture_output=True, timeout=120)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    print("wrote", png.relative_to(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="wo-previews-"))
    try:
        eng = Engine(tmp)
        prep = eng.ok("summary_prepare", {"group_set": "增长", "period": "monthly", "date": "2026-08-20"})
        lin = next(c["conversation_id"] for c in eng.ok("contacts", {"query": "舟舟"})["conversations"])
        pprep = eng.ok("persona_prepare", {"conversation_id": lin})
        if args.inspect:
            print(json.dumps({"window": prep["window"], "sections": prep["schema"]["sections"],
                              "links": prep["stats"]["links"], "days": [d["date"] for d in prep["stats"]["daily"]]},
                             ensure_ascii=False, indent=1))
            for m in prep["material"]["messages"]:
                print(m["id"], m["t"], m["from"], m["text"][:50])
            return
        OUT.mkdir(parents=True, exist_ok=True)
        semantic = summary_semantic(prep)
        psemantic = persona_semantic(pprep)
        for theme in ("claude", "newspaper"):
            s = eng.ok("summary_render", {"run_id": prep["run_id"], "semantic": semantic, "theme": theme})
            shoot(Path(s["report_html"]), OUT / f"preview-summary-{theme}.png", 900, 1500)
            p = eng.ok("persona_render", {"run_id": pprep["run_id"], "semantic": psemantic, "theme": theme})
            shoot(Path(p["report_html"]), OUT / f"preview-persona-{theme}.png", 900, 1500)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
