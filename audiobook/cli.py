"""audiobook.cli — 命令行接口（v6.0/B2）。

让「文字转有声读物」可脱离 GUI 用命令行/脚本调用，复用 v6.0 的分层实现与引擎
注册表。子命令：

    audiobook convert  <文件...> [选项]   把文本转成有声书（mp3 / m4b / srt）
    audiobook engines                      列出引擎及就绪状态
    audiobook voices   [--engine X]        列出某引擎的可用语音
    audiobook transcribe <音频> [选项]     语音转文字（ASR）

入口：`python -m audiobook ...` 或安装后的 `audiobook ...`。
"""

import argparse
import glob
import os
import sys


# ---------------- 公共工具 ----------------

def _read_inputs(paths):
    """读取一个或多个输入文件（支持通配符），合并为一段文本。"""
    from audiobook.io.readers import load_file_content
    parts = []
    for p in paths:
        matched = sorted(glob.glob(p)) or [p]
        for m in matched:
            if not os.path.isfile(m):
                raise FileNotFoundError(f"文件不存在: {m}")
            parts.append(load_file_content(m)["content"])
    return "\n\n".join(parts)


def _list_voices(engine):
    from audiobook.engines.base import get_engine
    return get_engine(engine).list_voices()  # {显示名: voice_id}


def _resolve_voice(engine, voice):
    """把用户给的 --voice（显示名或 voice_id）解析为 voice_id；未给则取默认。"""
    voices = _list_voices(engine)
    if voice:
        if voice in voices:            # 显示名
            return voices[voice]
        if voice in voices.values():   # 已是 voice_id
            return voice
        return voice                   # 未知：按原样透传（信任用户，如 edge 语音名）
    if voices:
        return next(iter(voices.values()))
    import tts_engine as _te
    return _te.get_voice_id("", engine)


# ---------------- 子命令 ----------------

def cmd_engines(args):
    from audiobook.engines.base import all_engines
    for e in all_engines():
        try:
            ready, msg = e.is_ready()
        except Exception as exc:  # 引擎检测异常不应中断列表
            ready, msg = False, repr(exc)
        status = "就绪" if ready else "不可用"
        first = (msg or "").splitlines()[0] if msg else ""
        print(f"{e.id:12} {e.display_name:10} [{status}]  {first}")
    return 0


def cmd_voices(args):
    voices = _list_voices(args.engine)
    if not voices:
        print(f"引擎 {args.engine!r} 无可用语音（可能未就绪）", file=sys.stderr)
        return 1
    for disp, vid in voices.items():
        print(f"{vid}\t{disp}")
    return 0


def cmd_convert(args):
    import tts_engine as _te
    from audiobook.io.audio import export_m4b

    text = _read_inputs(args.inputs)
    if not text.strip():
        print("错误：输入文本为空", file=sys.stderr)
        return 2

    voice = _resolve_voice(args.engine, args.voice)
    os.makedirs(args.out, exist_ok=True)

    def _progress(*a):
        if len(a) >= 2:
            print(f"\r进度: {a[0]}/{a[1]}", end="", file=sys.stderr, flush=True)

    files = _te.convert_batch(
        text=text, voice=voice, rate=args.rate, output_dir=args.out,
        split_mode=args.split, time_minutes=args.time_minutes,
        file_prefix=args.prefix, engine=args.engine,
        progress_callback=_progress,
        normalize_audio=args.normalize,
        dialogue_detection=args.dialogue,
        generate_subtitles=args.srt,
        write_metadata=args.metadata,
        album_title=args.album or args.prefix,
    )
    print(file=sys.stderr)  # 换行收尾进度
    for f in files:
        print(f)

    if args.m4b:
        mp3s = sorted(glob.glob(os.path.join(args.out, "*.mp3")))
        if not mp3s:
            print("警告：没有可合成 m4b 的 mp3", file=sys.stderr)
        else:
            m4b_path = os.path.join(args.out, f"{args.prefix}.m4b")
            export_m4b(mp3s, m4b_path, album=args.album or args.prefix)
            print(m4b_path)
    return 0


