"""audiobook.cli — 命令行接口（v6.0/B2）。

让「文字转有声读物」可脱离 GUI 用命令行/脚本调用，复用 v6.0 的分层实现与引擎
注册表。子命令：

    audiobook convert  <文件...> [选项]   把文本转成有声书（mp3 / m4b / srt）
    audiobook engines                      列出引擎及就绪状态
    audiobook voices   [--engine X]        列出某引擎的可用语音
    audiobook transcribe <音频> [选项]     语音转文字（ASR）
    audiobook models list|available|get|rm  模型管理（查看占用 / 下载 / 删除）
    audiobook clone list|add|rm|check|say  声音克隆（CosyVoice 零样本复刻音色）

入口：`python -m audiobook ...` 或安装后的 `audiobook ...`。
"""

import argparse
import glob
import os
import sys

from audiobook import i18n
from audiobook.i18n import tr


# ---------------- 公共工具 ----------------

def _read_inputs(paths):
    """读取一个或多个输入文件（支持通配符），合并为一段文本。"""
    from audiobook.io.readers import load_file_content
    parts = []
    for p in paths:
        matched = sorted(glob.glob(p)) or [p]
        for m in matched:
            if not os.path.isfile(m):
                raise FileNotFoundError(tr('文件不存在: {m}', m=m))
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
        status = tr("就绪") if ready else tr("不可用")
        first = (msg or "").splitlines()[0] if msg else ""
        print(f"{e.id:12} {tr(e.display_name):10} [{status}]  {first}")
    return 0


def cmd_voices(args):
    voices = _list_voices(args.engine)
    if not voices:
        print(tr('引擎 {engine!r} 无可用语音（可能未就绪）', engine=args.engine), file=sys.stderr)
        return 1
    for disp, vid in voices.items():
        print(f"{vid}\t{disp}")
    return 0


def cmd_convert(args):
    import tts_engine as _te
    from audiobook.io.audio import export_m4b

    text = _read_inputs(args.inputs)
    if not text.strip():
        print(tr("错误：输入文本为空"), file=sys.stderr)
        return 2

    voice = _resolve_voice(args.engine, args.voice)
    os.makedirs(args.out, exist_ok=True)

    def _progress(*a):
        if len(a) >= 2:
            print(tr('\r进度: {v1}/{v2}', v1=a[0], v2=a[1]), end="", file=sys.stderr, flush=True)

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
            print(tr("警告：没有可合成 m4b 的 mp3"), file=sys.stderr)
        else:
            m4b_path = os.path.join(args.out, f"{args.prefix}.m4b")
            export_m4b(mp3s, m4b_path, album=args.album or args.prefix)
            print(m4b_path)
    return 0


def cmd_config(args):
    import tts_engine as _te
    from audiobook.config import SCHEMA, effective, normalize
    if getattr(args, "set", None):
        raw = _te._load_config()
        for item in args.set:
            key, sep, value = item.partition("=")
            key = key.strip()
            if not sep or key not in SCHEMA:
                print(tr('错误：无效的设置 {item!r}（格式 KEY=VALUE，KEY 见 audiobook config）', item=item), file=sys.stderr)
                return 2
            raw[key] = value.strip()
            raw[key] = normalize({key: raw[key]})[key]  # 按 schema 校正类型（如 true → True）
        _te._save_config(raw)
        print(tr("已保存：") + ", ".join(i.partition("=")[0].strip() for i in args.set))
    cfg = effective(_te._load_config())
    print(tr('配置文件: {CONFIG_PATH}', CONFIG_PATH=_te.CONFIG_PATH))
    for key, spec in SCHEMA.items():
        print(f"  {key:20} = {cfg.get(key)!r:>18}   # {tr(spec['help'])}")
    # 展示未被 schema 覆盖的未知键（前向兼容）
    extras = {k: v for k, v in cfg.items() if k not in SCHEMA}
    for key, val in extras.items():
        print(tr('  {key:20} = {val!r:>18}   # (未知键，原样保留)', key=key, val=val))
    return 0


