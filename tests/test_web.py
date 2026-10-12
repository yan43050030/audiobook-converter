"""audiobook.web Web UI 测试（v6.0/B4）。

用 FastAPI TestClient 覆盖接口契约：engines/voices、convert 任务提交→轮询→下载、
输入校验、下载防目录穿越。转换通过 monkeypatch 打桩，不依赖 espeak/ffmpeg；
未安装 fastapi 时整文件跳过。
"""

import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import tts_engine as _te  # noqa: E402
from audiobook.web import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 把存储目录指向临时目录，产物不落到真实家目录
    monkeypatch.setattr(_te, "get_storage_dir", lambda: str(tmp_path))
    return TestClient(create_app())


def _fake_convert(**kwargs):
    """桩：按 convert_batch 的约定在 output_dir 写一个 mp3，并回调一次进度。"""
    out = kwargs["output_dir"]
    os.makedirs(out, exist_ok=True)
    cb = kwargs.get("progress_callback")
    if cb:
        cb(1, 1)
    path = os.path.join(out, f"{kwargs['file_prefix']}_01.mp3")
    with open(path, "wb") as f:
        f.write(b"ID3fake-audio")
    return [path]


def _wait_done(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("任务未在超时内完成")


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "文字转有声读物" in r.text


def test_engines_lists_local(client):
    r = client.get("/api/engines")
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()["engines"]]
    assert "local" in ids


def test_voices_bad_engine_400(client):
    r = client.get("/api/voices", params={"engine": "no_such_engine"})
    assert r.status_code == 400


def test_convert_empty_text_400(client):
    r = client.post("/api/convert", data={"text": "   ", "engine": "local"})
    assert r.status_code == 400


def test_convert_bad_split_400(client, monkeypatch):
    monkeypatch.setattr(_te, "convert_batch", _fake_convert)
    # 语音解析走 local 引擎；给足文本，split 非法应 400
    r = client.post("/api/convert",
                    data={"text": "你好世界", "engine": "local", "split": "nope"})
    assert r.status_code == 400


def test_convert_flow_and_download(client, monkeypatch):
    monkeypatch.setattr(_te, "convert_batch", _fake_convert)
    r = client.post("/api/convert",
                    data={"text": "第一章\n正文内容。", "engine": "local",
                          "prefix": "测试书", "split": "single"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    j = _wait_done(client, job_id)
    assert j["status"] == "done", j
    assert j["files"], "应有产物文件"
    fname = j["files"][0]
    assert fname.endswith(".mp3")

    # 下载产物
    d = client.get(f"/api/jobs/{job_id}/files/{fname}")
    assert d.status_code == 200
    assert d.content == b"ID3fake-audio"


def test_download_path_traversal_blocked(client, monkeypatch):
    monkeypatch.setattr(_te, "convert_batch", _fake_convert)
    r = client.post("/api/convert",
                    data={"text": "x", "engine": "local", "split": "single"})
    job_id = r.json()["job_id"]
    _wait_done(client, job_id)
    # 不在产物清单中的名字 / 穿越路径 → 404
    assert client.get(f"/api/jobs/{job_id}/files/../../etc/passwd").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/files/nope.mp3").status_code == 404


def test_job_not_found_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_convert_uploaded_file(client, monkeypatch):
    monkeypatch.setattr(_te, "convert_batch", _fake_convert)
    files = {"files": ("book.txt", b"\xe7\xab\xa0\xe8\x8a\x82\xe6\x96\x87\xe6\x9c\xac", "text/plain")}
    r = client.post("/api/convert",
                    data={"engine": "local", "split": "single", "prefix": "up"},
                    files=files)
    assert r.status_code == 202
    j = _wait_done(client, r.json()["job_id"])
    assert j["status"] == "done", j
    assert j["files"]
