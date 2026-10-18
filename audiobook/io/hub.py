"""audiobook.io.hub — 模型仓库整库下载（HuggingFace / hf-mirror / ModelScope 魔搭）。

CosyVoice 官方只以「模型仓库」形式发布（多个文件 + 子目录，官方说明用 ``snapshot_download``），
没有单个压缩包，这里用纯 HTTP 实现同等的整库下载，不依赖 huggingface_hub / modelscope SDK：

* 先按下载源顺序（默认 HuggingFace → hf-mirror → ModelScope）取文件清单，取到即确定主源；
  单个文件下载失败时再依次换其余源。
* 文件先下到同级隐藏目录 ``.<目标名>.partial/``，全部完成后整体改名为目标目录——半成品不会
  被当成可用模型；中断后再次下载时，已完成的文件（记在 ``.done``）跳过，未完成的断点续传。
* 下载前按清单总大小检查磁盘剩余空间。
* 进度经 `tts_engine` 的全局下载监听器推送（与其它模型下载一致）。
"""

import fnmatch
import json
import os
import shutil
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

from audiobook.i18n import tr

HF = "huggingface"
HF_MIRROR = "hf-mirror"
MODELSCOPE = "modelscope"
SOURCE_ORDER = (HF, HF_MIRROR, MODELSCOPE)
SOURCE_LABELS = {HF: "HuggingFace", HF_MIRROR: "hf-mirror 镜像", MODELSCOPE: "ModelScope 魔搭"}

_HOSTS = {HF: "https://huggingface.co", HF_MIRROR: "https://hf-mirror.com",
          MODELSCOPE: "https://modelscope.cn"}
LIST_TIMEOUT = 15
DONE_NAME = ".done"
ALWAYS_SKIP = (".gitattributes", ".gitignore", ".msc", ".mv")


class HubError(RuntimeError):
    """下载源 / 清单 / 磁盘空间问题（信息可直接展示给用户）。"""


@dataclass
class RepoFile:
    path: str          # 仓库内相对路径（/ 分隔）
    size: int = 0      # 字节；清单未给出时为 0


def _repo_for(repos: Dict[str, str], source: str) -> Optional[str]:
    """HF 与 hf-mirror 共用同一个仓库名。"""
    if source == HF_MIRROR:
        return repos.get(HF_MIRROR) or repos.get(HF)
    return repos.get(source)


def file_url(source: str, repo: str, path: str) -> str:
    if source == MODELSCOPE:
        return (f"{_HOSTS[MODELSCOPE]}/api/v1/models/{repo}/repo"
                f"?Revision=master&FilePath={quote(path, safe='')}")
    return f"{_HOSTS[source]}/{repo}/resolve/main/{quote(path)}"


