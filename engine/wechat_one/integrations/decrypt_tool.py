"""WeinanHu/wechat-decrypt 适配器：探测、安装、取钥、解密。

本模块只编排外部工具（git clone 固定版本、创建其虚拟环境、调用其脚本），不包含任何取钥或解密实现。
外部脚本的输出只写本地脱敏日志，密钥永不进入返回值。
编排方式移植自 Andrew-liu/wechat-summary（MIT）wechat_summary/setup_env.py。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from ..core.config import Settings
from ..core.errors import EngineError
from ..core.log import Logger
from ..core.state import State

EXTRACT_SCRIPT = "scripts/windows/extract_raw_key.py"
DECRYPT_SCRIPT = "scripts/windows/decrypt_all.py"
KEY_FILE = "key_windows.txt"
PIP_DEPS = ["frida", "pycryptodome", "zstandard"]
HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _scrub(text: str) -> str:
    return HEX_RE.sub("[redacted]", text or "")


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def venv_python(tool_dir: Path) -> Path:
    return tool_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def tool_ready(tool_dir: Path | None) -> bool:
    return bool(tool_dir) and (tool_dir / EXTRACT_SCRIPT).is_file() and (tool_dir / DECRYPT_SCRIPT).is_file()


def default_dir(state: State) -> Path:
    return state.root / "wechat-decrypt"


def locate(settings: Settings, state: State) -> tuple[Path | None, str]:
    """按 插件配置 → 本地设置 → ~/.wechat-report → 默认目录 的顺序探测已有安装。"""
    if settings.decrypt_tool_dir:
        path = Path(settings.decrypt_tool_dir).expanduser()
        if tool_ready(path):
            return path, settings.origin.get("decrypt_tool_dir", "config")
    path = default_dir(state)
    if tool_ready(path):
        return path, "default"
    return None, ""


def key_ready(tool_dir: Path | None, newer_than: float = 0) -> bool:
    if not tool_dir:
        return False
    path = tool_dir / KEY_FILE
    try:
        if path.stat().st_mtime <= newer_than:
            return False
        text = path.read_text(encoding="ascii", errors="ignore").strip()
    except OSError:
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", text))


def mirror_dir(tool_dir: Path) -> Path:
    return tool_dir / "decrypted"


# ------------------------------------------------------------------ 安装
def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=_child_env(), creationflags=_CREATE_NO_WINDOW)


def _download_zip(repo: str, ref: str, dest: Path) -> None:
    base = repo[:-4] if repo.endswith(".git") else repo
    url = f"{base}/archive/{ref}.zip"
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - 固定的 GitHub 地址
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        prefix = zf.namelist()[0].split("/")[0] + "/"
        for member in zf.infolist():
            rel = member.filename[len(prefix):]
            if not rel or ".." in Path(rel).parts:
                continue
            target = dest / rel
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))


def install(settings: Settings, state: State, log: Logger) -> dict[str, Any]:
    if os.name != "nt":
        raise EngineError("PLATFORM_UNSUPPORTED")
    found, origin = locate(settings, state)
    if found is not None:
        ensure_venv(found, log)
        return {"status": "ready", "detail": "已检测到 wechat-decrypt（来源：" + origin + "），已复用。"}
    if not settings.allow_decrypt_install:
        raise EngineError("DECRYPT_INSTALL_DISABLED")
    target = default_dir(state)
    if target.exists() and any(target.iterdir()):
        raise EngineError("SETUP_CONFLICT")
    target.parent.mkdir(parents=True, exist_ok=True)
    ref = settings.decrypt_tool_ref
    if shutil.which("git"):
        proc = _run(["git", "clone", "--quiet", settings.decrypt_repo, str(target)], timeout=600)
        if proc.returncode != 0:
            log.error("decrypt_install_clone", stderr=_scrub(proc.stderr[-800:]))
            raise EngineError("SETUP_FAILED", "git clone wechat-decrypt 失败，请检查网络后重试。", public=True)
        proc = _run(["git", "-c", "advice.detachedHead=false", "checkout", "--quiet", ref], cwd=target, timeout=120)
        if proc.returncode != 0:
            log.error("decrypt_install_checkout", stderr=_scrub(proc.stderr[-800:]))
            raise EngineError("SETUP_FAILED", "无法切换到固定版本 " + ref[:12] + "，上游可能已变化。", public=True)
    else:
        target.mkdir(parents=True, exist_ok=True)
        try:
            _download_zip(settings.decrypt_repo, ref, target)
        except Exception as exc:  # noqa: BLE001
            log.error("decrypt_install_zip", error=repr(exc))
            raise EngineError("SETUP_FAILED", "下载 wechat-decrypt 源码包失败，请检查网络后重试。", public=True) from exc
    if not tool_ready(target):
        raise EngineError("SETUP_FAILED", "wechat-decrypt 缺少预期脚本，上游结构可能已变化。", public=True)
    ensure_venv(target, log)
    state.update_settings(decryptToolDir=str(target))
    return {"status": "installed", "detail": "已安装 wechat-decrypt @" + ref[:12] + " 并创建虚拟环境。"}


def ensure_venv(tool_dir: Path, log: Logger) -> Path:
    py = venv_python(tool_dir)
    if not py.is_file():
        proc = _run([sys.executable, "-m", "venv", str(tool_dir / ".venv")], timeout=300)
        if proc.returncode != 0 or not py.is_file():
            log.error("decrypt_venv", stderr=_scrub(proc.stderr[-800:]))
            raise EngineError("SETUP_FAILED", "创建 wechat-decrypt 虚拟环境失败。", public=True)
    check = _run([str(py), "-c", "import frida, Crypto, zstandard"], timeout=60)
    if check.returncode != 0:
        proc = _run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", *PIP_DEPS], timeout=1200)
        if proc.returncode != 0:
            log.error("decrypt_pip", stderr=_scrub(proc.stderr[-1200:]))
            raise EngineError("SETUP_FAILED", "安装 wechat-decrypt 依赖失败（可尝试 Python 3.10~3.13 或设置 PIP_INDEX_URL）。",
                              public=True)
    return py


# ------------------------------------------------------------------ 取钥 / 解密
def extract_key(tool_dir: Path, log: Logger, wait_seconds: int = 180,
                check: Callable[[], None] = lambda: None) -> bool:
    if os.name != "nt":
        raise EngineError("PLATFORM_UNSUPPORTED")
    py = venv_python(tool_dir)
    if not py.is_file():
        raise EngineError("DECRYPT_TOOL_MISSING")
    before = (tool_dir / KEY_FILE).stat().st_mtime if (tool_dir / KEY_FILE).exists() else 0.0
    proc = subprocess.Popen([str(py), "-u", EXTRACT_SCRIPT, str(wait_seconds)], cwd=str(tool_dir),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=_child_env())
    tail: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = _scrub(line.rstrip())
            if line:
                tail.append(line)
                del tail[:-15]

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.time() + wait_seconds + 60
    try:
        while time.time() < deadline and proc.poll() is None:
            check()
            if key_ready(tool_dir, before):
                for _ in range(10):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.5)
                break
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
        reader.join(2)
    ok = key_ready(tool_dir, before)
    log.info("decrypt_extract", ok=ok, tail=tail[-6:])
    return ok


def decrypt(tool_dir: Path, log: Logger, timeout: int = 1500) -> dict[str, Any]:
    py = venv_python(tool_dir)
    if not py.is_file():
        raise EngineError("DECRYPT_TOOL_MISSING")
    if not key_ready(tool_dir):
        raise EngineError("DECRYPT_KEY_MISSING")
    try:
        proc = _run([str(py), DECRYPT_SCRIPT], cwd=tool_dir, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise EngineError("TIMEOUT") from exc
    out = proc.stdout or ""
    m = re.search(r"(\d+) decrypted, (\d+) skipped", out)
    log.info("decrypt_run", returncode=proc.returncode, tail=_scrub("\n".join(out.strip().splitlines()[-6:])),
             stderr=_scrub((proc.stderr or "")[-400:]))
    if proc.returncode != 0:
        raise EngineError("DECRYPT_FAILED")
    return {"decrypted": int(m.group(1)) if m else None, "skipped": int(m.group(2)) if m else None}