def cmd_serve(args):
    try:
        import uvicorn
    except ImportError:
        print(tr("错误：需要安装 Web 依赖：pip install \"audiobook-converter[web]\""),
              file=sys.stderr)
        return 2
    from audiobook.web import create_app
    app = create_app()
    print(tr('本地 Web UI: http://{host}:{port}  (Ctrl-C 停止)', host=args.host, port=args.port), file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_models(args):
    from audiobook import models as M

    if args.action == "list":
        entries = M.list_installed()
        if not entries:
            print(tr("（尚未安装任何模型）"))
        for e in entries:
            flag = "" if e.managed else tr("  [外部·只读]")
            print(f"{e.kind:10} {e.id:44} {M.format_size(e.size_bytes):>9}{flag}")
        print(tr('合计 {v1} 个，占用 {v2}', v1=len(entries), v2=M.format_size(M.total_size(entries))))
        return 0

    if args.action == "available":
        for it in M.downloadable():
            print(f"{'✓' if it.installed else ' '} {it.kind:10} {it.id:24} {it.label}")
        return 0

    if args.action == "get":
        import tts_engine as _te

        def _progress(desc, cur, total):
            if total > 0:
                print(tr('\r下载 {desc}: {v1:.1f}/{v2:.1f} MB', desc=desc, v1=cur / 1048576, v2=total / 1048576),
                      end="", file=sys.stderr, flush=True)

        _te.add_download_listener(_progress)
        try:
            M.download(args.kind, args.id)
        finally:
            _te.remove_download_listener(_progress)
            print(file=sys.stderr)
        print(tr('已下载: {kind} {id}', kind=args.kind, id=args.id))
        return 0

    # rm
    entry = M.find(args.kind, args.id)
    if entry is None:
        print(tr('未找到已安装模型: {kind} {id}（见 audiobook models list）', kind=args.kind, id=args.id), file=sys.stderr)
        return 1
    if not args.yes:
        ans = input(tr('删除 {name}（{v1}）？[y/N] ', name=entry.name, v1=M.format_size(entry.size_bytes)))
        if ans.strip().lower() not in ("y", "yes"):
            print(tr("已取消"))
            return 0
    M.delete_model(entry)
    print(tr('已删除: {name}，释放 {v1}', name=entry.name, v1=M.format_size(entry.size_bytes)))
    return 0


def cmd_clone(args):
    from audiobook.cloning import profiles as P
    from audiobook.cloning import client as C

    if args.action == "list":
        items = P.list_profiles()
        if not items:
            print(tr("（还没有克隆音色，用 audiobook clone add <名字> <参考音频> 创建）"))
        for p in items:
            text = (p.prompt_text[:24] + "…") if len(p.prompt_text) > 24 else (p.prompt_text or tr("（无文字稿）"))
            print(f"{p.id:20} {p.name:16} {p.duration:5.1f}s  {text}")
        return 0

    if args.action == "add":
        p = P.create_profile(args.name, args.audio, prompt_text=args.text or "")
        print(tr('已创建克隆音色：{name}（ID {id}，{duration:.1f} 秒）', name=p.name, id=p.id, duration=p.duration))
        if not p.prompt_text:
            print(tr("提示：填写参考音频的文字稿（--text）能明显提升复刻效果。"), file=sys.stderr)
        return 0

    if args.action == "rm":
        pid = P.parse_voice_id(args.id) or args.id
        prof = P.get_profile(pid)
        if prof is None:
            print(tr('未找到克隆音色：{id}', id=args.id), file=sys.stderr)
            return 1
        if not args.yes and input(tr('删除克隆音色「{name}」？[y/N] ', name=prof.name)).strip().lower() not in ("y", "yes"):
            print(tr("已取消"))
            return 0
        P.delete_profile(pid)
        print(tr('已删除：{name}', name=prof.name))
        return 0

    if args.action == "check":
        rep = C.check(load=args.load)
        ok, msg = C.readiness()
        print(tr('Python 解释器: {v1}', v1=rep['python'] or tr('（未配置）')))
        print(tr('CosyVoice 仓库: {v1}', v1=rep['repo'] or tr('（未配置，依赖解释器自身的 PYTHONPATH）')))
        print(tr('模型目录: {v1}', v1=rep['model_dir'] or tr('（未找到）')) + (f"  [{rep['model_type']}]" if rep['model_type'] else ""))
        print(tr('克隆音色: {v1} 个', v1=len(P.list_profiles())))
        if rep["ping"]:
            pg = rep["ping"]
            print(tr('桥接进程: Python {v1}，cosyvoice ', v1=pg.get('python'))
                  + (tr("可导入 ✓") if pg.get("cosyvoice") else tr('无法导入 ✗（{v1}）', v1=pg.get('import_error'))))
        if rep["load"]:
            print(tr('模型加载: {v1}，采样率 {v2} ✓', v1=rep['load'].get('model_type'), v2=rep['load'].get('sample_rate')))
        if rep["error"]:
            print(tr('错误: {v1}', v1=rep['error']), file=sys.stderr)
        print(("✓ " if ok else "✗ ") + msg)
        return 0 if ok and not rep["error"] and (rep["ping"] or {}).get("cosyvoice") else 1

    # say
    from audiobook.engines.base import get_engine
    pid = P.parse_voice_id(args.id) or args.id
    if P.get_profile(pid) is None:
        print(tr('未找到克隆音色：{id}', id=args.id), file=sys.stderr)
        return 1
    get_engine("clone").synthesize_segment(args.text, P.VOICE_PREFIX + pid, args.rate, args.out)
    print(args.out)
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
        description=tr("文字转有声读物 — 命令行接口"),
    )
    p.add_argument("--version", action="version", version=f"audiobook {VERSION}")
    p.add_argument("--lang", choices=list(i18n.LANGUAGE_CHOICES),
                   help=tr("界面语言：auto（跟随配置 / 系统）/ zh / en"))
    sub = p.add_subparsers(dest="command", required=True)

    # convert
    c = sub.add_parser("convert", help=tr("把文本文件转成有声书"))
    c.add_argument("inputs", nargs="+", help=tr("输入文件（txt/md/docx/epub/html/pdf，支持通配符）"))
    c.add_argument("--engine", default="local", help=tr("引擎 id（默认 local；见 audiobook engines）"))
    c.add_argument("--voice", default="", help=tr("语音（显示名或 voice_id；默认取引擎首个）"))
    c.add_argument("--rate", default="+0%", help=tr("语速，如 +0%% / -20%% / +30%%"))
    c.add_argument("--split", default="chapter", choices=["chapter", "time", "single"],
                   help=tr("拆分方式（默认 chapter）"))
    c.add_argument("--time-minutes", type=int, default=30, help=tr("按时间拆分时每段分钟数"))
    c.add_argument("--out", default="output", help=tr("输出目录（默认 ./output）"))
    c.add_argument("--prefix", default=tr("有声读物"), help=tr("输出文件名前缀 / 专辑名"))
    c.add_argument("--album", default="", help=tr("专辑名（默认用 --prefix）"))
    c.add_argument("--m4b", action="store_true", help=tr("额外导出带章节的 m4b"))
    c.add_argument("--srt", action="store_true", help=tr("同时生成 srt 字幕"))
    c.add_argument("--metadata", action="store_true", help=tr("写入 ID3 元数据"))
    c.add_argument("--normalize", action="store_true", help=tr("响度归一化（需 ffmpeg）"))
    c.add_argument("--dialogue", action="store_true", help=tr("多人对话检测"))
    c.set_defaults(func=cmd_convert)

    # engines
    e = sub.add_parser("engines", help=tr("列出引擎及就绪状态"))
    e.set_defaults(func=cmd_engines)

    # voices
    v = sub.add_parser("voices", help=tr("列出某引擎的可用语音"))
    v.add_argument("--engine", default="local", help=tr("引擎 id（默认 local）"))
    v.set_defaults(func=cmd_voices)

    # config
    cf = sub.add_parser("config", help=tr("查看 / 修改配置（--set KEY=VALUE，可多次）"))
    cf.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help=tr("修改一项配置，如 --set cosyvoice_python=/path/to/python"))
    cf.set_defaults(func=cmd_config)

    # serve
    s = sub.add_parser("serve", help=tr("启动本地 Web UI（需 [web] 依赖）"))
    s.add_argument("--host", default="127.0.0.1", help=tr("监听地址（默认 127.0.0.1）"))
    s.add_argument("--port", type=int, default=8000, help=tr("监听端口（默认 8000）"))
    s.set_defaults(func=cmd_serve)

    # models
    kinds = ["piper", "cosyvoice", "whisper"]
    m = sub.add_parser("models", help=tr("模型管理：查看占用 / 下载 / 删除"))
    msub = m.add_subparsers(dest="action", required=True)
    msub.add_parser("list", help=tr("列出已安装模型及占用空间"))
    msub.add_parser("available", help=tr("列出常用可下载模型（✓ 为已安装）"))
    mg = msub.add_parser("get", help=tr("下载模型（Piper 也可填官方目录中的语音 key）"))
    mg.add_argument("kind", choices=kinds)
    mg.add_argument("id", help=tr("模型标识，如 zh_CN-huayan-medium / Fun-CosyVoice3-0.5B / base"))
    mr = msub.add_parser("rm", help=tr("删除已安装模型（仅限存储目录内）"))
    mr.add_argument("kind", choices=kinds)
    mr.add_argument("id", help=tr("见 audiobook models list 的第二列"))
    mr.add_argument("-y", "--yes", action="store_true", help=tr("不询问直接删除"))
    m.set_defaults(func=cmd_models)

    # clone
    cl = sub.add_parser("clone", help=tr("声音克隆：用一段参考音频复刻音色（需 CosyVoice 环境）"))
    csub = cl.add_subparsers(dest="action", required=True)
    csub.add_parser("list", help=tr("列出克隆音色"))
    ca = csub.add_parser("add", help=tr("从参考音频创建克隆音色（建议 5–15 秒清晰人声）"))
    ca.add_argument("name", help=tr("音色名字"))
    ca.add_argument("audio", help=tr("参考音频（wav/mp3/m4a/...）"))
    ca.add_argument("--text", default="", help=tr("参考音频的文字稿（强烈建议填写）"))
    cr = csub.add_parser("rm", help=tr("删除克隆音色"))
    cr.add_argument("id", help=tr("音色 ID（见 audiobook clone list）"))
    cr.add_argument("-y", "--yes", action="store_true", help=tr("不询问直接删除"))
    cc = csub.add_parser("check", help=tr("检查 CosyVoice 环境（解释器 / 仓库 / 模型）"))
    cc.add_argument("--load", action="store_true", help=tr("同时实际加载一次模型（较慢）"))
    cs = csub.add_parser("say", help=tr("用克隆音色合成一句话"))
    cs.add_argument("id", help=tr("音色 ID"))
    cs.add_argument("text", help=tr("要合成的文本"))
    cs.add_argument("-o", "--out", default="clone_sample.mp3", help=tr("输出文件（默认 clone_sample.mp3）"))
    cs.add_argument("--rate", default="+0%", help=tr("语速，如 +0%% / -20%%"))
    cl.set_defaults(func=cmd_clone)

    # transcribe
    t = sub.add_parser("transcribe", help=tr("语音转文字（ASR，需 faster-whisper）"))
    t.add_argument("input", help=tr("音频文件（mp3/wav/m4a/...）"))
    t.add_argument("--model", default="base", help=tr("Whisper 模型：tiny/base/small/medium/large-v3"))
    t.add_argument("--language", default="auto", help=tr("语言：auto/zh/en/ja/..."))
    t.add_argument("--format", default="txt", choices=["txt", "srt", "json"], help=tr("输出格式"))
    t.add_argument("--out", default=None, help=tr("输出文件路径（默认音频旁）"))
    t.set_defaults(func=cmd_transcribe)

    return p


def _preparse_lang(argv):
    """在构建解析器之前取出 --lang：帮助说明在构建时就要按所选语言生成。"""
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    lang = _preparse_lang(argv)
    if lang in i18n.LANGUAGE_CHOICES:
        i18n.set_language(lang)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print(tr("\n已取消"), file=sys.stderr)
        return 130
    except Exception as exc:  # CLI 顶层：打印友好错误而非 traceback
        print(tr('错误: {exc}', exc=exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