def _get_json(url: str):
    import requests
    r = requests.get(url, timeout=LIST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def list_files(source: str, repo: str) -> List[RepoFile]:
    """取仓库文件清单（仅文件，不含目录）。"""
    if source == MODELSCOPE:
        d = _get_json(f"{_HOSTS[MODELSCOPE]}/api/v1/models/{repo}/repo/files"
                      f"?Revision=master&Recursive=true")
        if isinstance(d, dict) and d.get("Code") not in (None, 200):
            raise HubError(tr('ModelScope 返回错误：{v1}', v1=d.get('Message') or d.get('Code')))
        files = (d.get("Data") or {}).get("Files") or []
        return [RepoFile(f["Path"], int(f.get("Size") or 0))
                for f in files if f.get("Type") != "tree" and f.get("Path")]
    d = _get_json(f"{_HOSTS[source]}/api/models/{repo}?blobs=true")
    siblings = d.get("siblings") or []
    return [RepoFile(s["rfilename"], int(s.get("size") or 0))
            for s in siblings if s.get("rfilename")]


def _skipped(path: str, skip: Sequence[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name in ALWAYS_SKIP:
        return True
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in skip)


def _safe_dest(root: str, path: str) -> str:
    """清单里的路径来自网络：拒绝绝对路径与 .. 逃逸。"""
    dest = os.path.normpath(os.path.join(root, *path.split("/")))
    if os.path.isabs(path) or os.path.commonpath([os.path.abspath(dest), os.path.abspath(root)]) \
            != os.path.abspath(root):
        raise HubError(tr('仓库文件路径不安全，已拒绝：{path}', path=path))
    return dest


def resolve_listing(repos: Dict[str, str], sources: Iterable[str] = SOURCE_ORDER,
                    skip: Sequence[str] = ()) -> Tuple[str, List[RepoFile]]:
    """按顺序找到第一个能取到清单的源，返回 (源, 过滤后的文件清单)。"""
    errors = []
    for src in sources:
        repo = _repo_for(repos, src)
        if not repo:
            continue
        try:
            files = [f for f in list_files(src, repo) if not _skipped(f.path, skip)]
        except Exception as e:
            errors.append(f"{tr(SOURCE_LABELS.get(src, src))}: {e}")
            continue
        if files:
            return src, files
        errors.append(tr('{source}: 文件清单为空', source=tr(SOURCE_LABELS.get(src, src))))
    raise HubError(tr("无法从任何下载源获取模型文件清单（请检查网络）：\n") + "\n".join(errors))


def _check_disk(path: str, need: int) -> None:
    if need <= 0:
        return
    probe = path
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return
        probe = parent
    free = shutil.disk_usage(probe).free
    if free < need * 1.05:
        gb = 1024 ** 3
        raise HubError(tr('磁盘空间不足：需要约 {v1:.1f} GB，可用 {v2:.1f} GB（{probe}）', v1=need / gb, v2=free / gb, probe=probe))


def _read_done(partial: str) -> set:
    try:
        with open(os.path.join(partial, DONE_NAME), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError, TypeError):
        return set()


def _write_done(partial: str, done: set) -> None:
    tmp = os.path.join(partial, DONE_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)
    os.replace(tmp, os.path.join(partial, DONE_NAME))


def partial_dir(target_dir: str) -> str:
    parent, name = os.path.split(os.path.normpath(target_dir))
    return os.path.join(parent, f".{name}.partial")


def snapshot_download(repos: Dict[str, str], target_dir: str, description: str = "",
                      should_stop: Optional[Callable[[], bool]] = None,
                      skip: Sequence[str] = (),
                      sources: Iterable[str] = SOURCE_ORDER) -> str:
    """整库下载到 target_dir（已存在且非空则直接返回）。

    repos: {源: 仓库名}，如 ``{"huggingface": "FunAudioLLM/CosyVoice2-0.5B",
    "modelscope": "iic/CosyVoice2-0.5B"}``（hf-mirror 自动沿用 HF 的仓库名）。
    用户暂停时抛 `tts_engine.StopRequested`，已下载部分保留以便续传。
    """
    import tts_engine as _te

    target_dir = os.path.normpath(target_dir)
    if os.path.isdir(target_dir) and os.listdir(target_dir):
        return target_dir
    sources = list(sources)
    desc = description or os.path.basename(target_dir)

    if should_stop and should_stop():
        raise _te.StopRequested(tr("用户暂停"))
    primary, files = resolve_listing(repos, sources, skip)
    partial = partial_dir(target_dir)
    os.makedirs(partial, exist_ok=True)
    done = _read_done(partial)
    remaining = [f for f in files if f.path not in done]
    _check_disk(partial, sum(f.size for f in remaining))

    order = [primary] + [s for s in sources if s != primary]
    for i, f in enumerate(files, 1):
        if f.path in done:
            continue
        if should_stop and should_stop():
            raise _te.StopRequested(tr("用户暂停"))
        dest = _safe_dest(partial, f.path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        errors = []
        for src in order:
            repo = _repo_for(repos, src)
            if not repo:
                continue
            try:
                _te._download_file_single(file_url(src, repo, f.path), dest,
                                          f"{desc} [{i}/{len(files)}] {f.path}",
                                          should_stop=should_stop)
                if f.size and os.path.getsize(dest) != f.size:
                    got = os.path.getsize(dest)
                    os.remove(dest)  # 大小不符：丢弃，换源重下
                    raise HubError(tr('文件大小不符（应为 {size}，实得 {got}）', size=f.size, got=got))
                break
            except _te.StopRequested:
                raise
            except Exception as e:
                errors.append(f"{tr(SOURCE_LABELS.get(src, src))}: {e}")
        else:
            raise HubError(tr('下载失败：{path}\n', path=f.path) + "\n".join(errors))
        done.add(f.path)
        _write_done(partial, done)

    try:
        os.remove(os.path.join(partial, DONE_NAME))
    except OSError:
        pass
    if os.path.isdir(target_dir):  # 空目录（如旧版残留）
        os.rmdir(target_dir)
    os.replace(partial, target_dir)
    return target_dir
