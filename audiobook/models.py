"""audiobook.models — 统一模型管理（v6.0/C2-U1 模型管理器）。

把分散在各处的 Piper 语音包 / CosyVoice 模型 / Whisper 模型统一成一份清单，提供：

* `list_installed()`：盘点已安装模型（类型 / 名称 / 占用空间 / 位置 / 是否可删）；
* `delete_model()`：删除一个模型（仅限便携存储目录内的；先卸载缓存实例再删，删后刷新语音列表）；
* `downloadable()` / `download()`：常用可下载项的统一入口，分发到既有下载实现
  （Piper 内置语音 / CosyVoice / Whisper）。首次运行向导复用同一入口。

磁盘布局（均位于便携存储目录下）：

    piper-models/**/<voice>.onnx (+ .onnx.json)     Piper 语音包；另含用户「添加模型文件夹」的外部路径
    cosyvoice-models/<model>/                         CosyVoice 模型（解压目录）
    whisper-models/<size>/                            Whisper 模型（预下载布局，含 .complete）
    whisper-models/models--Systran--faster-whisper-<size>/   faster-whisper 自带 HF 缓存布局

安全约束：只有真实路径位于存储目录内的模型才标记为可删（`managed=True`）；用户添加的外部
模型文件夹只读展示，不会被删除。对 `tts_engine` / ASR 的引用在函数内惰性导入。
"""

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, List, Optional


KIND_PIPER = "piper"
KIND_COSYVOICE = "cosyvoice"
KIND_WHISPER = "whisper"

KIND_LABELS = {
    KIND_PIPER: "Piper 语音包",
    KIND_COSYVOICE: "CosyVoice 模型",
    KIND_WHISPER: "Whisper 模型",
}
KIND_ORDER = (KIND_PIPER, KIND_COSYVOICE, KIND_WHISPER)


@dataclass
class ModelEntry:
    kind: str                       # KIND_*
    id: str                         # 类型内唯一标识（Piper 语音 id / 目录名）
    name: str                       # 展示名
    paths: List[str] = field(default_factory=list)  # 构成该模型的文件/目录
    size_bytes: int = 0
    managed: bool = True            # 位于存储目录内 → 可删除
    location: str = ""              # 展示用位置（所在目录）

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class DownloadItem:
    kind: str
    id: str
    label: str
    installed: bool = False


# ---------------- 工具 ----------------

def format_size(n: int) -> str:
    """字节数 → 人类可读（B/KB/MB/GB）。"""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"  # pragma: no cover


def _path_size(path: str) -> int:
    """文件或目录占用字节数（不跟随符号链接）。"""
    try:
        if os.path.islink(path) or os.path.isfile(path):
            return os.lstat(path).st_size
    except OSError:
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for fn in files:
            try:
                total += os.lstat(os.path.join(dirpath, fn)).st_size
            except OSError:
                pass
    return total


def _is_within(path: str, root: str) -> bool:
    """path 的真实路径是否位于 root 之内（防误删外部文件、防符号链接逃逸）。"""
    try:
        p = os.path.realpath(path)
        r = os.path.realpath(root)
        return os.path.commonpath([p, r]) == r and p != r
    except ValueError:  # 不同盘符（Windows）
        return False


def _storage(storage_dir: Optional[str]) -> str:
    if storage_dir:
        return storage_dir
    import tts_engine as _te
    return _te.get_storage_dir()


def total_size(entries: List[ModelEntry]) -> int:
    return sum(e.size_bytes for e in entries)


# ---------------- 盘点 ----------------

def _list_piper(storage: str) -> List[ModelEntry]:
    import tts_engine as _te
    roots = [os.path.join(storage, "piper-models")] + list(_te.get_model_search_paths())
    out: List[ModelEntry] = []
    seen = set()
    for root in roots:
        for onnx in _te._search_models_in_tree(root):
            cfg = onnx + ".json"
            if not os.path.isfile(cfg):
                continue  # 与 refresh_piper_voices 一致：缺配置文件的不是可用 Piper 语音
            vid = os.path.splitext(os.path.basename(onnx))[0]
            if vid in seen:
                continue
            seen.add(vid)
            paths = [onnx, cfg]
            out.append(ModelEntry(
                kind=KIND_PIPER, id=vid, name=vid, paths=paths,
                size_bytes=sum(_path_size(p) for p in paths),
                managed=all(_is_within(p, storage) for p in paths),
                location=os.path.dirname(os.path.abspath(onnx)),
            ))
    return out


def _list_cosyvoice(storage: str) -> List[ModelEntry]:
    root = os.path.join(storage, "cosyvoice-models")
    out: List[ModelEntry] = []
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        p = os.path.join(root, entry)
        if entry.startswith(".") or not os.path.isdir(p) or not os.listdir(p):
            continue
        out.append(ModelEntry(
            kind=KIND_COSYVOICE, id=entry, name=entry, paths=[p],
            size_bytes=_path_size(p), managed=_is_within(p, storage), location=root,
        ))
    return out


def _whisper_size_from_dirname(dirname: str) -> Optional[str]:
    """把目录名还原为 Whisper 模型规格：预下载布局即规格名；HF 缓存布局按仓库反查。"""
    from audiobook.engines.asr import WHISPER_HF_REPOS
    if dirname in WHISPER_HF_REPOS:
        return dirname
    for size, repo in WHISPER_HF_REPOS.items():
        if dirname == "models--" + repo.replace("/", "--"):
            return size
    return None


def _whisper_dir_complete(path: str) -> bool:
    """Whisper 模型目录是否完整：预下载布局看 .complete 标记，HF 缓存布局看 snapshot 里的 model.bin。"""
    if os.path.basename(path).startswith("models--"):
        import glob as _glob
        return bool(_glob.glob(os.path.join(path, "snapshots", "*", "model.bin")))
    return os.path.isfile(os.path.join(path, ".complete"))


