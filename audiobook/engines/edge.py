"""audiobook.engines.edge — Edge（微软在线）TTS 引擎。

本模块持有 Edge 的全部实现：语音表、异步单段 / 多段 / 对话分段合成，以及整批生成的
异步编排（单事件循环 + 信号量并发 + 可中断重试）。`EdgeEngine.run_batch` 把整批编排
接入引擎注册表，`tts_engine.convert_batch` 不再对 Edge 做特判。`tts_engine` 保留
同名再导出以兼容旧 import。

对 `tts_engine` 共享助手（重试参数、进度落盘、逐章事件、`StopRequested` 等）一律在
函数内以 `import tts_engine as _te` 惰性引用，避免模块加载期循环导入，也让测试对
`tts_engine.MAX_RETRIES` 等的 patch 继续生效。
"""

import asyncio
import logging
import os
import shutil
import tempfile

from audiobook.engines.base import Engine
from audiobook.i18n import tr

# 可选导入 - Edge TTS（在线引擎，与其他引擎一致按可选依赖处理）
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    edge_tts = None

logger = logging.getLogger("audiobook_converter")

EDGE_VOICES = {
    "晓晓（女声，自然）": "zh-CN-XiaoxiaoNeural",
    "云希（男声，自然）": "zh-CN-YunxiNeural",
    "云健（男声，播音）": "zh-CN-YunjianNeural",
    "晓伊（女声，活泼）": "zh-CN-XiaoyiNeural",
    "云扬（男声，温暖）": "zh-CN-YunyangNeural",
    "晓辰（女声，温柔）": "zh-CN-XiaochenNeural",
}
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


# ======== 合成 ========

async def _edge_generate(text, voice, rate, output_path):
    if not EDGE_TTS_AVAILABLE:
        raise RuntimeError(tr("Edge TTS 未安装。请运行: pip install edge-tts"))
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


