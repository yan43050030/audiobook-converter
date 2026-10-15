"""CosyVoice 桥接子进程的管理端。

* 环境解析：用哪个 Python（`cosyvoice_python`，未设置时源码运行用当前解释器）、CosyVoice 仓库在哪
  （`cosyvoice_repo`）、用哪个模型目录（`clone_model_dir`，未设置时在存储目录 cosyvoice-models/
  下自动找 CosyVoice3 / CosyVoice2 模型）。
* `CloneBridge`：常驻子进程，模型只加载一次；逐行 JSON 请求/响应，按 id 配对；超时 / 进程退出 /
  用户暂停时终止子进程（下次调用自动重启）。子进程的 stderr 写入日志文件，避免管道写满卡死。
"""

import atexit
import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Optional, Tuple

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_worker.py")
PING_TIMEOUT = 120
LOAD_TIMEOUT = 900
TTS_TIMEOUT = 1800
LOG_NAME = "clone-bridge.log"

# 模型目录识别：yaml 文件名 → 型号（越靠前越优先）
_MODEL_MARKERS = (("cosyvoice3.yaml", "CosyVoice3"), ("cosyvoice2.yaml", "CosyVoice2"),
                  ("cosyvoice.yaml", "CosyVoice"))


class CloneBridgeError(RuntimeError):
    """桥接 / 环境问题（信息可直接展示给用户）。"""


# ---------------- 环境解析 ----------------

def _cfg() -> dict:
    import tts_engine as _te
    return _te._load_config()


def resolve_python() -> str:
    py = (_cfg().get("cosyvoice_python") or "").strip()
    if py:
        return py
    # 源码运行时默认用当前解释器（已在同一环境装好 CosyVoice 的情形）；打包版必须显式配置
    return "" if getattr(sys, "frozen", False) else sys.executable


def resolve_repo() -> str:
    return (_cfg().get("cosyvoice_repo") or "").strip()


def model_type(model_dir: str) -> Optional[str]:
    for marker, kind in _MODEL_MARKERS:
        if os.path.isfile(os.path.join(model_dir, marker)):
            return kind
    return None


def _cloning_capable(model_dir: str) -> bool:
    """CosyVoice 1 的 SFT / Instruct 模型不支持零样本复刻，排除。"""
    kind = model_type(model_dir)
    if kind is None:
        return False
    if kind == "CosyVoice":
        base = os.path.basename(os.path.normpath(model_dir)).lower()
        return "sft" not in base and "instruct" not in base
    return True


def resolve_model_dir(storage_dir: Optional[str] = None) -> str:
    configured = (_cfg().get("clone_model_dir") or "").strip()
    if configured:
        return configured if os.path.isdir(configured) else ""
    import tts_engine as _te
    root = os.path.join(storage_dir or _te.get_storage_dir(), "cosyvoice-models")
    if not os.path.isdir(root):
        return ""
    candidates = [os.path.join(root, d) for d in sorted(os.listdir(root))
                  if os.path.isdir(os.path.join(root, d))]
    for marker, _kind in _MODEL_MARKERS:
        for c in candidates:
            if os.path.isfile(os.path.join(c, marker)) and _cloning_capable(c):
                return c
    return ""


def readiness(storage_dir: Optional[str] = None) -> Tuple[bool, str]:
    """静态检查（不启动子进程）：解释器、模型、音色档案是否都有。"""
    from audiobook.cloning import profiles
    py = resolve_python()
    if not py:
        return False, ("尚未配置 CosyVoice 的 Python 解释器。\n"
                       "请在「声音克隆」设置里指定装好 CosyVoice 的 python（conda / venv 中的 python）。")
    if os.path.isabs(py) and not os.path.isfile(py):
        return False, f"配置的 Python 解释器不存在：{py}"
    md = resolve_model_dir(storage_dir)
    if not md:
        return False, ("未找到可用于克隆的 CosyVoice 模型（Fun-CosyVoice3-0.5B / CosyVoice2-0.5B）。\n"
                       "请把模型放到存储目录的 cosyvoice-models/ 下，或在设置里指定模型目录。")
    n = len(profiles.list_profiles(storage_dir))
    if n == 0:
        return False, "还没有克隆音色：先用一段 5–15 秒的清晰人声创建一个。"
    return True, f"声音克隆可用（{model_type(md)}，{n} 个音色）"


# ---------------- 桥接进程 ----------------

