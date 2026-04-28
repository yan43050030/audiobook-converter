"""GUI界面 - 文字转有声读物 v3.0.0"""

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
    get_storage_dir,
    set_storage_dir,
    get_portable_bin_dir,
    add_download_listener,
    remove_download_listener,
    refresh_local_voices,
    check_engine_ready,
    scan_storage_dependencies,
    get_registered_engines,
)

from asr_engine import (
    transcribe,
    check_asr_ready,
    WHISPER_MODELS,
)
from audio_player import AudioPlayer


class ScrollableFrame(ttk.Frame):
    """一个带垂直滚动条的容器，使用方式：把控件放到 .interior 上。"""

    def __init__(self, parent, width=340, **kwargs):
        super().__init__(parent, **kwargs)
        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, width=width)
        self._vbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vbar.set)

        self._vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.interior = ttk.Frame(self._canvas)
        self._window_id = self._canvas.create_window((0, 0), window=self.interior, anchor="nw")

        self.interior.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)

        # 鼠标滚轮：指针在控件上时启用，离开时解绑，避免影响其它滚动区
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _on_inner_config(self, _event):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event):
        # 让内部 frame 宽度始终与 canvas 可见宽度一致
        self._canvas.itemconfigure(self._window_id, width=event.width)

    def _bind_wheel(self, _event):
        # Windows / macOS
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)
        # Linux (X11)
        self._canvas.bind_all("<Button-4>", lambda e: self._on_wheel_button(e, -3))
        self._canvas.bind_all("<Button-5>", lambda e: self._on_wheel_button(e, 3))

    def _unbind_wheel(self, _event):
        self._canvas.unbind_all("<MouseWheel>")
        self._canvas.unbind_all("<Button-4>")
        self._canvas.unbind_all("<Button-5>")

    def _is_inner_scrollable(self, x_root: int, y_root: int) -> bool:
        """指针下若是 Text / Listbox / Treeview 等可滚动控件，则让它自己处理"""
        try:
            w = self.winfo_containing(x_root, y_root)
            cur = w
            while cur is not None and cur != self:
                cls = ""
                try:
                    cls = cur.winfo_class()
                except Exception:
                    pass
                if cls in ("Text", "Listbox", "Treeview"):
                    return True
                try:
                    cur = cur.master
                except Exception:
                    break
        except Exception:
            pass
        return False

    def _on_wheel(self, event):
        if self._is_inner_scrollable(event.x_root, event.y_root):
            return
        delta = event.delta
        # macOS delta 很小，Windows 一般是 120 的倍数
        if platform.system() == "Darwin":
            self._canvas.yview_scroll(int(-delta), "units")
        else:
            self._canvas.yview_scroll(int(-delta / 120), "units")

    def _on_wheel_button(self, event, direction: int):
        """X11 Button-4/5：与 _on_wheel 同样让位 Text/Listbox/Treeview"""
        if self._is_inner_scrollable(event.x_root, event.y_root):
            return
        self._canvas.yview_scroll(direction, "units")


class AudiobookConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"文字转有声读物 v{VERSION}")
        self.root.geometry("1100x750")
        self.root.minsize(860, 600)

        self.file_paths: list = []  # list[dict{path, name, content, encoding}]
        self._single_file_path = None  # backwards compat: last single file path
        self.is_converting = False
        self.should_stop = False
        self.chapters = []
        self._last_download_desc = ""
        self._ext_engine_widgets = []

        # 内置音频播放器
        self.player = AudioPlayer(on_state_change=self._on_player_state)
        # 全文试听状态：'idle' / 'generating' / 'playing' / 'paused'
        self._preview_state = "idle"
        self._preview_should_stop = False

        self._build_ui()
        self._configure_styles()
        self._bind_shortcuts()
        self._restore_window_geometry()

        # 订阅下载进度事件（模型/依赖下载时触发）
        add_download_listener(self._on_download_progress)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_sash_position(self, paned: "ttk.PanedWindow"):
        """让右侧面板初始约占 30% 宽度（最少 320px、最多 520px）"""
        try:
            paned.update_idletasks()
            total = paned.winfo_width()
            if total <= 0:
                return
            right_width = max(320, min(int(total * 0.3), 520))
            sash_x = total - right_width
            paned.sashpos(0, sash_x)
        except Exception as e:
            logger.debug(f"初始化分隔条位置失败: {e}")

    def _on_close(self):
        try:
            remove_download_listener(self._on_download_progress)
        except Exception:
            pass
        # 保存窗口几何信息
        try:
            from tts_engine import _load_config as _lc, _save_config as _sc
            cfg = _lc()
            cfg["window_geometry"] = self.root.geometry()
            _sc(cfg)
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        self.root.destroy()

    def _bind_shortcuts(self):
        """绑定键盘快捷键"""
        self.root.bind("<Control-o>", lambda e: self._add_files())
        self.root.bind("<Control-s>", lambda e: self._start_convert() if not self.is_converting else None)
        self.root.bind("<Control-p>", lambda e: self._preview())
        self.root.bind("<Control-m>", lambda e: self._merge_mp3())
        self.root.bind("<Control-l>", lambda e: self._show_log())
        self.root.bind("<Control-a>", lambda e: self._select_all_chapters())
        self.root.bind("<Control-f>", lambda e: self._focus_chapter_search())
        self.root.bind("<Escape>", lambda e: self._pause_convert() if self.is_converting else None)

    def _focus_chapter_search(self):
        """聚焦到章节搜索框"""
        try:
            self.chapter_search_var.set("")
            # 找到搜索 Entry 并聚焦
            for w in self.root.winfo_children():
                for c in w.winfo_children():
                    if isinstance(c, ttk.Entry) and c.get() == "":
                        c.focus_set()
                        return
        except Exception:
            pass

    def _filter_chapters(self):
        """根据搜索关键词过滤章节列表"""
        query = self.chapter_search_var.get().lower()
        self._refresh_chapters_list(filter_text=query)

    def _refresh_chapters_list(self, filter_text: str = ""):
        """刷新章节列表显示，支持过滤"""
        self.chapter_tree.delete(*self.chapter_tree.get_children())
        for idx, ch in enumerate(self.chapters):
            if filter_text and filter_text not in ch["title"].lower():
                continue
            display = ch["title"]
            source = ch.get("source", "")
            if source and len(self.file_paths) > 1:
                display = f"[{source}] {display}"
            self.chapter_tree.insert("", tk.END, text=display)
        for item in self.chapter_tree.get_children():
            self.chapter_tree.selection_add(item)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # 主标题已在窗口标题栏显示，避免界面顶部重复占用纵向空间
        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True)

        self.tts_tab = ttk.Frame(notebook)
        notebook.add(self.tts_tab, text=" 文字转语音 ")
        self.asr_tab = ttk.Frame(notebook)
        notebook.add(self.asr_tab, text=" 语音转文字 ")

        # ===== TTS 标签页：左右两栏放进 PanedWindow =====
        body = ttk.PanedWindow(self.tts_tab, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        # ===== 左侧：章节列表 + 文本 =====
        left = ttk.Frame(body)
        body.add(left, weight=3)

        # 章节选择区
        ch_frame = ttk.LabelFrame(left, text="章节列表（勾选要生成的章节）", padding=5)
        ch_frame.pack(fill=tk.X, pady=(0, 5))

        ch_btns = ttk.Frame(ch_frame)
        ch_btns.pack(fill=tk.X, pady=(0, 3))
        ttk.Button(ch_btns, text="全选", command=self._select_all_chapters).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ch_btns, text="全不选", command=self._deselect_all_chapters).pack(side=tk.LEFT)
        self.chapter_count_label = ttk.Label(ch_btns, text="", foreground="gray")
        self.chapter_count_label.pack(side=tk.RIGHT)

        # 章节搜索
        search_frame = ttk.Frame(ch_frame)
        search_frame.pack(fill=tk.X, pady=(0, 3))
        self.chapter_search_var = tk.StringVar()
        self.chapter_search_var.trace("w", lambda *a: self._filter_chapters())
        ttk.Entry(search_frame, textvariable=self.chapter_search_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(search_frame, text="🔍 过滤", font=("Helvetica", 9), foreground="gray").pack(side=tk.RIGHT, padx=(4, 0))

        list_frame = ttk.Frame(ch_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.chapter_tree = ttk.Treeview(list_frame, columns=(), show="tree", selectmode="extended", height=6)
        ch_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.chapter_tree.yview)
        self.chapter_tree.configure(yscrollcommand=ch_scroll.set)
        self.chapter_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ch_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 文本区
        text_frame = ttk.LabelFrame(left, text="文本内容", padding=5)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self.text_area = scrolledtext.ScrolledText(text_frame, wrap=tk.WORD, font=("Helvetica", 12))
        self.text_area.pack(fill=tk.BOTH, expand=True)

        # ===== 右侧：可滚动 + 可拖动调整宽度的控制面板 =====
        right_outer = ttk.LabelFrame(body, text="设置（拖动左侧分隔条调整宽度）", padding=4)
        body.add(right_outer, weight=1)
        # 初始建议宽度：让 PanedWindow 给一个合适的初始 sash 位置
        self.root.after(50, lambda: self._init_sash_position(body))
        self._right_scroller = ScrollableFrame(right_outer, width=320)
        self._right_scroller.pack(fill=tk.BOTH, expand=True)
        right = self._right_scroller.interior
        pad = 4  # 标准化内边距单位

        # ===== 外观 =====
        appearance = ttk.LabelFrame(right, text="外观", padding=pad)
        appearance.pack(fill=tk.X, pady=(0, pad))
        self._theme_btn = ttk.Button(appearance, text="🔄 切换深色/浅色主题",
                                     command=self._toggle_theme)
        self._theme_btn.pack(fill=tk.X)

        # ===== 文件 =====
        files_group = ttk.LabelFrame(right, text="文件", padding=pad)
        files_group.pack(fill=tk.X, pady=(0, pad))
        file_import_frame = ttk.Frame(files_group)
        file_import_frame.pack(fill=tk.X, pady=(0, pad))
        ttk.Button(file_import_frame, text="📂 添加文件（可多选）", command=self._add_files).pack(side=tk.LEFT, padx=(0, pad))
        ttk.Button(file_import_frame, text="🗑 移除选中", command=self._remove_selected_file).pack(side=tk.LEFT)
        self.file_tree = ttk.Treeview(files_group, columns=("name",), show="tree", height=4)
        self.file_tree.pack(fill=tk.X, pady=(0, pad))
        self.file_tree.bind("<Delete>", lambda e: self._remove_selected_file())
        self.file_count_label = ttk.Label(files_group, text="未加载文件", foreground="gray")
        self.file_count_label.pack(fill=tk.X)

        # ===== 引擎与语音 =====
        engine_group = ttk.LabelFrame(right, text="引擎与语音", padding=pad)
        engine_group.pack(fill=tk.X, pady=(0, pad))
        ttk.Label(engine_group, text="语音引擎:", style="Section.TLabel").pack(anchor=tk.W, pady=(0, pad))
        self.engine_var = tk.StringVar(value="edge")
        ttk.Radiobutton(engine_group, text="Edge（联网）", variable=self.engine_var,
                        value="edge", command=self._on_engine_change).pack(anchor=tk.W)
        ttk.Radiobutton(engine_group, text="本地（离线）", variable=self.engine_var,
                        value="local", command=self._on_engine_change).pack(anchor=tk.W)
        ttk.Radiobutton(engine_group, text="Piper（离线高质量）", variable=self.engine_var,
                        value="piper", command=self._on_engine_change).pack(anchor=tk.W)
        self._ext_engine_frame = ttk.Frame(engine_group)
        self._ext_engine_frame.pack(fill=tk.X)
        # 没有外挂引擎时，给一个引导按钮，让用户能配置 CosyVoice 等
        ext_btn_row = ttk.Frame(engine_group)
        ext_btn_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Button(ext_btn_row, text="+ 添加 / 配置外挂引擎",
                   command=self._open_external_dialog).pack(fill=tk.X)
        # 引擎状态
        self.engine_status_label = ttk.Label(engine_group, text="", wraplength=280, foreground="gray")
        self.engine_status_label.pack(fill=tk.X, pady=(pad, 0))
        # 语音选择
        voice_row = ttk.Frame(engine_group)
        voice_row.pack(fill=tk.X, pady=(pad, 0))
        ttk.Label(voice_row, text="语音:").pack(side=tk.LEFT)
        ttk.Button(voice_row, text="试听", width=5, command=self._preview_voice_sample).pack(side=tk.RIGHT, padx=(0, pad))
        ttk.Button(voice_row, text="刷新", width=5, command=self._refresh_voices).pack(side=tk.RIGHT)
        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(engine_group, textvariable=self.voice_var, state="readonly")
        self.voice_combo.pack(fill=tk.X, pady=(pad, 0))
        self._on_engine_change()

        # ===== 存储与依赖 =====
        storage_group = ttk.LabelFrame(right, text="存储与依赖", padding=pad)
        storage_group.pack(fill=tk.X, pady=(0, pad))
        ttk.Label(storage_group, text="便携存储目录:", style="Section.TLabel").pack(anchor=tk.W, pady=(0, pad))
        self.storage_var = tk.StringVar(value=get_storage_dir())
        ttk.Entry(storage_group, textvariable=self.storage_var, state="readonly").pack(fill=tk.X, pady=(0, pad))
        storage_btns = ttk.Frame(storage_group)
        storage_btns.pack(fill=tk.X, pady=(0, pad))
        ttk.Button(storage_btns, text="选择文件夹", command=self._choose_storage).pack(side=tk.LEFT, padx=(0, pad))
        ttk.Button(storage_btns, text="打开", command=self._open_storage).pack(side=tk.LEFT, padx=(0, pad))
        ttk.Button(storage_btns, text="恢复默认", command=self._reset_storage).pack(side=tk.LEFT)
        ttk.Label(
            storage_group,
            text="bin/ 存放可执行文件；piper-models/ 存放语音包。程序自动在子目录搜索。",
            wraplength=280, foreground="gray",
        ).pack(fill=tk.X, pady=(0, pad))
        # 依赖检测面板（内置滚动条，内容多时可单独滚动）
        deps_frame = ttk.LabelFrame(storage_group, text="依赖检测", padding=pad)
        deps_frame.pack(fill=tk.X)
        deps_inner = ttk.Frame(deps_frame)
        deps_inner.pack(fill=tk.BOTH, expand=True)
        self.deps_text = tk.Text(deps_inner, height=8, wrap=tk.WORD, relief=tk.FLAT,
                                 font=("Helvetica", 10), bg="#ffffff")
        deps_sb = ttk.Scrollbar(deps_inner, orient=tk.VERTICAL, command=self.deps_text.yview)
        self.deps_text.configure(yscrollcommand=deps_sb.set)
        self.deps_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        deps_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.deps_text.configure(state="disabled")
        ttk.Button(deps_frame, text="⚙ 重新扫描依赖",
                   command=self._refresh_deps).pack(fill=tk.X, pady=(pad, 0))
        self._refresh_deps()

        # ===== 语速与输出 =====
        output_group = ttk.LabelFrame(right, text="语速与输出", padding=pad)
        output_group.pack(fill=tk.X, pady=(0, pad))
        ttk.Label(output_group, text="语速:").pack(anchor=tk.W)
        rate_row = ttk.Frame(output_group)
        rate_row.pack(fill=tk.X)
        self.rate_var = tk.IntVar(value=0)
        self.rate_label = ttk.Label(rate_row, text="正常")
        self.rate_label.pack(side=tk.RIGHT)
        ttk.Scale(output_group, from_=-50, to=50, variable=self.rate_var,
                  command=self._update_rate_label).pack(fill=tk.X, pady=(0, pad))
        ttk.Label(output_group, text="输出模式:").pack(anchor=tk.W)
        self.mode_var = tk.StringVar(value="chapter")
        for label, val in [("按章节拆分", "chapter"), ("按时间拆分", "time"), ("合并为一个文件", "single")]:
            ttk.Radiobutton(output_group, text=label, variable=self.mode_var,
                            value=val, command=self._on_mode_change).pack(anchor=tk.W)
        self.time_frame = ttk.Frame(output_group)
        ttk.Label(self.time_frame, text="每段:").pack(side=tk.LEFT)
        self.time_var = tk.IntVar(value=30)
        ttk.Spinbox(self.time_frame, from_=5, to=180, textvariable=self.time_var,
                    width=5, increment=5).pack(side=tk.LEFT, padx=pad)
        ttk.Label(self.time_frame, text="分钟").pack(side=tk.LEFT)

        # ===== 操作 =====
        actions_group = ttk.LabelFrame(right, text="操作", padding=pad)
        actions_group.pack(fill=tk.X)
        self.btn_preview_full = ttk.Button(
            actions_group, text="🔊 试听全文（可暂停）", command=self._toggle_preview_full
        )
        self.btn_preview_full.pack(fill=tk.X, pady=(0, pad))
        self.btn_convert = ttk.Button(actions_group, text="▶ 生成MP3", command=self._start_convert,
                                       style="Accent.TButton")
        self.btn_convert.pack(fill=tk.X, pady=(0, pad))
        self.btn_pause = ttk.Button(actions_group, text="⏹ 暂停", command=self._pause_convert, state="disabled")
        self.btn_pause.pack(fill=tk.X, pady=(0, pad))
        self.btn_resume = ttk.Button(actions_group, text="▶ 继续生成", command=self._resume_convert)
        self.btn_resume.pack(fill=tk.X, pady=(0, pad))
        ttk.Button(actions_group, text="🔀 合并MP3文件", command=self._merge_mp3).pack(fill=tk.X, pady=(0, pad))
        ttk.Button(actions_group, text="📋 查看日志", command=self._show_log).pack(fill=tk.X)

        # 底部进度和状态
        bottom_frame = ttk.LabelFrame(main, text="状态", padding=pad)
        bottom_frame.pack(fill=tk.X, pady=(pad, 0))
        self.progress = ttk.Progressbar(bottom_frame, mode="determinate", style="Vertical.TProgressbar")
        self.progress.pack(fill=tk.X, pady=(0, pad))
        self.status_label = ttk.Label(bottom_frame, text="就绪", style="Status.TLabel")
        self.status_label.pack(anchor=tk.W)

        # 下载进度（仅在下载模型/依赖时显示）
        self.download_frame = ttk.Frame(bottom_frame)
        self.download_label = ttk.Label(self.download_frame, text="", foreground="#0066cc")
        self.download_label.pack(anchor=tk.W)
        self.download_progress = ttk.Progressbar(self.download_frame, mode="determinate")
        self.download_progress.pack(fill=tk.X, pady=(pad, 0))
        # 默认隐藏

        # 扫描并添加外部引擎插件
        self._rebuild_external_engines()

        # ===== ASR 标签页 =====
        self._build_asr_tab()

    # ===== 存储目录 / 刷新语音 / 下载进度 =====

    def _choose_storage(self):
        path = filedialog.askdirectory(title="选择便携存储文件夹（建议放在U盘或外置硬盘）")
        if not path:
            return
        try:
            set_storage_dir(path)
        except Exception as e:
            logger.error(f"设置存储目录失败: {e}")
            messagebox.showerror("错误", f"设置失败: {e}")
            return
        self.storage_var.set(get_storage_dir())
        # 重新扫描依赖 + 评估当前引擎可用性
        self._refresh_deps()
        messagebox.showinfo(
            "已切换存储目录",
            f"当前目录：{get_storage_dir()}\n\n"
            "可在该目录下：\n"
            "  - bin/            放置 ffmpeg、piper 等可执行文件\n"
            "  - piper-models/   放置或缓存 Piper 语音包\n\n"
            "程序已自动在子目录中搜索依赖，结果见「依赖检测」区。",
        )

    def _reset_storage(self):
        if not messagebox.askyesno("确认", "恢复为默认存储目录（用户主目录）？"):
            return
        set_storage_dir("")
        self.storage_var.set(get_storage_dir())
        self._refresh_deps()

    def _open_storage(self):
        path = get_storage_dir()
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showinfo("目录", path)
            logger.warning(f"打开目录失败: {e}")

    def _refresh_voices(self):
        """重新检测当前引擎的可用语音"""
        if self.engine_var.get() == "local":
            refresh_local_voices()
        self._on_engine_change()

    # ===== 依赖检测（递归扫描便携目录） =====

    def _refresh_deps(self):
        """扫描便携存储目录及子目录，刷新依赖状态显示。"""
        try:
            info = scan_storage_dependencies()
        except Exception as e:
            logger.error(f"依赖扫描失败: {e}")
            self._set_deps_text(f"扫描失败: {e}", color="red")
            return

        def short(p):
            if not p:
                return "未找到"
            storage = info["storage_dir"]
            if p.startswith(storage):
                return "…" + p[len(storage):]
            return p

        lines = []
        def add(label, found):
            mark = "✓" if found else "✗"
            lines.append(f"{mark} {label}: {short(found)}")

        add("ffmpeg", info["ffmpeg"])
        add("ffprobe", info["ffprobe"])
        if info["piper_python"]:
            lines.append("✓ Piper Python 包：已安装")
        else:
            add("Piper 可执行文件", info["piper_cli"])
        lines.append(f"Piper 语音包：{len(info['piper_models'])} 个")
        for m in info["piper_models"][:4]:
            lines.append(f"   • {os.path.basename(m)}")
        if len(info["piper_models"]) > 4:
            lines.append(f"   … 另有 {len(info['piper_models']) - 4} 个")

        # GPU 状态
        gpu = info.get("gpu_status", {})
        if gpu.get("cuda_available"):
            lines.append("✓ CUDA 可用（GPU 加速）")
        else:
            lines.append("○ CUDA: 未检测到（仅 CPU）")
        if gpu.get("onnxruntime_gpu"):
            lines.append("✓ onnxruntime GPU 可用")

        # 外部插件引擎
        ext = info.get("external_engines", {})
        if ext:
            lines.append(f"外部引擎：{len(ext)} 个")
            for eid, einfo in ext.items():
                vcount = len(einfo.get("voices", []))
                lines.append(f"   • {einfo['name']}（{vcount} 个语音）")
        elif os.path.isdir(os.path.join(info["storage_dir"], "engines")):
            lines.append("○ 外部引擎目录存在，无可用引擎")

        if info["missing"]:
            lines.append("")
            lines.append("缺少：")
            for m in info["missing"]:
                lines.append(f"   - {m}")

        text = "\n".join(lines)
        color = "red" if info["missing"] else "black"
        self._set_deps_text(text, color=color)
        # 依赖更新后重估当前引擎可用性，并重建外部引擎按钮
        self._rebuild_external_engines()
        self._on_engine_change()

    def _set_deps_text(self, text: str, color: str = "black"):
        if not hasattr(self, "deps_text"):
            return
        self.deps_text.configure(state="normal")
        self.deps_text.delete("1.0", tk.END)
        self.deps_text.insert("1.0", text)
        self.deps_text.configure(state="disabled", foreground=color)

    # ===== 试听当前语音（不依赖已加载文本） =====

    SAMPLE_PREVIEW_TEXT = "你好，这是一段语音试听示例。春江潮水连海平，海上明月共潮生。"

    def _preview_voice_sample(self):
        """用固定样例句试听当前所选语音，方便在挑选语音时快速比较"""
        engine = self.engine_var.get()
        ready, msg = check_engine_ready(engine)
        if not ready:
            messagebox.showerror("引擎不可用", msg)
            return
        voice_display = self.voice_var.get()
        if not voice_display:
            messagebox.showwarning("提示", "当前引擎没有可用语音")
            return

        self.status_label.config(text="正在生成语音试听...")

        def run():
            try:
                voice = get_voice_id(voice_display, engine)
                rate = self._get_rate_string()
                path = generate_preview(self.SAMPLE_PREVIEW_TEXT, voice, rate, engine=engine)
                self.root.after(0, lambda: self.player.play(path))
                self.root.after(0, lambda: self.status_label.config(text="语音试听播放中..."))
            except Exception as e:
                logger.error(f"语音试听失败: {e}")
                self.root.after(0, lambda: messagebox.showerror("错误", f"语音试听失败: {e}"))
                self.root.after(0, lambda: self.status_label.config(text="语音试听失败"))

        threading.Thread(target=run, daemon=True).start()

    # ===== 下载进度回调 =====

    def _on_download_progress(self, description: str, current: int, total: int):
        """来自 tts_engine 的下载进度回调（可能在任意线程触发），转到主线程更新 UI"""
        self.root.after(0, lambda: self._update_download_ui(description, current, total))

    def _update_download_ui(self, description: str, current: int, total: int):
        # 首次出现时显示进度条
        if not self.download_frame.winfo_ismapped():
            self.download_frame.pack(fill=tk.X, pady=(4, 0))

        if total > 0:
            pct = min(int(current / total * 100), 100)
            mb_cur = current / (1024 * 1024)
            mb_tot = total / (1024 * 1024)
            self.download_progress.configure(mode="determinate", value=pct, maximum=100)
            self.download_label.configure(text=f"下载中 {description}: {mb_cur:.1f}/{mb_tot:.1f} MB ({pct}%)")
            if current >= total:
                # 完成：延迟隐藏
                self.root.after(1500, self._hide_download_ui)
        else:
            # 未知总大小：不确定模式
            mb_cur = current / (1024 * 1024)
            self.download_progress.configure(mode="indeterminate")
            try:
                self.download_progress.start(80)
            except Exception:
                pass
            self.download_label.configure(text=f"下载中 {description}: {mb_cur:.1f} MB")

    def _hide_download_ui(self):
        try:
            self.download_progress.stop()
        except Exception:
            pass
        self.download_progress.configure(mode="determinate", value=0)
        self.download_label.configure(text="")
        if self.download_frame.winfo_ismapped():
            self.download_frame.pack_forget()

    # ===== 引擎切换 =====

    def _restore_window_geometry(self):
        """从配置恢复窗口位置和大小"""
        try:
            from tts_engine import _load_config as _lc
            cfg = _lc()
            geom = cfg.get("window_geometry")
            if geom:
                self.root.geometry(geom)
        except Exception:
            pass

    # ===== 外挂引擎设置对话框 =====

    EXTERNAL_PRESETS = {
        "Custom（自定义）": {
            "engine_id": "my_engine",
            "name": "我的外挂引擎",
            "description": "自定义命令行 TTS",
            "args_template": "--text {text} --voice {voice} --output {output}",
            "voices": "默认音色",
            "output_format": "wav",
            "text_via_stdin": False,
        },
        "CosyVoice（推荐）": {
            "engine_id": "cosyvoice",
            "name": "CosyVoice",
            "description": "阿里通义实验室 CosyVoice",
            "args_template": "--text {text} --voice {voice} --output {output}",
            "voices": "中文女声-默认\n中文男声-默认\n克隆音色-自定义1",
            "output_format": "wav",
            "text_via_stdin": False,
        },
        "GPT-SoVITS": {
            "engine_id": "gpt_sovits",
            "name": "GPT-SoVITS",
            "description": "GPT-SoVITS 推理 CLI",
            "args_template": "--text {text} --voice {voice} --output {output}",
            "voices": "默认音色",
            "output_format": "wav",
            "text_via_stdin": False,
        },
    }

    def _open_external_dialog(self):
        """添加 / 配置外挂引擎。保存为 {storage_dir}/engines/<id>/engine.json + 包装脚本。"""
        win = tk.Toplevel(self.root)
        win.title("添加外挂引擎")
        win.geometry("560x600")
        win.transient(self.root)
        win.grab_set()

        pad = {"padx": 10, "pady": 4}

        # 预设
        ttk.Label(win, text="预设模板：").pack(anchor=tk.W, **pad)
        preset_var = tk.StringVar(value="CosyVoice（推荐）")
        preset_combo = ttk.Combobox(win, textvariable=preset_var, state="readonly",
                                    values=list(self.EXTERNAL_PRESETS.keys()))
        preset_combo.pack(fill=tk.X, **pad)

        # 引擎 ID
        ttk.Label(win, text="引擎 ID（小写英文/数字，将作为 engines/<ID>/ 目录名）：").pack(anchor=tk.W, **pad)
        id_var = tk.StringVar()
        ttk.Entry(win, textvariable=id_var).pack(fill=tk.X, **pad)

        # 显示名
        ttk.Label(win, text="显示名称：").pack(anchor=tk.W, **pad)
        name_var = tk.StringVar()
        ttk.Entry(win, textvariable=name_var).pack(fill=tk.X, **pad)

        # 可执行文件
        ttk.Label(win, text="可执行文件（绝对路径，将复制到 engines/<id>/）：").pack(anchor=tk.W, **pad)
        exe_row = ttk.Frame(win)
        exe_row.pack(fill=tk.X, **pad)
        exe_var = tk.StringVar()
        ttk.Entry(exe_row, textvariable=exe_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def browse_exe():
            p = filedialog.askopenfilename(title="选择外挂引擎可执行文件 / 包装脚本")
            if p:
                exe_var.set(p)
        ttk.Button(exe_row, text="浏览…", command=browse_exe).pack(side=tk.LEFT, padx=(4, 0))

        # 参数模板
        ttk.Label(win, text="参数模板（占位符：{text} {voice} {output} {rate}）：").pack(anchor=tk.W, **pad)
        args_var = tk.StringVar()
        ttk.Entry(win, textvariable=args_var).pack(fill=tk.X, **pad)

        # 语音列表
        ttk.Label(win, text="语音列表（每行一个）：").pack(anchor=tk.W, **pad)
        voices_text = tk.Text(win, height=5)
        voices_text.pack(fill=tk.BOTH, expand=True, **pad)

        # 输出格式 + stdin
        opts = ttk.Frame(win)
        opts.pack(fill=tk.X, **pad)
        ttk.Label(opts, text="输出格式：").pack(side=tk.LEFT)
        fmt_var = tk.StringVar(value="wav")
        ttk.Combobox(opts, textvariable=fmt_var, state="readonly",
                     values=["wav", "mp3"], width=6).pack(side=tk.LEFT, padx=(4, 12))
        stdin_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="文本走标准输入（{text} 占位符置空）",
                        variable=stdin_var).pack(side=tk.LEFT)

        def apply_preset(_event=None):
            tpl = self.EXTERNAL_PRESETS.get(preset_var.get())
            if not tpl:
                return
            id_var.set(tpl["engine_id"])
            name_var.set(tpl["name"])
            args_var.set(tpl["args_template"])
            voices_text.delete("1.0", tk.END)
            voices_text.insert("1.0", tpl["voices"])
            fmt_var.set(tpl["output_format"])
            stdin_var.set(tpl["text_via_stdin"])
        preset_combo.bind("<<ComboboxSelected>>", apply_preset)
        apply_preset()  # 初始化

        # 按钮
        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, pady=(8, 8), padx=10)

        def save_and_close():
            import re as _re
            eid = (id_var.get() or "").strip().lower()
            if not _re.match(r"^[a-z0-9_-]+$", eid):
                messagebox.showerror("错误", "引擎 ID 只能用小写英文、数字、下划线/连字符")
                return
            exe = (exe_var.get() or "").strip()
            if not exe or not os.path.isfile(exe):
                messagebox.showerror("错误", "请选择有效的可执行文件")
                return

            engines_dir = os.path.join(get_storage_dir(), "engines", eid)
            os.makedirs(engines_dir, exist_ok=True)
            # 复制可执行文件（保持原名，便于自动检测）
            import shutil as _sh
            target_exe = os.path.join(engines_dir, os.path.basename(exe))
            try:
                _sh.copy2(exe, target_exe)
                # 设置可执行位（非 Windows）
                if platform.system() != "Windows":
                    os.chmod(target_exe, 0o755)
            except Exception as e:
                messagebox.showerror("复制失败", str(e))
                return

            # 写 engine.json + 自定义参数模板（自定义字段，便于后续读取）
            voices_list = [v.strip() for v in voices_text.get("1.0", tk.END).splitlines() if v.strip()]
            meta = {
                "name": name_var.get().strip() or eid,
                "description": "用户配置的外挂引擎",
                "version": "1.0.0",
                "args_template": args_var.get().strip(),
                "voices": voices_list,
                "output_format": fmt_var.get(),
                "text_via_stdin": bool(stdin_var.get()),
            }
            try:
                with open(os.path.join(engines_dir, "engine.json"), "w", encoding="utf-8") as f:
                    import json as _j
                    _j.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("写元数据失败", str(e))
                return

            win.destroy()
            messagebox.showinfo(
                "已添加",
                f"外挂引擎已写入：\n{engines_dir}\n\n"
                "现在可以在「语音引擎」分组中看到对应单选按钮。",
            )
            self._rebuild_external_engines()
            self._refresh_deps()

        ttk.Button(btns, text="保存", command=save_and_close).pack(side=tk.RIGHT)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 6))

    def _rebuild_external_engines(self):
        """扫描并动态添加外部引擎单选按钮"""
        if not hasattr(self, "_ext_engine_frame"):
            return
        # 清除旧的
        for w in getattr(self, "_ext_engine_widgets", []):
            try:
                w.destroy()
            except Exception:
                pass
        self._ext_engine_widgets = []

        engines = get_registered_engines()
        for eid, info in engines.items():
            if info["type"] != "external":
                continue
            rb = ttk.Radiobutton(
                self._ext_engine_frame, text=f"⚡ {info['name']}（外挂）",
                variable=self.engine_var, value=eid,
                command=self._on_engine_change,
            )
            rb.pack(anchor=tk.W)
            self._ext_engine_widgets.append(rb)

    def _on_engine_change(self):
        engine = self.engine_var.get()
        voices = get_voice_list(engine)
        self.voice_combo["values"] = voices
        if voices:
            self.voice_combo.current(0)

        # 检测引擎可用性并更新状态和按钮
        if hasattr(self, "engine_status_label"):
            ready, msg = check_engine_ready(engine)
            if ready:
                self.engine_status_label.config(text=msg, foreground="green")
            else:
                self.engine_status_label.config(text=msg, foreground="red")

            # 根据引擎可用性控制按钮
            state = "normal" if ready else "disabled"
            if hasattr(self, "btn_convert"):
                self.btn_convert.config(state=state)

        if hasattr(self, "status_label"):
            self.status_label.config(text="就绪", foreground="gray")

    def _apply_dark_mode_to_tk_widgets(self, is_dark: bool):
        """手动更新非 ttk 控件（Text/ScrolledText）的配色以适配深色/浅色主题"""
        if is_dark:
            bg, fg, ins = "#1e1e1e", "#e0e0e0", "#e0e0e0"
        else:
            bg, fg, ins = "#ffffff", "#000000", "#000000"
        for widget in (self.text_area, self.asr_result_text):
            try:
                widget.configure(bg=bg, fg=fg, insertbackground=ins)
            except Exception:
                pass
        try:
            self.deps_text.configure(bg=bg, fg=fg)
        except Exception:
            pass

    def _configure_styles(self):
        """配置 ttk 自定义样式"""
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Helvetica", 16, "bold"))
        style.configure("Section.TLabel", font=("Helvetica", 11, "bold"))
        style.configure("Accent.TButton", font=("Helvetica", 10, "bold"))
        style.configure("Status.TLabel", foreground="gray", font=("Helvetica", 10))
        style.configure("Vertical.TProgressbar", thickness=12)

    def _toggle_theme(self):
        """切换深色/浅色主题"""
        import sv_ttk
        current = sv_ttk.get_theme()
        new = "dark" if current == "light" else "light"
        sv_ttk.set_theme(new)
        self._apply_dark_mode_to_tk_widgets(new == "dark")
        # 持久化
        try:
            from tts_engine import _load_config as _lc, _save_config as _sc
            cfg = _lc()
            cfg["theme"] = new
            _sc(cfg)
        except Exception:
            pass

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
            self.time_frame.pack(fill=tk.X, pady=(4, 0))
        else:
            self.time_frame.pack_forget()

    def _add_files(self):
        """添加一个或多个文件（多选）"""
        paths = filedialog.askopenfilenames(
            title="选择一个或多个文本文件",
            filetypes=[
                ("支持的文档", "*.txt *.md *.markdown *.docx *.epub *.html *.htm *.pdf"),
                ("纯文本", "*.txt"),
                ("Markdown", "*.md *.markdown"),
                ("Word 文档", "*.docx"),
                ("ePub 电子书", "*.epub"),
                ("HTML 页面", "*.html *.htm"),
                ("PDF 文档", "*.pdf"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        existing = {f["path"] for f in self.file_paths}
        for path in paths:
            if path not in existing:
                self._load_file(path)
        self._rebuild_file_tree()
        self._reconcile_text()

    def _remove_selected_file(self):
        """从列表中移除选中的文件"""
        sel = self.file_tree.selection()
        if not sel:
            return
        for item_id in sel:
            values = self.file_tree.item(item_id, "values")
            if values:
                path = values[0]
                self.file_paths = [f for f in self.file_paths if f["path"] != path]
        self._rebuild_file_tree()
        self._reconcile_text()

    def _rebuild_file_tree(self):
        """刷新文件列表树"""
        self.file_tree.delete(*self.file_tree.get_children())
        for fi in self.file_paths:
            self.file_tree.insert("", tk.END, values=(fi["path"],), text=fi["name"])
        count = len(self.file_paths)
        if count == 0:
            self.file_count_label.config(text="未加载文件", foreground="gray")
        else:
            self.file_count_label.config(text=f"已加载 {count} 个文件", foreground="black")

    def _reconcile_text(self):
        """将所有已加载文件的内容合并到 text_area，重新检测章节"""
        if not self.file_paths:
            self.text_area.delete("1.0", tk.END)
            self.chapters = []
            self._refresh_chapters()
            self.status_label.config(text="就绪")
            return
        merged = "\n\n".join(f["content"] for f in self.file_paths)
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert("1.0", merged)
        total_chars = sum(len(f["content"]) for f in self.file_paths)
        self.status_label.config(text=f"已加载 {len(self.file_paths)} 个文件（{total_chars}字）")
        self._refresh_chapters()

    def _read_docx(self, path: str) -> str:
        """读取 .docx：优先用 python-docx，否则回退到直接解析 zip 中的 document.xml"""
        # 优先 python-docx
        try:
            import docx  # type: ignore
            d = docx.Document(path)
            return "\n".join(p.text for p in d.paragraphs)
        except ImportError:
            pass
        # 回退：直接解析 zip
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(path) as z:
            with z.open("word/document.xml") as f:
                tree = ET.parse(f)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = tree.getroot()
        paragraphs = []
        for p in root.iter(f"{ns}p"):
            texts = [t.text or "" for t in p.iter(f"{ns}t")]
            paragraphs.append("".join(texts))
        return "\n".join(paragraphs)

    def _read_markdown(self, path: str) -> str:
        """读取 .md：保留结构，去掉基本 Markdown 标记使朗读更自然"""
        import re as _re
        raw = None
        for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    raw = f.read()
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raise RuntimeError("无法解码 Markdown 文件")

        text = raw
        # 移除代码块（三引号）
        text = _re.sub(r"```.*?```", "", text, flags=_re.DOTALL)
        # 移除行内代码
        text = _re.sub(r"`([^`]+)`", r"\1", text)
        # 图片 ![alt](url) 只保留 alt
        text = _re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
        # 链接 [text](url) 只保留 text
        text = _re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        # 标题符号 # 去掉
        text = _re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=_re.MULTILINE)
        # 引用 > 去掉
        text = _re.sub(r"^\s{0,3}>\s?", "", text, flags=_re.MULTILINE)
        # 列表符号 -/*/+ 和有序列表符号去掉
        text = _re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=_re.MULTILINE)
        text = _re.sub(r"^\s{0,3}\d+\.\s+", "", text, flags=_re.MULTILINE)
        # 粗体 / 斜体 / 删除线
        text = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = _re.sub(r"\*([^*]+)\*", r"\1", text)
        text = _re.sub(r"__([^_]+)__", r"\1", text)
        text = _re.sub(r"_([^_]+)_", r"\1", text)
        text = _re.sub(r"~~([^~]+)~~", r"\1", text)
        # 水平分割线
        text = _re.sub(r"^\s{0,3}[-*_]{3,}\s*$", "", text, flags=_re.MULTILINE)
        return text

    def _read_epub(self, path: str) -> str:
        """读取 .epub 电子书"""
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError:
            raise ImportError("ebooklib 未安装 (pip install ebooklib)")
        book = epub.read_epub(path)
        chapters = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                content = item.get_content()
                text = self._extract_text_from_html(content.decode("utf-8", errors="replace"))
                if text.strip():
                    chapters.append(text)
        return "\n\n".join(chapters)

    def _read_html(self, path: str) -> str:
        """读取 HTML 文件，提取正文文本"""
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts = []
                self.skip_tags = {"script", "style", "nav", "header", "footer"}
                self._skip_depth = 0

            def handle_starttag(self, tag, attrs):
                if tag in self.skip_tags:
                    self._skip_depth += 1

            def handle_endtag(self, tag):
                if tag in self.skip_tags and self._skip_depth > 0:
                    self._skip_depth -= 1
                if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                    self.text_parts.append("\n")

            def handle_data(self, data):
                if self._skip_depth == 0:
                    text = data.strip()
                    if text:
                        self.text_parts.append(text)

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        extractor = TextExtractor()
        extractor.feed(content)
        return "".join(extractor.text_parts)

    @staticmethod
    def _extract_text_from_html(html: str) -> str:
        """从 HTML 片段提取纯文本"""
        from html.parser import HTMLParser

        class _Extractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []

            def handle_data(self, data):
                t = data.strip()
                if t:
                    self.parts.append(t)

            def handle_endtag(self, tag):
                if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"):
                    self.parts.append("\n")

        ex = _Extractor()
        ex.feed(html)
        return " ".join(ex.parts)

    def _read_pdf(self, path: str) -> str:
        """读取 PDF 文件"""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(path)
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()
            return "\n\n".join(text_parts)
        except ImportError:
            pass
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                text_parts = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
            return "\n\n".join(text_parts)
        except ImportError:
            raise ImportError("需要安装 PyMuPDF 或 pdfplumber 来读取 PDF (pip install PyMuPDF)")

    def _load_file(self, path: str):
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".docx":
                content = self._read_docx(path)
            elif ext in (".md", ".markdown"):
                content = self._read_markdown(path)
            elif ext == ".epub":
                content = self._read_epub(path)
            elif ext in (".html", ".htm"):
                content = self._read_html(path)
            elif ext == ".pdf":
                content = self._read_pdf(path)
            else:
                content = None
                for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
                    try:
                        with open(path, "r", encoding=enc) as f:
                            content = f.read()
                        break
                    except UnicodeDecodeError:
                        continue
                if content is None:
                    messagebox.showerror("错误", "无法读取文件，编码不支持")
                    return

            self._single_file_path = path
            self.file_paths.append({
                "path": path,
                "name": os.path.basename(path),
                "content": content,
                "encoding": ext,
            })
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            messagebox.showerror("错误", f"读取文件失败: {e}")

    def _refresh_chapters(self):
        text = self.text_area.get("1.0", tk.END).strip()
        # 构建 source_map
        source_map = []
        char_offset = 0
        for fi in self.file_paths:
            source_map.append((char_offset, fi["name"]))
            char_offset += len(fi["content"]) + 2  # +2 for "\n\n" separator
        self.chapters = detect_chapters(text, source_map=source_map)
        self._refresh_chapters_list(filter_text=self.chapter_search_var.get().lower() if hasattr(self, "chapter_search_var") else "")
        count = len(self.chapters)
        has_titles = count > 1 or (count == 1 and self.chapters[0]["title"] != "全文")
        if has_titles:
            self.chapter_count_label.config(text=f"共 {count} 章/段")
        else:
            self.chapter_count_label.config(text="未检测到章节")
        logger.info(f"检测到 {count} 个章节/段落")

    def _select_all_chapters(self):
        for item in self.chapter_tree.get_children():
            self.chapter_tree.selection_add(item)

    def _deselect_all_chapters(self):
        self.chapter_tree.selection_remove(self.chapter_tree.selection())

    # ===== 预览 =====

    def _get_rate_string(self) -> str:
        val = self.rate_var.get()
        return f"+{val}%" if val >= 0 else f"{val}%"

    # ----- 全文试听（无长度限制 + 可暂停 + 内置播放） -----

    def _toggle_preview_full(self):
        """单按钮控制：空闲→开始；生成中→中止生成；播放中→暂停；暂停中→继续。"""
        state = self._preview_state
        if state == "idle":
            self._start_preview_full()
        elif state == "generating":
            self._preview_should_stop = True
            try:
                self.player.stop()
            except Exception:
                pass
            self._cleanup_preview_tmp()
            self._set_preview_state("idle")
            self.status_label.config(text="已中止试听")
        elif state == "playing":
            if self.player.pause():
                self._set_preview_state("paused")
            else:
                self.player.stop()
                self._preview_should_stop = True
                self._cleanup_preview_tmp()
                self._set_preview_state("idle")
        elif state == "paused":
            if self.player.resume():
                self._set_preview_state("playing")

    # 流式试听片段长度（字）
    STREAM_PREVIEW_SEG_CHARS = 400

    def _start_preview_full(self):
        text = self.text_area.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("提示", "请先输入或导入文字内容")
            return
        engine = self.engine_var.get()
        ready, msg = check_engine_ready(engine)
        if not ready:
            messagebox.showerror("引擎不可用", msg)
            return

        self._preview_should_stop = False
        self._set_preview_state("generating")
        self.status_label.config(text="正在生成首段试听...")

        # 拆分为短段：首段 ~400 字快速起播，其余段 ~800 字
        first = text[: self.STREAM_PREVIEW_SEG_CHARS]
        rest = text[self.STREAM_PREVIEW_SEG_CHARS:]
        from tts_engine import split_text, _generate_one_safe
        rest_segs = split_text(rest, max_length=self.STREAM_PREVIEW_SEG_CHARS * 2) if rest else []
        segments = [first] + rest_segs
        total = len(segments)
        logger.info(f"流式试听：拆分为 {total} 段，首段 {len(first)} 字")

        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix="audiobook_preview_")
        self._preview_tmp_dir = tmp_dir

        def run():
            try:
                voice = get_voice_id(self.voice_var.get(), engine)
                rate = self._get_rate_string()
                playing_started = False
                for i, seg in enumerate(segments):
                    if self._preview_should_stop:
                        return
                    out = os.path.join(tmp_dir, f"seg_{i:04d}.mp3")
                    _generate_one_safe(seg, voice, rate, out, engine=engine,
                                       should_stop=lambda: self._preview_should_stop)
                    if self._preview_should_stop:
                        return
                    self.root.after(0, lambda p=out: self.player.enqueue(p))
                    if not playing_started:
                        playing_started = True
                        self.root.after(0, lambda i=i, t=total: (
                            self._set_preview_state("playing"),
                            self.status_label.config(
                                text=f"试听播放中（边生成边播放，{i + 1}/{t} 段就绪）"
                            ),
                        ))
                    else:
                        self.root.after(0, lambda i=i, t=total: self.status_label.config(
                            text=f"试听播放中（{i + 1}/{t} 段就绪）"
                        ))
                self.root.after(0, lambda: self.status_label.config(text="全部段已生成，等待播放完毕"))
            except Exception as e:
                logger.error(f"流式试听失败: {e}", exc_info=True)
                self.root.after(0, lambda: messagebox.showerror("错误", f"试听失败: {e}"))
                self.root.after(0, lambda: self._set_preview_state("idle"))
                self.root.after(0, lambda: self.status_label.config(text="试听失败"))

        threading.Thread(target=run, daemon=True).start()

    def _cleanup_preview_tmp(self):
        d = getattr(self, "_preview_tmp_dir", None)
        if d and os.path.isdir(d):
            try:
                import shutil as _sh
                _sh.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._preview_tmp_dir = None

    def _on_player_state(self, state: str):
        """来自 AudioPlayer 的状态回调（任意线程）"""
        def apply():
            if state == "ended":
                self._cleanup_preview_tmp()
                self._set_preview_state("idle")
                self.status_label.config(text="试听播放结束")
            elif state == "stopped" and self._preview_state in ("playing", "paused"):
                self._cleanup_preview_tmp()
                self._set_preview_state("idle")
        self.root.after(0, apply)

    def _set_preview_state(self, state: str):
        self._preview_state = state
        if not hasattr(self, "btn_preview_full"):
            return
        labels = {
            "idle": "试听全文（可暂停）",
            "generating": "中止生成",
            "playing": "暂停播放",
            "paused": "继续播放",
        }
        self.btn_preview_full.config(text=labels.get(state, "试听全文"))

    # ===== 生成控制 =====

    def _get_selected_indices(self) -> list:
        return [int(self.chapter_tree.index(item)) for item in self.chapter_tree.selection()]

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

        engine = self.engine_var.get()
        ready, msg = check_engine_ready(engine)
        if not ready:
            messagebox.showerror("引擎不可用", msg)
            return

        output_dir = filedialog.askdirectory(title="选择保存目录")
        if not output_dir:
            return

        # 从已加载文件生成前缀
        if len(self.file_paths) == 1:
            file_prefix = os.path.splitext(self.file_paths[0]["name"])[0]
        elif self._single_file_path:
            file_prefix = os.path.splitext(os.path.basename(self._single_file_path))[0]
        else:
            file_prefix = "有声读物"

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
        self.btn_pause.config(state="disabled")
        self.status_label.config(text="正在暂停（等待当前片段结束）...")
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

    # ===== ASR 标签页 =====

    def _build_asr_tab(self):
        """构建 ASR（语音转文字）标签页"""
        pad = 4
        paned = ttk.PanedWindow(self.asr_tab, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # 上方：ASR 设置
        asr_top = ttk.Frame(paned)
        paned.add(asr_top, weight=1)

        # 音频文件选择
        audio_frame = ttk.LabelFrame(asr_top, text="音频文件", padding=pad)
        audio_frame.pack(fill=tk.X, pady=(0, pad))
        ttk.Button(audio_frame, text="📂 选择音频文件", command=self._select_audio_file).pack(anchor=tk.W)
        self.audio_file_label = ttk.Label(audio_frame, text="未选择文件", foreground="gray")
        self.audio_file_label.pack(anchor=tk.W, pady=(pad, 0))

        # 模型选择
        ttk.Label(asr_top, text="Whisper 模型:").pack(anchor=tk.W, pady=(pad, 0))
        self.asr_model_var = tk.StringVar(value="base")
        self.asr_model_combo = ttk.Combobox(asr_top, textvariable=self.asr_model_var, state="readonly")
        self.asr_model_combo["values"] = list(WHISPER_MODELS.keys())
        self.asr_model_combo.pack(fill=tk.X, pady=(pad, 0))
        model_desc = "、".join(f"{k}={v}" for k, v in WHISPER_MODELS.items())
        ttk.Label(asr_top, text=model_desc, wraplength=400, foreground="gray",
                  font=("Helvetica", 9)).pack(fill=tk.X, pady=(0, pad))

        # 语言选择
        ttk.Label(asr_top, text="语言:").pack(anchor=tk.W)
        self.asr_lang_var = tk.StringVar(value="auto（自动检测）")
        self.asr_lang_combo = ttk.Combobox(asr_top, textvariable=self.asr_lang_var, state="readonly")
        self.asr_lang_combo["values"] = [
            "auto（自动检测）", "zh（中文）", "en（英文）", "ja（日文）",
            "ko（韩文）", "fr（法文）", "de（德文）", "es（西班牙文）",
            "ru（俄文）",
        ]
        self.asr_lang_combo.pack(fill=tk.X, pady=(pad, 0))

        # 输出格式
        ttk.Label(asr_top, text="输出格式:").pack(anchor=tk.W, pady=(pad, 0))
        self.asr_format_var = tk.StringVar(value="txt")
        fmt_row = ttk.Frame(asr_top)
        fmt_row.pack(fill=tk.X, pady=(0, pad))
        for label, val in [("纯文本 (txt)", "txt"), ("字幕 (srt)", "srt"), ("JSON", "json")]:
            ttk.Radiobutton(fmt_row, text=label, variable=self.asr_format_var, value=val).pack(side=tk.LEFT, padx=(0, pad))

        # 操作按钮
        self.btn_asr_start = ttk.Button(asr_top, text="▶ 开始识别", command=self._start_asr, style="Accent.TButton")
        self.btn_asr_start.pack(fill=tk.X)
        self.asr_status_label = ttk.Label(asr_top, text="", foreground="gray")
        self.asr_status_label.pack(fill=tk.X, pady=(pad, 0))

        # 下方：识别结果（可滚动）
        asr_bottom = ttk.Frame(paned)
        paned.add(asr_bottom, weight=2)

        result_frame = ttk.LabelFrame(asr_bottom, text="识别结果", padding=pad)
        result_frame.pack(fill=tk.BOTH, expand=True)

        self.asr_result_text = scrolledtext.ScrolledText(
            result_frame, wrap=tk.WORD, font=("Helvetica", 12),
            state="disabled",
        )
        self.asr_result_text.pack(fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(result_frame)
        btn_frame.pack(fill=tk.X, pady=(pad, 0))
        ttk.Button(btn_frame, text="📋 复制结果", command=self._copy_asr_result).pack(side=tk.LEFT, padx=(0, pad))
        ttk.Button(btn_frame, text="💾 保存到文件", command=self._save_asr_result).pack(side=tk.LEFT)

    # ===== ASR 操作回调 =====

    def _select_audio_file(self):
        """选择用于 ASR 识别的音频文件"""
        path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[
                ("音频文件", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self._audio_file_path = path
        name = os.path.basename(path)
        size = os.path.getsize(path)
        size_str = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
        self.audio_file_label.config(text=f"{name} ({size_str})", foreground="black")
        self.asr_status_label.config(text="")

    def _start_asr(self):
        """开始 ASR 语音识别"""
        if not hasattr(self, "_audio_file_path") or not self._audio_file_path:
            messagebox.showwarning("提示", "请先选择音频文件")
            return

        if self.is_converting:
            messagebox.showinfo("提示", "正在处理中，请等待完成")
            return

        self.is_converting = True
        self.btn_asr_start.config(state="disabled", text="识别中...")
        self.asr_status_label.config(text="正在准备...")
        self.asr_result_text.configure(state="normal")
        self.asr_result_text.delete("1.0", tk.END)
        self.asr_result_text.configure(state="disabled")
        self._asr_last_result = ""

        audio_path = self._audio_file_path
        model_size = self.asr_model_var.get()
        lang_raw = self.asr_lang_var.get()
        # 从 "zh（中文）" 提取 "zh"
        language = lang_raw.split("（")[0] if "（" in lang_raw else lang_raw
        if language == "auto":
            language = "auto"
        output_format = self.asr_format_var.get()

        def run():
            try:
                storage_dir = get_storage_dir()
                ready, msg = check_asr_ready(storage_dir)
                if not ready:
                    self.root.after(0, lambda: self._asr_reset_ui())
                    self.root.after(0, lambda: messagebox.showerror("ASR 不可用", msg))
                    return

                def progress_cb(current, total):
                    self.root.after(0, lambda: self.asr_status_label.config(
                        text=f"识别进度: {current}/{total}"))

                def should_stop_cb():
                    return self.should_stop

                result = transcribe(
                    input_path=audio_path,
                    storage_dir=storage_dir,
                    model_size=model_size,
                    language=language,
                    output_format=output_format,
                    progress_callback=progress_cb,
                    should_stop=should_stop_cb,
                )
                self.root.after(0, lambda: self._on_asr_done(result))
            except Exception as e:
                logger.error(f"ASR 识别失败: {e}", exc_info=True)
                self.root.after(0, lambda: self.asr_status_label.config(text="识别失败", foreground="red"))
                self.root.after(0, lambda: messagebox.showerror("错误", f"ASR 识别失败: {e}"))
            finally:
                self.is_converting = False
                self.root.after(0, lambda: self.btn_asr_start.config(state="normal", text="开始识别"))

        threading.Thread(target=run, daemon=True).start()

    def _asr_reset_ui(self):
        """ASR 失败后重置 UI"""
        self.btn_asr_start.config(state="normal", text="开始识别")
        self.asr_status_label.config(text="")

    def _on_asr_done(self, result: str):
        """ASR 完成回调"""
        self.asr_result_text.configure(state="normal")
        self.asr_result_text.delete("1.0", tk.END)
        self.asr_result_text.insert("1.0", result)
        self.asr_result_text.configure(state="disabled")
        self.asr_status_label.config(text="识别完成", foreground="green")
        self._asr_last_result = result

    def _copy_asr_result(self):
        """复制 ASR 结果到剪贴板"""
        text = self.asr_result_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.asr_status_label.config(text="已复制到剪贴板", foreground="green")

    def _save_asr_result(self):
        """保存 ASR 结果到文件"""
        text = self.asr_result_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("提示", "没有可保存的内容")
            return
        ext = self.asr_format_var.get()
        default_name = "transcript." + ext
        path = filedialog.asksaveasfilename(
            title="保存识别结果",
            initialfile=default_name,
            defaultextension=f".{ext}",
            filetypes=[
                (f"{ext.upper()} 文件", f"*.{ext}"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.asr_status_label.config(text=f"已保存到 {os.path.basename(path)}", foreground="green")
            logger.info(f"ASR 结果已保存到 {path}")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")
