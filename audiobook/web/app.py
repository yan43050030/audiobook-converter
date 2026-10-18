"""audiobook.web.app — FastAPI 应用与后台转换任务（v6.0/B4）。

设计要点：
* 复用 `tts_engine`（convert_batch / 存储目录）与引擎注册表（`audiobook.engines.base`）
  以及读取器（`audiobook.io.readers`），不重复实现转换逻辑；
* 转换在后台线程执行，接口立即返回 job_id，前端轮询进度，避免长请求超时；
* 任务产物落在 `<存储目录>/web_output/<job_id>/`，通过下载接口取回；
* 纯本地工具定位：任务登记在内存，重启即清空（不做持久化/鉴权）。
"""

import html
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from audiobook import i18n
from audiobook.i18n import tr


# ---------------- 任务模型 ----------------

@dataclass
class Job:
    id: str
    status: str = "pending"          # pending / running / done / error
    engine: str = ""
    prefix: str = ""
    cur: int = 0
    total: int = 0
    files: List[str] = field(default_factory=list)  # 产物文件名（相对 out_dir）
    error: str = ""
    out_dir: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "engine": self.engine,
            "prefix": self.prefix,
            "progress": {"cur": self.cur, "total": self.total},
            "files": list(self.files),
            "error": self.error,
            "created_at": self.created_at,
        }


class JobStore:
    """线程安全的内存任务登记表。"""

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, **kw) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], **kw)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)


# ---------------- 转换执行 ----------------

def _assemble_text(pasted: str, uploads: List[tuple]) -> str:
    """把粘贴文本与上传文件（(文件名, 字节)）合并为一段文本。

    上传文件先按原扩展名落到临时文件，再交给 readers.load_file_content 以正确
    处理 epub/pdf/docx 等格式。
    """
    import tempfile
    from audiobook.io.readers import load_file_content

    parts = []
    for name, data in uploads:
        suffix = os.path.splitext(name)[1] or ".txt"
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            parts.append(load_file_content(tmp)["content"])
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    if pasted and pasted.strip():
        parts.append(pasted)
    return "\n\n".join(p for p in parts if p and p.strip())


def _run_job(store: JobStore, job: Job, text: str, opts: dict) -> None:
    """后台线程：执行一次转换，更新任务状态与进度。"""
    import tts_engine as _te
    from audiobook.io.audio import export_m4b

    def _progress(*a):
        if len(a) >= 2:
            job.cur, job.total = int(a[0]), int(a[1])

    try:
        job.status = "running"
        os.makedirs(job.out_dir, exist_ok=True)
        _te.convert_batch(
            text=text, voice=opts["voice"], rate=opts["rate"],
            output_dir=job.out_dir, split_mode=opts["split"],
            time_minutes=opts["time_minutes"], file_prefix=opts["prefix"],
            engine=opts["engine"], progress_callback=_progress,
            normalize_audio=opts["normalize"], dialogue_detection=opts["dialogue"],
            generate_subtitles=opts["srt"], write_metadata=opts["metadata"],
            album_title=opts["album"] or opts["prefix"],
        )
        if opts["m4b"]:
            import glob as _glob
            mp3s = sorted(_glob.glob(os.path.join(job.out_dir, "*.mp3")))
            if mp3s:
                m4b_path = os.path.join(job.out_dir, f"{opts['prefix']}.m4b")
                export_m4b(mp3s, m4b_path, album=opts["album"] or opts["prefix"])
        # 收集产物
        job.files = sorted(
            f for f in os.listdir(job.out_dir)
            if os.path.isfile(os.path.join(job.out_dir, f))
        )
        job.status = "done"
    except Exception as exc:  # 任务级错误：记录，不让线程静默崩溃
        job.error = f"{type(exc).__name__}: {exc}"
        job.status = "error"


# ---------------- 页面渲染 ----------------

_TAG = re.compile(r"\{\{(.+?)\}\}")                 # 页面里的静态文字：{{中文原文}}
_JS_CALL = re.compile(r'\bt\("((?:[^"\\]|\\.)*)"')   # 脚本里的动态文字：t("中文原文")
_JS_TABLE = "/*I18N*/{}/*END_I18N*/"


