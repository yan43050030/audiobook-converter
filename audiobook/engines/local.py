"""audiobook.engines.local — 本地系统语音引擎（离线）。

macOS `say` + `afconvert` / Windows PowerShell+SAPI / Linux espeak-ng。
v6.0/A5b：把 `_local_generate*` 合成实现从 `tts_engine` 迁入本模块，并提供
`LocalEngine`（Engine 子类）。合成与平台调用不变；对 tts_engine 内部助手
（`_run_subprocess_interruptible` / `_wav_to_mp3` / `_PLATFORM`）在函数内以
`import tts_engine as _te` 惰性引用，避免模块加载期循环导入。
"""

import os
import shutil
import tempfile
from shutil import which

from audiobook.engines.base import Engine


def _local_generate_macos(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    import tts_engine as _te
    rate_val = int(rate.replace("%", "").replace("+", ""))
    wpm = max(int(175 * (1 + rate_val / 100.0)), 50)

    aiff_path = output_path.rsplit(".", 1)[0] + ".aiff"
    try:
        rc, _o, err = _te._run_subprocess_interruptible(
            ["say", "-v", voice, "-r", str(wpm), "-o", aiff_path, text],
            should_stop=should_stop,
        )
        if rc != 0:
            raise RuntimeError(f"say 失败: {err.decode('utf-8', errors='replace')}")
        rc, _o, err = _te._run_subprocess_interruptible(
            ["afconvert", "-f", "mp4f", "-d", "aac", aiff_path, output_path],
            should_stop=should_stop,
        )
        if rc != 0:
            raise RuntimeError(f"afconvert 失败: {err.decode('utf-8', errors='replace')}")
    finally:
        if os.path.exists(aiff_path):
            try:
                os.remove(aiff_path)
            except Exception:
                pass


# PowerShell：使用 SAPI 合成到 WAV
_POWERSHELL_SPEAK_TEMPLATE = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "try {{ $s.SelectVoice('{voice}') }} catch {{ }} "
    "$s.Rate = {rate}; "
    "$s.SetOutputToWaveFile('{wav}'); "
    "$txt = [IO.File]::ReadAllText('{txtfile}', [Text.Encoding]::UTF8); "
    "$s.Speak($txt); "
    "$s.Dispose();"
)


def _local_generate_windows(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    """Windows: 用 PowerShell 调用 SAPI 合成 WAV，再转 MP3"""
    import tts_engine as _te
    ps = which("powershell") or which("pwsh")
    if not ps:
        raise RuntimeError("未找到 PowerShell，无法调用 Windows 系统语音")

    rate_val = int(rate.replace("%", "").replace("+", ""))
    sapi_rate = max(min(int(rate_val / 10), 10), -10)

    tmpdir = tempfile.mkdtemp()
    wav_path = os.path.join(tmpdir, "tts.wav")
    txt_path = os.path.join(tmpdir, "tts.txt")
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)
        safe_voice = voice.replace("'", "''")
        safe_wav = wav_path.replace("'", "''")
        safe_txt = txt_path.replace("'", "''")
        script = _POWERSHELL_SPEAK_TEMPLATE.format(
            voice=safe_voice, rate=sapi_rate, wav=safe_wav, txtfile=safe_txt
        )
        rc, _out, err = _te._run_subprocess_interruptible(
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            should_stop=should_stop, timeout=600,
        )
        if rc != 0 or not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            raise RuntimeError(f"Windows SAPI 合成失败: {err.decode('utf-8', errors='replace').strip()}")
        _te._wav_to_mp3(wav_path, output_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _local_generate_linux(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    """Linux: 用 espeak-ng 合成 WAV，再转 MP3"""
    import tts_engine as _te
    espeak = which("espeak-ng") or which("espeak")
    if not espeak:
        raise RuntimeError("未找到 espeak-ng，请先安装（sudo apt install espeak-ng）")

    rate_val = int(rate.replace("%", "").replace("+", ""))
    wpm = max(int(175 * (1 + rate_val / 100.0)), 80)

    tmpdir = tempfile.mkdtemp()
    wav_path = os.path.join(tmpdir, "tts.wav")
    try:
        rc, _out, err = _te._run_subprocess_interruptible(
            [espeak, "-v", voice or "zh", "-s", str(wpm), "-w", wav_path, text],
            should_stop=should_stop, timeout=600,
        )
        if rc != 0:
            raise RuntimeError(f"espeak 失败: {err.decode('utf-8', errors='replace')}")
        _te._wav_to_mp3(wav_path, output_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _local_generate(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    """跨平台本地 TTS 调度器"""
    import tts_engine as _te
    if _te._PLATFORM == "Darwin":
        _local_generate_macos(text, voice, rate, output_path, should_stop=should_stop)
    elif _te._PLATFORM == "Windows":
        _local_generate_windows(text, voice, rate, output_path, should_stop=should_stop)
    elif _te._PLATFORM == "Linux":
        _local_generate_linux(text, voice, rate, output_path, should_stop=should_stop)
    else:
        raise RuntimeError(f"暂不支持的平台: {_te._PLATFORM}")


class LocalEngine(Engine):
    """本地系统语音引擎（离线，跨平台）。"""

    id = "local"
    display_name = "本地"

    def is_ready(self):
        import tts_engine as _te
        return _te.check_engine_ready("local")

    def list_voices(self):
        import tts_engine as _te
        return {disp: _te.get_voice_id(disp, "local")
                for disp in _te.get_voice_list("local")}

    def synthesize(self, text, voice, rate, out_path, should_stop=None):
        # 合成编排（分段 + 合并）与 tts_engine._generate_one_safe 的 local 分支一致；
        # 重试属编排层，仍由 _generate_one_safe / convert_batch 负责，不在引擎内。
        from audiobook.core.text import split_text
        from audiobook.io.audio import _merge_mp3_files
        segments = split_text(text)
        if len(segments) == 1:
            _local_generate(segments[0], voice, rate, out_path, should_stop=should_stop)
            return
        temp_dir = tempfile.mkdtemp()
        temp_files = []
        try:
            for i, seg in enumerate(segments):
                tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                _local_generate(seg, voice, rate, tp, should_stop=should_stop)
                temp_files.append(tp)
            _merge_mp3_files(temp_files, out_path)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