class CloneBridge:
    def __init__(self, python: str, repo: str = "", log_path: Optional[str] = None):
        self.python = python
        self.repo = repo
        self.log_path = log_path
        self.proc: Optional[subprocess.Popen] = None
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._log = None

    # -- 生命周期 --
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if not os.path.isfile(WORKER):
            raise CloneBridgeError(f"找不到声音克隆桥接脚本：{WORKER}（安装包不完整？）")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        if self.repo:
            parts = [self.repo, os.path.join(self.repo, "third_party", "Matcha-TTS")]
            if env.get("PYTHONPATH"):
                parts.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(parts)
        args = [self.python, "-u", WORKER] + (["--repo", self.repo] if self.repo else [])
        self._log = open(self.log_path, "a", encoding="utf-8") if self.log_path else subprocess.DEVNULL
        import tts_engine as _te
        try:
            self.proc = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
                env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=self.repo or None, **_te._quiet_popen_kwargs())
        except OSError as e:
            raise CloneBridgeError(f"无法启动 CosyVoice 桥接进程（{self.python}）：{e}")
        self._q = queue.Queue()
        threading.Thread(target=self._reader, args=(self.proc, self._q), daemon=True).start()

    @staticmethod
    def _reader(proc, q):
        try:
            for line in proc.stdout:
                q.put(line)
        except Exception:
            pass
        q.put(None)  # EOF

    def close(self, force: bool = False) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            if not force:
                try:
                    proc.stdin.write(json.dumps({"id": 0, "cmd": "quit"}) + "\n")
                    proc.stdin.flush()
                    proc.wait(timeout=3)
                except Exception:
                    pass
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
        if self._log not in (None, subprocess.DEVNULL):
            try:
                self._log.close()
            except Exception:
                pass
        self._log = None

    def _log_tail(self, n: int = 1500) -> str:
        if not self.log_path or not os.path.isfile(self.log_path):
            return ""
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                f.seek(max(0, os.path.getsize(self.log_path) - n))
                return f.read().strip()
        except Exception:
            return ""

    # -- 请求 --
    def request(self, cmd: str, timeout: float, should_stop=None, **payload) -> dict:
        with self._lock:
            if not self.alive():
                self.start()
            rid = next(self._ids)
            try:
                self.proc.stdin.write(json.dumps(dict(payload, id=rid, cmd=cmd), ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                tail = self._log_tail()
                self.close(force=True)
                raise CloneBridgeError("CosyVoice 桥接进程已退出。" + (f"\n日志：\n{tail}" if tail else ""))

            deadline = time.monotonic() + timeout
            while True:
                if should_stop is not None and should_stop():
                    # 推理无法中途取消：直接结束子进程，下次调用会自动重启并重新加载模型
                    self.close(force=True)
                    import tts_engine as _te
                    raise _te.StopRequested("用户暂停")
                if time.monotonic() > deadline:
                    self.close(force=True)
                    raise CloneBridgeError(f"CosyVoice 响应超时（{cmd}，{int(timeout)} 秒）")
                try:
                    line = self._q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if line is None:
                    tail = self._log_tail()
                    self.close(force=True)
                    raise CloneBridgeError("CosyVoice 桥接进程意外退出。"
                                           + (f"\n日志：\n{tail}" if tail else ""))
                try:
                    resp = json.loads(line)
                except ValueError:
                    continue  # 非协议输出（理论上已被导到 stderr），忽略
                if resp.get("id") != rid:
                    continue
                if not resp.get("ok"):
                    raise CloneBridgeError(resp.get("error") or "CosyVoice 处理失败")
                return resp


_bridge: Optional[CloneBridge] = None
_bridge_key = None
_bridge_lock = threading.Lock()


def get_bridge() -> CloneBridge:
    """按当前配置取（必要时重建）常驻桥接进程。"""
    global _bridge, _bridge_key
    py = resolve_python()
    if not py:
        raise CloneBridgeError(readiness()[1])
    key = (py, resolve_repo())
    with _bridge_lock:
        if _bridge is not None and _bridge_key != key:
            _bridge.close()
            _bridge = None
        if _bridge is None:
            import tts_engine as _te
            _bridge = CloneBridge(py, key[1], log_path=os.path.join(_te.get_storage_dir(), LOG_NAME))
            _bridge_key = key
        return _bridge


def shutdown() -> None:
    global _bridge, _bridge_key
    with _bridge_lock:
        if _bridge is not None:
            _bridge.close()
        _bridge, _bridge_key = None, None


atexit.register(shutdown)


# ---------------- 高层接口 ----------------

def synthesize(profile, text: str, out_wav: str, speed: float = 1.0, should_stop=None) -> dict:
    """用某个音色档案合成一段文本到 WAV。"""
    md = resolve_model_dir()
    if not md:
        raise CloneBridgeError(readiness()[1])
    try:
        stamp = int(os.path.getmtime(os.path.join(profile.path, "profile.json")))
    except OSError:
        stamp = 0
    return get_bridge().request(
        "tts", TTS_TIMEOUT, should_stop,
        model_dir=md, text=text, prompt_wav=profile.wav, prompt_text=profile.prompt_text,
        # 文字稿改动后 mtime 变化 → 换一个 key，桥接端会重新登记说话人
        speaker_key=f"{profile.id}@{stamp}", speed=float(speed), out=out_wav)


def check(load: bool = False) -> dict:
    """诊断环境：返回各项检查结果（启动子进程执行 ping，可选加载模型）。"""
    report = {"python": resolve_python(), "repo": resolve_repo(),
              "model_dir": resolve_model_dir(), "ping": None, "load": None, "error": ""}
    report["model_type"] = model_type(report["model_dir"]) if report["model_dir"] else None
    try:
        br = get_bridge()
        report["ping"] = br.request("ping", PING_TIMEOUT)
        if load and report["model_dir"] and report["ping"].get("cosyvoice"):
            report["load"] = br.request("load", LOAD_TIMEOUT, model_dir=report["model_dir"])
    except CloneBridgeError as e:
        report["error"] = str(e)
    return report