def render_index(page: str) -> str:
    """按当前界面语言渲染页面：替换 {{…}}，并为脚本注入 t("…") 的译文表。"""
    def _tag(m):
        key = m.group(1)
        if key == "HTML_LANG":
            return "en" if i18n.current_language() == "en" else "zh-CN"
        return html.escape(tr(key), quote=True)

    table = {k: tr(k) for k in _JS_CALL.findall(page)}
    # json.dumps 后再转义 "</"，防止译文里出现 </script> 提前结束脚本
    page = page.replace(_JS_TABLE, json.dumps(table, ensure_ascii=False).replace("</", "<\\/"))
    return _TAG.sub(_tag, page)


# ---------------- 应用工厂 ----------------

def create_app():
    from fastapi import FastAPI, Form, UploadFile, File, HTTPException
    from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

    app = FastAPI(title=tr("文字转有声读物 · Web UI"), version="6.0")
    store = JobStore()

    _here = os.path.dirname(os.path.abspath(__file__))
    _index = os.path.join(_here, "index.html")

    def _web_root() -> str:
        import tts_engine as _te
        root = os.path.join(_te.get_storage_dir(), "web_output")
        os.makedirs(root, exist_ok=True)
        return root

    @app.get("/", response_class=HTMLResponse)
    def index():
        with open(_index, "r", encoding="utf-8") as f:
            return HTMLResponse(render_index(f.read()))

    @app.get("/api/engines")
    def api_engines():
        from audiobook.engines.base import all_engines
        out = []
        for e in all_engines():
            try:
                ready, msg = e.is_ready()
            except Exception as exc:
                ready, msg = False, repr(exc)
            first = (msg or "").splitlines()[0] if msg else ""
            out.append({"id": e.id, "name": tr(e.display_name),
                        "ready": bool(ready), "message": first})
        return {"engines": out}

    @app.get("/api/voices")
    def api_voices(engine: str = "local"):
        from audiobook.engines.base import get_engine
        try:
            voices = get_engine(engine).list_voices()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=tr('引擎 {engine!r} 不可用: {exc}', engine=engine, exc=exc))
        # voices 保持 {显示名: voice_id} 不变（接口兼容）；voice_labels 给出显示名的译名
        return {"engine": engine, "voices": voices,
                "voice_labels": {disp: tr(disp) for disp in voices}}

    @app.post("/api/convert")
    async def api_convert(
        text: str = Form(""),
        engine: str = Form("local"),
        voice: str = Form(""),
        rate: str = Form("+0%"),
        split: str = Form("chapter"),
        time_minutes: int = Form(30),
        prefix: str = Form(tr("有声读物")),
        album: str = Form(""),
        m4b: bool = Form(False),
        srt: bool = Form(False),
        metadata: bool = Form(False),
        normalize: bool = Form(False),
        dialogue: bool = Form(False),
        files: List[UploadFile] = File(default=[]),
    ):
        uploads = []
        for uf in files or []:
            uploads.append((uf.filename or "upload.txt", await uf.read()))
        full_text = _assemble_text(text, uploads)
        if not full_text.strip():
            raise HTTPException(status_code=400, detail=tr("输入文本为空"))

        if split not in ("chapter", "time", "single"):
            raise HTTPException(status_code=400, detail=tr('非法拆分方式: {split}', split=split))

        from audiobook.cli import _resolve_voice
        try:
            resolved_voice = _resolve_voice(engine, voice)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=tr('引擎/语音解析失败: {exc}', exc=exc))

        job = store.create(engine=engine, prefix=prefix)
        job.out_dir = os.path.join(_web_root(), job.id)
        opts = {
            "voice": resolved_voice, "rate": rate, "split": split,
            "time_minutes": time_minutes, "prefix": prefix, "album": album,
            "engine": engine, "m4b": m4b, "srt": srt, "metadata": metadata,
            "normalize": normalize, "dialogue": dialogue,
        }
        t = threading.Thread(target=_run_job, args=(store, job, full_text, opts),
                             daemon=True)
        t.start()
        return JSONResponse({"job_id": job.id}, status_code=202)

    @app.get("/api/jobs")
    def api_jobs():
        return {"jobs": [j.to_dict() for j in store.list()]}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        job = store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=tr("任务不存在"))
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/files/{name}")
    def api_job_file(job_id: str, name: str):
        job = store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=tr("任务不存在"))
        # 防目录穿越：只允许产物清单内的裸文件名
        safe = os.path.basename(name)
        if safe != name or safe not in job.files:
            raise HTTPException(status_code=404, detail=tr("文件不存在"))
        path = os.path.join(job.out_dir, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=tr("文件不存在"))
        return FileResponse(path, filename=safe)

    return app
