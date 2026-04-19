"""GUI界面 - 文字转有声读物 v2.1"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import os
import subprocess
import platform
import logging

from tts_engine import (
    VERSION,
    LOG_PATH,
    get_voice_list,
    get_voice_id,
    generate_preview,
    convert_batch,
    detect_chapters,
    load_progress,
    merge_mp3_files,
    logger,
)


class AudiobookConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"文字转有声读物 v{VERSION}")
        self.root.geometry("1020x750")
        self.root.minsize(860, 600)

        self.file_path = None
        self.is_converting = False
        self.should_stop = False
        self.chapters = []

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text=f"文字转有声读物 v{VERSION}", font=("Helvetica", 16, "bold")).pack(pady=(0, 8))

        body = ttk.Frame(main)
        body.pack(fill=tk.BOTH, expand=True)

        # ===== 左侧：章节列表 + 文本 =====
        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        # 章节选择区
        ch_frame = ttk.LabelFrame(left, text="章节列表（勾选要生成的章节）", padding=5)
        ch_frame.pack(fill=tk.X, pady=(0, 5))

        ch_btns = ttk.Frame(ch_frame)
        ch_btns.pack(fill=tk.X, pady=(0, 3))
        ttk.Button(ch_btns, text="全选", command=self._select_all_chapters).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(ch_btns, text="全不选", command=self._deselect_all_chapters).pack(side=tk.LEFT)
        self.chapter_count_label = ttk.Label(ch_btns, text="", foreground="gray")
        self.chapter_count_label.pack(side=tk.RIGHT)

        list_frame = ttk.Frame(ch_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.chapter_listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, height=6, font=("Helvetica", 11))
        ch_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.chapter_listbox.yview)
        self.chapter_listbox.config(yscrollcommand=ch_scroll.set)
        self.chapter_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ch_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 文本区
        text_frame = ttk.LabelFrame(left, text="文本内容", padding=5)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self.text_area = scrolledtext.ScrolledText(text_frame, wrap=tk.WORD, font=("Helvetica", 12))
        self.text_area.pack(fill=tk.BOTH, expand=True)

        # ===== 右侧：控制面板 =====
        right = ttk.LabelFrame(body, text="设置", padding=10, width=250)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))
        right.pack_propagate(False)

        # 文件
        ttk.Button(right, text="选择文本文件", command=self._select_file).pack(fill=tk.X, pady=(0, 3))
        self.file_label = ttk.Label(right, text="未选择文件", wraplength=210, foreground="gray")
        self.file_label.pack(fill=tk.X, pady=(0, 10))

        # TTS 引擎
        ttk.Label(right, text="语音引擎:").pack(anchor=tk.W)
        self.engine_var = tk.StringVar(value="edge")
        eng_frame = ttk.Frame(right)
        eng_frame.pack(fill=tk.X, pady=(2, 2))
        ttk.Radiobutton(eng_frame, text="Edge（联网）", variable=self.engine_var,
                        value="edge", command=self._on_engine_change).pack(side=tk.LEFT)
        ttk.Radiobutton(eng_frame, text="本地（离线）", variable=self.engine_var,
                        value="local", command=self._on_engine_change).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Radiobutton(eng_frame, text="Piper（离线高质量）", variable=self.engine_var,
                        value="piper", command=self._on_engine_change).pack(side=tk.LEFT, padx=(8, 0))

        # 语音
        ttk.Label(right, text="语音:").pack(anchor=tk.W, pady=(6, 0))
        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(right, textvariable=self.voice_var, state="readonly")
        self.voice_combo.pack(fill=tk.X, pady=(2, 8))
        self._on_engine_change()

        # 语速
        ttk.Label(right, text="语速:").pack(anchor=tk.W)
        rate_row = ttk.Frame(right)
        rate_row.pack(fill=tk.X, pady=(2, 2))
        self.rate_var = tk.IntVar(value=0)
        self.rate_label = ttk.Label(rate_row, text="正常")
        self.rate_label.pack(side=tk.RIGHT)
        ttk.Scale(right, from_=-50, to=50, variable=self.rate_var,
                  command=self._update_rate_label).pack(fill=tk.X, pady=(0, 8))

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=4)

        # 输出模式
        ttk.Label(right, text="输出模式:").pack(anchor=tk.W)
        self.mode_var = tk.StringVar(value="chapter")
        for label, val in [("按章节拆分", "chapter"), ("按时间拆分", "time"), ("合并为一个文件", "single")]:
            ttk.Radiobutton(right, text=label, variable=self.mode_var,
                            value=val, command=self._on_mode_change).pack(anchor=tk.W)

        self.time_frame = ttk.Frame(right)
        ttk.Label(self.time_frame, text="每段:").pack(side=tk.LEFT)
        self.time_var = tk.IntVar(value=30)
        ttk.Spinbox(self.time_frame, from_=5, to=180, textvariable=self.time_var,
                    width=5, increment=5).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.time_frame, text="分钟").pack(side=tk.LEFT)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # 操作按钮
        ttk.Button(right, text="试听（前200字）", command=self._preview).pack(fill=tk.X, pady=2)
        self.btn_convert = ttk.Button(right, text="生成MP3", command=self._start_convert)
        self.btn_convert.pack(fill=tk.X, pady=2)
        self.btn_pause = ttk.Button(right, text="暂停", command=self._pause_convert, state="disabled")
        self.btn_pause.pack(fill=tk.X, pady=2)
        self.btn_resume = ttk.Button(right, text="继续生成", command=self._resume_convert)
        self.btn_resume.pack(fill=tk.X, pady=2)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # 合并MP3
        ttk.Button(right, text="合并MP3文件", command=self._merge_mp3).pack(fill=tk.X, pady=2)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # 查看日志
        ttk.Button(right, text="查看日志", command=self._show_log).pack(fill=tk.X, pady=2)

        # 底部进度
        bottom = ttk.Frame(main)
        bottom.pack(fill=tk.X, pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill=tk.X)
        self.status_label = ttk.Label(bottom, text="就绪", foreground="gray")
        self.status_label.pack(anchor=tk.W, pady=(4, 0))

    # ===== 引擎切换 =====

    def _on_engine_change(self):
        engine = self.engine_var.get()
        voices = get_voice_list(engine)
        self.voice_combo["values"] = voices
        if voices:
            self.voice_combo.current(0)

        # Piper引擎需要ffmpeg（仅在status_label已创建后检查）
        if hasattr(self, "status_label"):
            if engine == "piper":
                from tts_engine import _check_ffmpeg
                if not _check_ffmpeg():
                    self.status_label.config(
                        text="警告: 未检测到ffmpeg，Piper引擎将无法使用",
                        foreground="red"
                    )
                else:
                    self.status_label.config(text="就绪", foreground="gray")
            else:
                self.status_label.config(text="就绪", foreground="gray")

    # ===== UI 回调 =====

    def _update_rate_label(self, value):
        val = int(float(value))
        if val == 0:
            self.rate_label.config(text="正常")
        elif val > 0:
            self.rate_label.config(text=f"快 +{val}%")
        else:
            self.rate_label.config(text=f"慢 {val}%")

    def _on_mode_change(self):
        if self.mode_var.get() == "time":
            self.time_frame.pack(fill=tk.X, pady=(2, 0))
        else:
            self.time_frame.pack_forget()

    def _select_file(self):
        path = filedialog.askopenfilename(
            title="选择文本文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        try:
            encodings = ["utf-8", "gbk", "gb2312", "latin-1"]
            content = None
            for enc in encodings:
                try:
                    with open(path, "r", encoding=enc) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue

            if content is None:
                messagebox.showerror("错误", "无法读取文件，编码不支持")
                return

            self.file_path = path
            self.text_area.delete("1.0", tk.END)
            self.text_area.insert("1.0", content)

            filename = os.path.basename(path)
            self.file_label.config(text=filename, foreground="black")
            self.status_label.config(text=f"已加载: {filename}（{len(content)}字）")
            logger.info(f"加载文件: {path} ({len(content)}字)")
            self._refresh_chapters()
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            messagebox.showerror("错误", f"读取文件失败: {e}")

    def _refresh_chapters(self):
        text = self.text_area.get("1.0", tk.END).strip()
        self.chapters = detect_chapters(text)
        self.chapter_listbox.delete(0, tk.END)
        for ch in self.chapters:
            self.chapter_listbox.insert(tk.END, ch["title"])
            self.chapter_listbox.selection_set(tk.END)
        count = len(self.chapters)
        has_titles = count > 1 or (count == 1 and self.chapters[0]["title"] != "全文")
        if has_titles:
            self.chapter_count_label.config(text=f"共 {count} 章/段")
        else:
            self.chapter_count_label.config(text="未检测到章节")
        logger.info(f"检测到 {count} 个章节/段落")

    def _select_all_chapters(self):
        self.chapter_listbox.selection_set(0, tk.END)

    def _deselect_all_chapters(self):
        self.chapter_listbox.selection_clear(0, tk.END)

    # ===== 预览 =====

    def _get_rate_string(self) -> str:
        val = self.rate_var.get()
        return f"+{val}%" if val >= 0 else f"{val}%"

    def _preview(self):
        text = self.text_area.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("提示", "请先输入或导入文字内容")
            return

        self.status_label.config(text="正在生成预览...")
        self.progress["value"] = 0

        def run():
            try:
                engine = self.engine_var.get()
                voice = get_voice_id(self.voice_var.get(), engine)
                rate = self._get_rate_string()
                path = generate_preview(text, voice, rate, engine=engine)
                self.root.after(0, lambda: self._play_audio(path))
                self.root.after(0, lambda: self.status_label.config(text="预览生成完成，正在播放..."))
            except Exception as e:
                logger.error(f"预览失败: {e}")
                self.root.after(0, lambda: messagebox.showerror("错误", f"预览失败: {e}"))
                self.root.after(0, lambda: self.status_label.config(text="预览失败"))

        threading.Thread(target=run, daemon=True).start()

    def _play_audio(self, path: str):
        try:
            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(["afplay", path])
            elif system == "Windows":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    # ===== 生成控制 =====

    def _get_selected_indices(self) -> list:
        selection = self.chapter_listbox.curselection()
        if not selection:
            return []
        return list(selection)

    def _start_convert(self):
        selected = self._get_selected_indices()
        if not selected:
            messagebox.showwarning("提示", "请至少勾选一个章节")
            return

        text = self.text_area.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("提示", "请先输入或导入文字内容")
            return

        if self.is_converting:
            messagebox.showinfo("提示", "正在转换中")
            return

        output_dir = filedialog.askdirectory(title="选择保存目录")
        if not output_dir:
            return

        file_prefix = "有声读物"
        if self.file_path:
            file_prefix = os.path.splitext(os.path.basename(self.file_path))[0]

        self._run_convert(output_dir, file_prefix, selected, resume=False)

    def _resume_convert(self):
        output_dir = filedialog.askdirectory(title="选择之前保存的目录（包含进度文件）")
        if not output_dir:
            return

        items = load_progress(output_dir)
        if not items:
            messagebox.showinfo("提示", "该目录下没有找到进度文件")
            return

        file_prefix = "有声读物"
        self._run_convert(output_dir, file_prefix, selected_indices=None, resume=True)

    def _run_convert(self, output_dir: str, file_prefix: str, selected_indices: list, resume: bool):
        self.is_converting = True
        self.should_stop = False
        self.progress["value"] = 0
        self.btn_convert.config(state="disabled")
        self.btn_pause.config(state="normal")
        self.btn_resume.config(state="disabled")
        self.status_label.config(text="准备转换..." if not resume else "准备继续转换...")

        engine = self.engine_var.get()
        voice = get_voice_id(self.voice_var.get(), engine)
        rate = self._get_rate_string()
        mode = self.mode_var.get()
        time_minutes = self.time_var.get()

        def progress_cb(current, total):
            pct = int(current / total * 100)
            self.root.after(0, lambda: self.progress.configure(value=pct))
            self.root.after(0, lambda: self.status_label.configure(
                text=f"正在处理: {current}/{total} ({pct}%)"))

        def should_stop_cb():
            return self.should_stop

        def run():
            try:
                files = convert_batch(
                    text=self.text_area.get("1.0", tk.END).strip(),
                    voice=voice,
                    rate=rate,
                    output_dir=output_dir,
                    split_mode=mode,
                    time_minutes=time_minutes,
                    file_prefix=file_prefix,
                    selected_indices=selected_indices,
                    engine=engine,
                    progress_callback=progress_cb,
                    should_stop=should_stop_cb,
                    resume=resume,
                )
                if self.should_stop:
                    self.root.after(0, lambda: self._on_pause(output_dir))
                else:
                    self.root.after(0, lambda: self._on_convert_done(output_dir, files))
            except Exception as e:
                logger.error(f"转换异常: {e}", exc_info=True)
                self.root.after(0, lambda: messagebox.showerror("错误", f"转换失败: {e}"))
                self.root.after(0, lambda: self.status_label.config(text="转换失败"))
            finally:
                self.is_converting = False
                self.root.after(0, lambda: self.btn_convert.config(state="normal"))
                self.root.after(0, lambda: self.btn_pause.config(state="disabled"))
                self.root.after(0, lambda: self.btn_resume.config(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def _pause_convert(self):
        self.should_stop = True
        self.status_label.config(text="正在暂停...")
        logger.info("用户点击暂停")

    def _on_pause(self, output_dir: str):
        self.status_label.config(text=f"已暂停，进度已保存到: {output_dir}")
        self.progress["value"] = 0
        messagebox.showinfo("暂停", f"已暂停，进度已保存。\n下次可点击「继续生成」恢复。")

    def _on_convert_done(self, output_dir: str, files: list):
        self.progress["value"] = 100
        count = len(files)
        self.status_label.config(text=f"完成! 共生成 {count} 个文件")
        logger.info(f"批量生成完成: {count} 个文件 → {output_dir}")

        names = [os.path.basename(f) for f in files[:8]]
        preview = "\n".join(names)
        if count > 8:
            preview += f"\n...共{count}个文件"

        result = messagebox.askyesno(
            "完成",
            f"已生成 {count} 个MP3文件:\n{preview}\n\n保存目录:\n{output_dir}\n\n是否打开文件夹？"
        )
        if result:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", output_dir])
            elif platform.system() == "Windows":
                os.startfile(output_dir)
            else:
                subprocess.Popen(["xdg-open", output_dir])

    # ===== 合并 MP3 =====

    def _merge_mp3(self):
        """选择多个MP3文件合并为一个"""
        files = filedialog.askopenfilenames(
            title="选择要合并的MP3文件",
            filetypes=[("MP3文件", "*.mp3"), ("所有文件", "*.*")]
        )
        if not files:
            return

        if len(files) < 2:
            messagebox.showinfo("提示", "请选择至少2个文件")
            return

        # 排序（按文件名自然顺序）
        file_list = sorted(list(files))

        output_path = filedialog.asksaveasfilename(
            title="保存合并后的MP3",
            initialfile="合并_有声读物.mp3",
            defaultextension=".mp3",
            filetypes=[("MP3文件", "*.mp3")]
        )
        if not output_path:
            return

        try:
            self.status_label.config(text="正在合并MP3...")
            merge_mp3_files(file_list, output_path)
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            self.status_label.config(text=f"合并完成: {os.path.basename(output_path)} ({size_mb:.1f}MB)")
            logger.info(f"合并完成: {len(file_list)} 个文件 → {output_path} ({size_mb:.1f}MB)")
            messagebox.showinfo("完成", f"已合并 {len(file_list)} 个文件:\n{os.path.basename(output_path)}\n\n大小: {size_mb:.1f}MB")
        except Exception as e:
            logger.error(f"合并失败: {e}")
            messagebox.showerror("错误", f"合并失败: {e}")

    # ===== 日志查看 =====

    def _show_log(self):
        """打开日志文件"""
        if os.path.exists(LOG_PATH):
            if platform.system() == "Darwin":
                subprocess.Popen(["open", LOG_PATH])
            elif platform.system() == "Windows":
                os.startfile(LOG_PATH)
            else:
                subprocess.Popen(["xdg-open", LOG_PATH])
        else:
            messagebox.showinfo("提示", "日志文件不存在")
