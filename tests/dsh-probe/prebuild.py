"""探针前置：在合成镜像上直接用引擎建好索引（不经过 DSH 审批）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from wechat_one import features  # noqa: E402,F401
from wechat_one.core.dispatch import handle  # noqa: E402

tmp = Path(sys.argv[1])
res = handle({"action": "refresh", "params": {"reindex_only": True}, "config": {"sourcePath": str(tmp / "decrypted")}},
             str(tmp / "state"))
print(json.dumps({"ok": res["ok"], "messages": res.get("data", {}).get("messages")}))
sys.exit(0 if res["ok"] else 1)
