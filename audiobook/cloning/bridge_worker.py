"""CosyVoice 桥接脚本 —— 在用户的 CosyVoice Python 环境里常驻运行。

由 audiobook.cloning.client 以子进程启动：

    <cosyvoice 环境的 python> bridge_worker.py [--repo <CosyVoice 仓库路径>]

协议：stdin / stdout 上逐行 JSON。每个请求带 "id"，响应原样带回：
    {"id": 1, "cmd": "ping"}
    {"id": 2, "cmd": "load", "model_dir": "..."}
    {"id": 3, "cmd": "tts", "model_dir": "...", "text": "...", "prompt_wav": "...",
     "prompt_text": "...", "speaker_key": "...", "speed": 1.0, "out": "/x.wav"}   零样本复刻
    {"id": 4, "cmd": "tts", "model_dir": "...", "text": "...", "spk_id": "中文女",
     "speed": 1.0, "out": "/x.wav"}                                              预置说话人（SFT）
    {"id": 5, "cmd": "speakers", "model_dir": "..."}
    {"id": 6, "cmd": "quit"}
响应：{"id": n, "ok": true, ...} 或 {"id": n, "ok": false, "error": "...", "trace": "..."}

注意：本文件不能 import audiobook 包（运行在另一个 Python 环境里），只依赖标准库、numpy
与 cosyvoice（官方仓库 FunAudioLLM/CosyVoice，接口：cosyvoice.cli.cosyvoice.AutoModel）。
库自身的 print / 进度条一律导到 stderr，stdout 只留给协议。
"""

import json
import os
import sys
import traceback
import wave

# CosyVoice3 的提示文本需要这段系统前缀（见官方 example.py）
V3_PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"

_models = {}        # model_dir -> 模型实例（同一时间只保留一个，切换模型时释放旧的）
_speakers = {}      # model_dir -> 已登记的零样本说话人 key 集合


def _setup_paths(repo):
    if not repo:
        return
    for p in (os.path.join(repo, "third_party", "Matcha-TTS"), repo):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def _get_model(model_dir):
    m = _models.get(model_dir)
    if m is None:
        if _models:  # 克隆模型与预置说话人模型交替使用时，避免两个模型同时占用显存 / 内存
            _models.clear()
            _speakers.clear()
            import gc
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        from cosyvoice.cli.cosyvoice import AutoModel
        m = AutoModel(model_dir=model_dir)
        _models[model_dir] = m
        _speakers[model_dir] = set()
    return m


def _model_type(model):
    return type(model).__name__


def _is_v3(model):
    return "CosyVoice3" in [c.__name__ for c in type(model).__mro__]


def _list_spks(model):
    """预置说话人列表（新接口 list_available_spks，老版本拼写为 list_avaliable_spks）。"""
    for name in ("list_available_spks", "list_avaliable_spks"):
        fn = getattr(model, name, None)
        if fn is not None:
            return [str(s) for s in (fn() or [])]
    return []


def _to_array(speech):
    import numpy as np
    if hasattr(speech, "detach"):          # torch.Tensor
        speech = speech.detach().cpu().numpy()
    return np.asarray(speech, dtype="float32").reshape(-1)


def _write_wav(path, audio, sample_rate):
    import numpy as np
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())


def _with_prompt(fn, *args, prompt_wav, **kw):
    """新版接口直接接收参考音频路径；老版本要求 16 kHz 张量——路径不行时再转张量重试。"""
    try:
        return list(fn(*args, prompt_wav, **kw))
    except (AttributeError, TypeError) as first:
        try:
            from cosyvoice.utils.file_utils import load_wav
            return list(fn(*args, load_wav(prompt_wav, 16000), **kw))
        except Exception:
            raise first  # 回退也失败：报原始错误，避免误导


def _tts(req):
    import numpy as np
    model_dir = req["model_dir"]
    model = _get_model(model_dir)
    text = req["text"]
    prompt_wav = req.get("prompt_wav") or ""
    prompt_text = (req.get("prompt_text") or "").strip()
    speed = float(req.get("speed") or 1.0)
    v3 = _is_v3(model)
    spk_id = (req.get("spk_id") or "").strip()

    if spk_id:
        spks = _list_spks(model)
        if spks and spk_id not in spks:
            raise ValueError(f"模型没有预置说话人「{spk_id}」，可用：{'、'.join(spks)}")
        if not spks:
            raise ValueError("该模型没有预置说话人（请使用 CosyVoice-300M-SFT 模型）")
        chunks = list(model.inference_sft(text, spk_id, stream=False, speed=speed))
        mode = "sft"
    elif prompt_text:
        if v3 and "<|endofprompt|>" not in prompt_text:
            prompt_text = V3_PROMPT_PREFIX + prompt_text
        key = req.get("speaker_key") or ""
        registered = _speakers.setdefault(model_dir, set())
        # 同一音色只提取一次说话人特征，长书逐段合成时省掉重复的提示音频处理
        if key and key not in registered and hasattr(model, "add_zero_shot_spk"):
            try:
                if model.add_zero_shot_spk(prompt_text, prompt_wav, key):
                    registered.add(key)
            except Exception:
                pass
        if key and key in registered:
            chunks = list(model.inference_zero_shot(text, "", "", zero_shot_spk_id=key,
                                                    stream=False, speed=speed))
        else:
            chunks = _with_prompt(model.inference_zero_shot, text, prompt_text,
                                  prompt_wav=prompt_wav, stream=False, speed=speed)
        mode = "zero_shot"
    else:
        tts_text = V3_PROMPT_PREFIX + text if v3 and "<|endofprompt|>" not in text else text
        chunks = _with_prompt(model.inference_cross_lingual, tts_text,
                              prompt_wav=prompt_wav, stream=False, speed=speed)
        mode = "cross_lingual"

    parts = [_to_array(c["tts_speech"]) for c in chunks if c is not None and "tts_speech" in c]
    if not parts:
        raise RuntimeError("CosyVoice 返回空音频")
    audio = np.concatenate(parts)
    sr = int(getattr(model, "sample_rate", 24000))
    _write_wav(req["out"], audio, sr)
    return {"out": req["out"], "seconds": round(len(audio) / float(sr), 3),
            "sample_rate": sr, "mode": mode, "model_type": _model_type(model)}


def _handle(req):
    cmd = req.get("cmd")
    if cmd == "ping":
        try:
            import cosyvoice  # noqa: F401
            ok, err = True, ""
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        return {"python": sys.version.split()[0], "executable": sys.executable,
                "cosyvoice": ok, "import_error": err}
    if cmd == "load":
        m = _get_model(req["model_dir"])
        return {"model_type": _model_type(m), "sample_rate": int(getattr(m, "sample_rate", 0))}
    if cmd == "tts":
        return _tts(req)
    if cmd == "speakers":
        return {"speakers": _list_spks(_get_model(req["model_dir"]))}
    raise ValueError(f"未知命令: {cmd}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    repo = argv[argv.index("--repo") + 1] if "--repo" in argv else ""
    _setup_paths(repo)

    # 协议走原 stdout 的副本；之后任何库输出（含 C 层写 fd 1）都进 stderr
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            if req.get("cmd") == "quit":
                proto.write(json.dumps({"id": rid, "ok": True}) + "\n")
                break
            resp = {"id": rid, "ok": True}
            resp.update(_handle(req))
        except Exception as e:
            resp = {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc()[-4000:]}
        proto.write(json.dumps(resp, ensure_ascii=False) + "\n")
        proto.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