async def _edge_generate_multi(segments, voice, rate, output_path):
    """单个文件内多段文本串行生成（避免并发过多触发限流）"""
    from audiobook.io.audio import _merge_mp3_files
    temp_dir = tempfile.mkdtemp()
    temp_files = []
    try:
        for i, seg in enumerate(segments):
            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
            await _edge_generate(seg, voice, rate, tp)
            temp_files.append(tp)
        _merge_mp3_files(temp_files, output_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _edge_generate_dialogue(dialogue_segments, default_voice, voice_map,
                                  rate, output_path, stop_event=None):
    """对话检测模式：逐段按角色/类型切换语音生成后合并（Edge 引擎）"""
    import tts_engine as _te
    from audiobook.core.text import _resolve_segment_voice
    from audiobook.io.audio import _merge_mp3_files
    temp_dir = tempfile.mkdtemp()
    temp_files = []
    try:
        for i, seg in enumerate(dialogue_segments):
            if stop_event is not None and stop_event.is_set():
                raise _te.StopRequested(tr("用户暂停"))
            seg_voice = _resolve_segment_voice(seg, default_voice, voice_map)
            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
            await _edge_generate(seg["text"], seg_voice, rate, tp)
            temp_files.append(tp)
        _merge_mp3_files(temp_files, output_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ======== 整批生成（并发 + 单一事件循环） ========

async def _interruptible_asleep(seconds: float, stop_event: "asyncio.Event") -> bool:
    """异步版可中断睡眠。返回 True 表示被 stop_event 唤醒。"""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _generate_one_item(item, voice, rate, output_dir, semaphore, stop_event, on_start=None):
    """生成单个章节，由信号量控制并发（支持暂停）。on_start 在真正拿到并发名额时调用。"""
    import tts_engine as _te
    from audiobook.core.text import split_text
    if stop_event.is_set():
        return
    out_path = os.path.join(output_dir, item["filename"])
    segments = split_text(item["text"])

    async with semaphore:
        if stop_event.is_set():
            return
        if on_start:
            on_start()
        max_retries = _te.MAX_RETRIES
        for attempt in range(max_retries + 1):
            if stop_event.is_set():
                return
            try:
                logger.info(f"生成 [{item['title']}] → {item['filename']} (尝试 {attempt + 1}/{max_retries + 1})")

                if item.get("segments") and item.get("voice_map"):
                    # 对话检测模式：按角色/类型切换语音
                    await _edge_generate_dialogue(item["segments"], voice,
                                                  item["voice_map"], rate, out_path,
                                                  stop_event=stop_event)
                elif len(segments) == 1:
                    await _edge_generate(segments[0], voice, rate, out_path)
                else:
                    await _edge_generate_multi(segments, voice, rate, out_path)

                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    item["status"] = "done"
                    logger.info(f"完成 [{item['title']}] ({os.path.getsize(out_path)} bytes)")
                    return
                else:
                    raise RuntimeError(tr('生成文件为空: {out_path}', out_path=out_path))

            except Exception as e:
                delay = _te.RETRY_DELAY * (2 ** attempt)  # 指数退避: 5s, 10s, 20s
                logger.error(f"生成失败 [{item['title']}] 尝试{attempt + 1}/{max_retries + 1}: {e}")
                if attempt < max_retries:
                    logger.info(f"等待 {delay}秒 后重试...")
                    if await _interruptible_asleep(delay, stop_event):
                        return
                else:
                    item["status"] = "error"
                    item["error"] = str(e)


async def _edge_batch_generate(items, voice, rate, output_dir, progress_callback, should_stop,
                               item_callback=None):
    """并发批量生成，支持暂停（stop_event 即时传播）"""
    import tts_engine as _te
    pending_items = [(i, it) for i, it in enumerate(items) if _te._is_active(it)]
    total_active = len(pending_items)
    done_count = 0

    semaphore = asyncio.Semaphore(_te.get_edge_concurrency())
    stop_event = asyncio.Event()

    async def _watch_stop():
        while not stop_event.is_set():
            if should_stop and should_stop():
                logger.info("用户暂停：广播取消事件")
                stop_event.set()
                return
            await asyncio.sleep(0.2)

    async def _process_item(idx, item):
        nonlocal done_count
        started = []

        def _on_start():
            item.pop("error", None)  # 重试时清掉上次的错误信息
            started.append(True)
            _te._emit_item(item_callback, "running", index=idx)

        await _generate_one_item(item, voice, rate, output_dir, semaphore, stop_event,
                                 on_start=_on_start)
        _te._emit_item_result(item_callback, idx, item, bool(started))
        done_count += 1
        if progress_callback:
            try:
                progress_callback(done_count, total_active)
            except Exception:
                pass
        _te.save_progress(output_dir, items)

    watcher = asyncio.create_task(_watch_stop())
    tasks = [asyncio.create_task(_process_item(i, item)) for i, item in pending_items]

    try:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        stop_event.set()
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            # Python 3.8+ 中 CancelledError 继承 BaseException，`except Exception` 接不住：
            # 此前每次 Edge 批量结束都会把它抛出 convert_batch，导致界面收不到完成信号、
            # 字幕/元数据/归一化后处理不执行、进度文件不清理。
            pass
        except Exception:
            pass


class EdgeEngine(Engine):
    """Edge 在线云端语音引擎（需联网）。"""

    id = "edge"
    display_name = "Edge（联网）"

    def is_ready(self):
        import tts_engine as _te
        return _te.check_engine_ready("edge")

    def list_voices(self):
        return dict(EDGE_VOICES)

    def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
        # 单段原语（Edge 为网络合成，不支持 should_stop 中断，故忽略该参数）。
        asyncio.run(_edge_generate(text, voice, rate, out_path))

    def run_batch(self, items, voice, rate, output_dir, *, progress_callback=None,
                  should_stop=None, item_callback=None) -> bool:
        # 整批走单一事件循环并发：网络合成的瓶颈在往返延迟，并发远快于逐章串行。
        if not EDGE_TTS_AVAILABLE:
            raise RuntimeError(tr("Edge TTS 未安装。请运行: pip install edge-tts，"
                                  "或切换到离线引擎（系统语音 / Piper / CosyVoice）。"))
        asyncio.run(_edge_batch_generate(items, voice, rate, output_dir, progress_callback,
                                         should_stop, item_callback=item_callback))
        return True
