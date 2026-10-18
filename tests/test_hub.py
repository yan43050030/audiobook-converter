"""audiobook.io.hub 整库下载：本地 HTTP 服务模拟 HuggingFace / ModelScope 接口，驱动真实下载路径。"""

import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

os.environ["AUDIOBOOK_LANG"] = "zh"  # 断言按中文界面文案写；不受系统语言影响（unittest 不加载 conftest）

try:
    import requests  # noqa: F401
    import tqdm  # noqa: F401
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

import tts_engine
from audiobook.io import hub

REPO_FILES = {
    "cosyvoice3.yaml": b"model: v3\n",
    "llm.pt": b"L" * 5000,
    "llm.rl.pt": b"R" * 100,
    "CosyVoice-BlankEN/config.json": b"{}",
    ".gitattributes": b"*.pt filter=lfs",
}


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "fakehub"
    state = None  # 由测试注入：dict

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, data):
        st = self.state
        st["gets"].append(self.path)
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                return self._send(416)
            chunk = data[start:]
            return self._send(206, chunk, {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"})
        if st.get("truncate_once") and self.path.startswith("/hf/") and self.path not in st["truncated"]:
            # 模拟连接中断：只发一半并声明完整长度会让 requests 报错，这里直接发一半并以较短长度结束
            st["truncated"].add(self.path)
            return self._send(200, data[: len(data) // 2])
        return self._send(200, data)

    def do_GET(self):
        st = self.state
        u = urlparse(self.path)
        q = parse_qs(u.query)
        prefix = u.path.split("/")[1]  # hf / ms（用路径前缀区分两个「站点」）
        if prefix in st.get("down", ()):
            return self._send(503, b"down")
        path = "/" + "/".join(u.path.split("/")[2:])
        if prefix == "hf":
            if path == "/api/models/org/model":
                sib = [{"rfilename": k, "size": len(v)} for k, v in REPO_FILES.items()]
                return self._send(200, json.dumps({"siblings": sib}).encode())
            if path.startswith("/org/model/resolve/main/"):
                name = path[len("/org/model/resolve/main/"):]
                if name in REPO_FILES:
                    return self._serve_file(REPO_FILES[name])
        if prefix == "ms":
            if path == "/api/v1/models/iic/model/repo/files":
                files = [{"Name": k.rsplit("/", 1)[-1], "Path": k, "Type": "blob", "Size": len(v)}
                         for k, v in REPO_FILES.items()]
                files.append({"Name": "CosyVoice-BlankEN", "Path": "CosyVoice-BlankEN", "Type": "tree"})
                return self._send(200, json.dumps({"Code": 200, "Data": {"Files": files}}).encode())
            if path == "/api/v1/models/iic/model/repo":
                name = q.get("FilePath", [""])[0]
                if name in REPO_FILES:
                    return self._serve_file(REPO_FILES[name])
        return self._send(404, b"nf")


@unittest.skipUnless(HAS_DEPS, "需要 requests / tqdm")
class TestSnapshotDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = {"gets": [], "down": set(), "truncated": set()}
        handler = type("H", (_Handler,), {"state": self.state})
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        hosts = {hub.HF: base + "/hf", hub.HF_MIRROR: base + "/mirror", hub.MODELSCOPE: base + "/ms"}
        self.p = mock.patch.dict(hub._HOSTS, hosts)
        self.p.start()
        self.repos = {"huggingface": "org/model", "modelscope": "iic/model"}
        self.target = os.path.join(self.tmp, "cosyvoice-models", "Fun-CosyVoice3-0.5B")

    def tearDown(self):
        self.p.stop()
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_complete(self, skipped=("llm.rl.pt",)):
        for name, data in REPO_FILES.items():
            path = os.path.join(self.target, *name.split("/"))
            if name in skipped or name == ".gitattributes":
                self.assertFalse(os.path.exists(path), name)
            else:
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), data, name)
        self.assertFalse(os.path.exists(hub.partial_dir(self.target)))
        self.assertFalse(os.path.exists(os.path.join(self.target, hub.DONE_NAME)))

    def test_hf_download_with_skip(self):
        out = hub.snapshot_download(self.repos, self.target, "t", skip=("*.rl.pt",))
        self.assertEqual(out, os.path.normpath(self.target))
        self._assert_complete()
        self.assertTrue(all(g.startswith("/hf/") for g in self.state["gets"]))

    def test_falls_back_to_modelscope(self):
        self.state["down"] = {"hf", "mirror"}
        hub.snapshot_download(self.repos, self.target, "t", skip=("*.rl.pt",))
        self._assert_complete()
        self.assertTrue(all(g.startswith("/ms/") for g in self.state["gets"]))

    def test_per_file_source_fallback(self):
        # 清单从 HF 取到，但 HF 的文件下载全挂 → 每个文件换 hf-mirror(也挂) → ModelScope
        orig = hub.list_files

        def _list(src, repo):
            files = orig(src, repo)
            self.state["down"] = {"hf", "mirror"}
            return files

        with mock.patch.object(hub, "list_files", side_effect=_list):
            hub.snapshot_download(self.repos, self.target, "t", skip=("*.rl.pt",))
        self._assert_complete()

    def test_resume_after_stop(self):
        partial = hub.partial_dir(self.target)

        def _stop():  # 至少完成一个文件后暂停
            return os.path.exists(os.path.join(partial, hub.DONE_NAME))

        with self.assertRaises(tts_engine.StopRequested):
            hub.snapshot_download(self.repos, self.target, "t", should_stop=_stop, skip=("*.rl.pt",))
        self.assertTrue(os.path.isdir(partial))
        self.assertFalse(os.path.exists(self.target))  # 半成品不会出现在目标位置
        done = json.load(open(os.path.join(partial, hub.DONE_NAME), encoding="utf-8"))
        self.assertTrue(done)
        n_before = len(self.state["gets"])
        hub.snapshot_download(self.repos, self.target, "t", skip=("*.rl.pt",))
        self._assert_complete()
        fetched_again = [g for g in self.state["gets"][n_before:]]
        for name in done:  # 已完成的文件不再请求
            self.assertFalse(any(g.endswith("/" + name) for g in fetched_again), name)

    def test_size_mismatch_retries_other_source(self):
        self.state["truncate_once"] = True  # 每个文件首次请求只发一半
        hub.snapshot_download(self.repos, self.target, "t", skip=("*.rl.pt",))
        self._assert_complete()

    def test_all_sources_down(self):
        self.state["down"] = {"hf", "mirror", "ms"}
        with self.assertRaises(hub.HubError) as cm:
            hub.snapshot_download(self.repos, self.target, "t")
        self.assertIn("文件清单", str(cm.exception))
        self.assertFalse(os.path.exists(self.target))

    def test_disk_space_check(self):
        Usage = type("U", (), {"free": 10})
        with mock.patch.object(hub.shutil, "disk_usage", return_value=Usage()):
            with self.assertRaises(hub.HubError) as cm:
                hub.snapshot_download(self.repos, self.target, "t")
        self.assertIn("磁盘空间不足", str(cm.exception))

    def test_existing_target_is_noop(self):
        os.makedirs(self.target)
        open(os.path.join(self.target, "cosyvoice3.yaml"), "w").close()
        hub.snapshot_download(self.repos, self.target, "t")
        self.assertEqual(self.state["gets"], [])


class TestHubHelpers(unittest.TestCase):
    def test_unsafe_paths_rejected(self):
        root = tempfile.mkdtemp()
        try:
            for bad in ("../evil", "a/../../evil", "/etc/passwd"):
                with self.assertRaises(hub.HubError):
                    hub._safe_dest(root, bad)
            self.assertTrue(hub._safe_dest(root, "a/b.txt").startswith(root))
        finally:
            shutil.rmtree(root)

    def test_urls(self):
        self.assertEqual(hub.file_url(hub.HF, "o/m", "a b/c.pt"),
                         "https://huggingface.co/o/m/resolve/main/a%20b/c.pt")
        self.assertEqual(hub.file_url(hub.HF_MIRROR, "o/m", "x.pt"),
                         "https://hf-mirror.com/o/m/resolve/main/x.pt")
        self.assertEqual(hub.file_url(hub.MODELSCOPE, "iic/m", "d/x.pt"),
                         "https://modelscope.cn/api/v1/models/iic/m/repo?Revision=master&FilePath=d%2Fx.pt")

    def test_mirror_uses_hf_repo(self):
        self.assertEqual(hub._repo_for({"huggingface": "a/b"}, hub.HF_MIRROR), "a/b")
        self.assertIsNone(hub._repo_for({"huggingface": "a/b"}, hub.MODELSCOPE))

    def test_registry(self):
        for key, meta in tts_engine.COSYVOICE_MODELS.items():
            self.assertIn("huggingface", meta["repos"], key)
            self.assertIn("modelscope", meta["repos"], key)
        self.assertIn(tts_engine.COSYVOICE_CLONE_MODEL, tts_engine.COSYVOICE_MODELS)

    def test_download_cosyvoice_model_dispatch(self):
        with mock.patch.object(hub, "snapshot_download") as m:
            out = tts_engine._download_cosyvoice_model("CosyVoice2-0.5B")
        args, kw = m.call_args
        self.assertEqual(args[0]["modelscope"], "iic/CosyVoice2-0.5B")
        self.assertTrue(out.endswith("CosyVoice2-0.5B"))
        with self.assertRaises(ValueError):
            tts_engine._download_cosyvoice_model("nope")


if __name__ == "__main__":
    unittest.main()