def cmd_config(args):
    import tts_engine as _te
    from audiobook.config import SCHEMA, effective
    cfg = effective(_te._load_config())
    print(f"配置文件: {_te.CONFIG_PATH}")
    for key, spec in SCHEMA.items():
        print(f"  {key:20} = {cfg.get(key)!r:>18}   # {spec['help']}")
    # 展示未被 schema 覆盖的未知键（前向兼容）
    extras = {k: v for k, v in cfg.items() if k not in SCHEMA}
    for key, val in extras.items():
        print(f"  {key:20} = {val!r:>18}   # (未知键，原样保留)")
    return 0


def cmd_transcribe(args):
    import tts_engine as _te
    from audiobook.engines.asr import transcribe
    out = transcribe(
        args.input, _te.get_storage_dir(),
        model_size=args.model, language=args.language,
        output_format=args.format, output_path=args.out,
    )
    print(out)
    return 0


# ---------------- 解析器 ----------------

def build_parser():
    from tts_engine import VERSION
    p = argparse.ArgumentParser(
        prog="audiobook",
        description="文字转有声读物 — 命令行接口",
    )
    p.add_argument("--version", action="version", version=f"audiobook {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    # convert
    c = sub.add_parser("convert", help="把文本文件转成有声书")
    c.add_argument("inputs", nargs="+", help="输入文件（txt/md/docx/epub/html/pdf，支持通配符）")
    c.add_argument("--engine", default="local", help="引擎 id（默认 local；见 audiobook engines）")
    c.add_argument("--voice", default="", help="语音（显示名或 voice_id；默认取引擎首个）")
    c.add_argument("--rate", default="+0%", help="语速，如 +0% / -20% / +30%")
    c.add_argument("--split", default="chapter", choices=["chapter", "time", "single"],
                   help="拆分方式（默认 chapter）")
    c.add_argument("--time-minutes", type=int, default=30, help="按时间拆分时每段分钟数")
    c.add_argument("--out", default="output", help="输出目录（默认 ./output）")
    c.add_argument("--prefix", default="有声读物", help="输出文件名前缀 / 专辑名")
    c.add_argument("--album", default="", help="专辑名（默认用 --prefix）")
    c.add_argument("--m4b", action="store_true", help="额外导出带章节的 m4b")
    c.add_argument("--srt", action="store_true", help="同时生成 srt 字幕")
    c.add_argument("--metadata", action="store_true", help="写入 ID3 元数据")
    c.add_argument("--normalize", action="store_true", help="响度归一化（需 ffmpeg）")
    c.add_argument("--dialogue", action="store_true", help="多人对话检测")
    c.set_defaults(func=cmd_convert)

    # engines
    e = sub.add_parser("engines", help="列出引擎及就绪状态")
    e.set_defaults(func=cmd_engines)

    # voices
    v = sub.add_parser("voices", help="列出某引擎的可用语音")
    v.add_argument("--engine", default="local", help="引擎 id（默认 local）")
    v.set_defaults(func=cmd_voices)

    # config
    cf = sub.add_parser("config", help="查看生效配置及各项说明")
    cf.set_defaults(func=cmd_config)

    # transcribe
    t = sub.add_parser("transcribe", help="语音转文字（ASR，需 faster-whisper）")
    t.add_argument("input", help="音频文件（mp3/wav/m4a/...）")
    t.add_argument("--model", default="base", help="Whisper 模型：tiny/base/small/medium/large-v3")
    t.add_argument("--language", default="auto", help="语言：auto/zh/en/ja/...")
    t.add_argument("--format", default="txt", choices=["txt", "srt", "json"], help="输出格式")
    t.add_argument("--out", default=None, help="输出文件路径（默认音频旁）")
    t.set_defaults(func=cmd_transcribe)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130
    except Exception as exc:  # CLI 顶层：打印友好错误而非 traceback
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