def _list_whisper(storage: str) -> List[ModelEntry]:
    root = os.path.join(storage, "whisper-models")
    out: List[ModelEntry] = []
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        p = os.path.join(root, entry)
        if entry.startswith(".") or not os.path.isdir(p) or not os.listdir(p):
            continue
        size = _whisper_size_from_dirname(entry)
        if size is None:
            name = f"Whisper {entry}"
        elif entry.startswith("models--"):
            name = f"Whisper {size}（HF 缓存）"
        else:
            name = f"Whisper {size}"
        if size is not None and not _whisper_dir_complete(p):
            name += "（未下载完整）"
        out.append(ModelEntry(
            kind=KIND_WHISPER, id=entry, name=name, paths=[p],
            size_bytes=_path_size(p), managed=_is_within(p, storage), location=root,
        ))
    return out


def list_installed(storage_dir: Optional[str] = None) -> List[ModelEntry]:
    """盘点所有已安装模型，按 Piper → CosyVoice → Whisper 排序。"""
    storage = _storage(storage_dir)
    return _list_piper(storage) + _list_cosyvoice(storage) + _list_whisper(storage)


def find(kind: str, model_id: str, storage_dir: Optional[str] = None) -> Optional[ModelEntry]:
    for e in list_installed(storage_dir):
        if e.kind == kind and e.id == model_id:
            return e
    return None


# ---------------- 删除 ----------------

def delete_model(entry: ModelEntry, storage_dir: Optional[str] = None) -> None:
    """删除一个模型。外部（非存储目录内）模型拒绝删除，抛 PermissionError。

    删除前先卸载对应的内存缓存实例（释放文件句柄，Windows 下否则删不掉），
    删除后刷新相应语音列表，界面重新取语音即可看到变化。
    """
    storage = _storage(storage_dir)
    if not entry.managed or not all(_is_within(p, storage) for p in entry.paths):
        raise PermissionError(f"「{entry.name}」不在便携存储目录内，为安全起见不在此删除：{entry.location}")

    import tts_engine as _te
    if entry.kind == KIND_PIPER:
        _te._unload_piper_model(entry.id)
    elif entry.kind == KIND_COSYVOICE:
        _te._unload_cosyvoice_model()
        try:  # 声音克隆的桥接进程可能正加载着该模型
            from audiobook.cloning import client as _clone
            _clone.shutdown()
        except Exception:
            pass
    elif entry.kind == KIND_WHISPER:
        from audiobook.engines.asr import unload_whisper_model
        unload_whisper_model()

    for p in entry.paths:
        if os.path.islink(p) or os.path.isfile(p):
            os.remove(p)
        elif os.path.isdir(p):
            shutil.rmtree(p)

    if entry.kind == KIND_PIPER:
        _te._invalidate_scan_cache()
        _te.refresh_piper_voices()
    elif entry.kind == KIND_COSYVOICE:
        _te.refresh_cosyvoice_voices()


# ---------------- 下载 ----------------

def downloadable(storage_dir: Optional[str] = None) -> List[DownloadItem]:
    """常用可下载项（Piper 内置中文语音 / CosyVoice / Whisper 各规格），附是否已安装。

    Piper 官方目录里的其余数百个语音走界面的「Piper 语音包目录」对话框按语言挑选。
    """
    import tts_engine as _te
    from audiobook.engines.asr import WHISPER_MODELS
    entries = list_installed(storage_dir)
    installed = {(e.kind, e.id) for e in entries}
    whisper_installed = {_whisper_size_from_dirname(e.id) for e in entries
                         if e.kind == KIND_WHISPER and _whisper_dir_complete(e.paths[0])}

    items: List[DownloadItem] = []
    for label, vid in _te.PIPER_BUILTIN_VOICES.items():
        items.append(DownloadItem(KIND_PIPER, vid, label, (KIND_PIPER, vid) in installed))
    for key, meta in _te.COSYVOICE_MODEL_URLS.items():
        items.append(DownloadItem(KIND_COSYVOICE, key, meta.get("description", key),
                                  (KIND_COSYVOICE, key) in installed))
    for size, label in WHISPER_MODELS.items():
        items.append(DownloadItem(KIND_WHISPER, size, f"Whisper {label}", size in whisper_installed))
    return items


def download(kind: str, model_id: str, should_stop: Optional[Callable[[], bool]] = None,
             storage_dir: Optional[str] = None) -> None:
    """下载一个模型（进度经 tts_engine 的全局下载监听器推送）。"""
    import tts_engine as _te
    if kind == KIND_PIPER:
        if model_id in _te.PIPER_BUILTIN_VOICES.values():
            _te._ensure_piper_model(model_id, should_stop=should_stop)
        else:
            # 官方目录中的语音 key（如 zh_CN-huayan-x_low）
            _te.download_piper_voice_from_catalog(model_id, should_stop=should_stop)
        _te._invalidate_scan_cache()
        _te.refresh_piper_voices()
    elif kind == KIND_COSYVOICE:
        _te._ensure_cosyvoice_model(model_id, should_stop=should_stop)
        _te.refresh_cosyvoice_voices()
    elif kind == KIND_WHISPER:
        from audiobook.engines.asr import _ensure_whisper_model, WHISPER_HF_REPOS
        if model_id not in WHISPER_HF_REPOS:
            raise ValueError(f"未知的 Whisper 模型规格: {model_id}")
        _ensure_whisper_model(model_id, _storage(storage_dir), should_stop=should_stop)
    else:
        raise ValueError(f"未知的模型类型: {kind}")
