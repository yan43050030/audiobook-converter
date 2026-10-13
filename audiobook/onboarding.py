"""audiobook.onboarding — 首次运行向导的场景与判定逻辑（v6.0/C2-U2）。

向导流程：选使用场景 → 自动下载该场景缺的模型 → 试听验证 → 记住所选引擎。
本模块只放纯逻辑（场景定义、还缺哪些模型、何时自动弹出、完成后写配置），界面在
`gui_pyside6`。模型下载复用 `audiobook.models.download`。
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from audiobook import models as M


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    engine: str                              # 完成后默认选中的引擎
    models: Tuple[Tuple[str, str], ...] = ()  # 需要的模型 (kind, id)
    size_hint: str = ""                      # 下载体积提示


SCENARIOS: Tuple[Scenario, ...] = (
    Scenario(
        "online", "联网 · 音质最好",
        "使用微软 Edge 在线语音，自然度最高，无需下载模型；生成时需要能访问互联网。",
        engine="edge", size_hint="无需下载",
    ),
    Scenario(
        "offline", "完全离线",
        "下载 Piper 中文神经语音，断网也能生成，音质较好。",
        engine="piper", models=((M.KIND_PIPER, "zh_CN-huayan-medium"),), size_hint="约 60 MB",
    ),
    Scenario(
        "offline_asr", "离线 + 语音转文字",
        "在「完全离线」基础上再下载 Whisper base 识别模型，可把音频转回文字 / 字幕。",
        engine="piper",
        models=((M.KIND_PIPER, "zh_CN-huayan-medium"), (M.KIND_WHISPER, "base")),
        size_hint="约 360 MB",
    ),
    Scenario(
        "system", "系统自带语音（零下载）",
        "直接使用操作系统自带的语音（macOS / Windows 内置，Linux 需 espeak-ng），音质一般。",
        engine="local", size_hint="无需下载",
    ),
)


def get(scenario_id: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    raise KeyError(scenario_id)


def pending_downloads(scenario: Scenario, storage_dir: Optional[str] = None) -> List[M.DownloadItem]:
    """该场景还需要下载的模型（已安装的不再列出），顺序与场景定义一致。"""
    if not scenario.models:
        return []
    available = {(it.kind, it.id): it for it in M.downloadable(storage_dir)}
    out = []
    for key in scenario.models:
        item = available.get(key)
        if item is not None and not item.installed:
            out.append(item)
    return out


def availability(scenario: Scenario) -> Tuple[bool, str]:
    """该场景的引擎软件是否已装好（模型缺了可以下载，引擎本身缺了向导装不了）。

    返回 (可选, 说明首行)。
    """
    import tts_engine as _te
    try:
        ok, msg = _te.check_engine_ready(scenario.engine)
    except Exception as e:  # 检测本身出错时视为不可用，不让向导崩掉
        ok, msg = False, str(e)
    lines = (msg or "").strip().splitlines()
    return bool(ok), (lines[0] if lines else "")


def recommend() -> str:
    """推荐的默认场景：联网可用就推荐联网（零下载、音质好），其次完全离线，再次系统语音。"""
    for sid in ("online", "offline", "system"):
        if availability(get(sid))[0]:
            return sid
    return "online"


def should_show(cfg: Optional[dict] = None) -> bool:
    """是否在启动时自动弹出向导。

    已完成过向导的不弹；老用户（配置里已有窗口位置记忆，说明用过本程序）也不自动弹，
    以免升级后被打扰——他们仍可从「存储与依赖」面板手动打开。
    """
    if cfg is None:
        import tts_engine as _te
        cfg = _te._load_config()
    if cfg.get("wizard_completed"):
        return False
    return not cfg.get("window_geometry")


def mark_completed(engine: Optional[str] = None) -> None:
    """记录已完成向导；给定 engine 时同时设为默认引擎。"""
    import tts_engine as _te
    cfg = _te._load_config()
    cfg["wizard_completed"] = True
    if engine:
        cfg["default_engine"] = engine
    _te._save_config(cfg)
