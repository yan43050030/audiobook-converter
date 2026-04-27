# 文字转有声读物 (Text-to-Audiobook Converter)

**v3.0.0** — 将文字转换为自然语音的有声读物制作工具，支持多引擎 TTS 生成与 ASR 语音转文字。

## 功能特点

### 文字转语音 (TTS)
- **多引擎支持**：Edge（联网高质量）、系统语音（离线）、Piper（离线神经网络）、CosyVoice（实验性离线）、外挂引擎插件
- **多文件导入**：支持 txt / markdown / docx / epub / html / pdf，可同时导入多个文件，逐个移除
- **智能章节检测**：自动识别中文章节标题（第X章、序章、楔子等），支持章节选择、过滤搜索
- **灵活拆分**：按章节拆分、按时长拆分、合并为单个文件
- **断点续传**：批量生成中断后可从中断处继续
- **自动合并**：生成后可合并多个 MP3 文件

### 语音转文字 (ASR)
- 基于 faster-whisper 的高精度语音识别
- 支持多种 Whisper 模型（tiny ~ large-v3），首次自动下载
- GPU 加速（CUDA）自动检测
- 输出格式：纯文本 (txt)、字幕 (srt)、JSON
- 支持多种音频格式（mp3 / wav / m4a / flac / ogg / aac / wma）

### UI/UX
- 深色/浅色主题切换
- 键盘快捷键
- 章节搜索过滤
- 窗口位置记忆
- 依赖检测面板
- 实时进度显示

## 界面预览

```
┌─────────────────────────────────────────────────┐
│  文字转有声读物 v3.0.0                          │
├─────────────────────────────────────────────────┤
│  ┌──────────┬──────┐  ┌─── 文字转语音 ─┬─────┤ │
│  │ 章节列表  │ 文本  │  │ [文字转语音] │[ASR]│ │
│  │ [全选]    │ 内容  │  │   引擎选择    │     │ │
│  │ □ 第一章  │ 区域  │  │   语音选择    │     │ │
│  │ □ 第二章  │       │  │   语速调整    │     │ │
│  │ 搜索:____ │       │  │   输出设置    │     │ │
│  └──────────┴──────┘  │   [生成MP3]    │     │ │
│                        └─────────────────┘     │
│  进度: ████████░░░░░░░░ 72%                     │
│  状态: 正在处理: 5/12 (42%)                     │
└─────────────────────────────────────────────────┘
```

## 快速开始

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/yan43050030/audiobook-converter.git
cd audiobook-converter

# 2. 安装核心依赖
pip install -r requirements.txt

# 3. (可选) 安装增强功能
pip install faster-whisper    # ASR 语音转文字
pip install ebooklib          # EPUB 电子书读取
pip install PyMuPDF           # PDF 文档读取
pip install python-docx       # Word DOCX 优化读取
```

### 运行

```bash
python main.py
```

### 打包为独立应用

```bash
# macOS
pip install pyinstaller
pyinstaller audiobook_converter_mac.spec
# → dist/AudiobookConverter.app

# Windows（在 Windows 上执行）
pyinstaller audiobook_converter_win.spec
# → dist/audiobook_converter.exe

# 跨平台通用包
pyinstaller audiobook_converter.spec
# → dist/audiobook_converter/
```

## 引擎对比

| 引擎 | 类型 | 质量 | 速度 | 网络 | 平台 |
|------|------|------|------|------|------|
| Edge | 在线云端 | ★★★★★ | 快 | 需要 | 全平台 |
| 本地系统 | 离线系统 | ★★★ | 快 | 不需要 | macOS/Win/Linux |
| Piper | 离线神经网络 | ★★★★ | 中 | 不需要 | 全平台（需模型） |
| CosyVoice | 离线神经网络 | ★★★★★ | 慢 | 不需要 | 全平台（实验性） |
| 外挂引擎 | 外部程序 | 可变 | 可变 | 可变 | 全平台 |

### Piper 语音包

Piper 引擎需要下载语音包（.onnx + .json），放入便携存储目录的 `piper-models/` 文件夹。

推荐语音包下载地址：[Piper Voice Models](https://huggingface.co/rhasspy/piper-voices/tree/main/)

> 提示：程序首次启动时会自动在便携存储目录的子目录中递归搜索语音包。

## 外挂引擎插件

将外部 TTS 程序放在 `{便携存储目录}/engines/{引擎名称}/` 目录下，程序自动发现并注册。

外挂引擎需实现标准 CLI 协议：

```bash
# 列出可用语音
myengine --list-voices
# → [{"id": "voice1", "name": "语音1"}, ...]

# 生成语音
myengine --voice voice1 --text "要合成的文本" --output /path/to/output.mp3 --speed 1.0
```

## 便携模式

把 ffmpeg、piper 等可执行文件放入 `{便携存储目录}/bin/`，配合语音包可做到完全离线便携，适合 U 盘携带到任意机器使用。

## 项目结构

```
audiobook_converter/
├── main.py              # 程序入口
├── gui.py               # Tkinter GUI
├── tts_engine.py         # TTS 引擎（Edge/本地/Piper/CosyVoice/外挂）
├── asr_engine.py         # ASR 引擎（faster-whisper）
├── requirements.txt      # 依赖清单
├── audiobook_converter.spec       # PyInstaller 通用打包配置
├── audiobook_converter_mac.spec   # macOS .app 打包配置
├── audiobook_converter_win.spec   # Windows .exe 打包配置
└── README.md
```

## 版本历史

- **v3.0.0** (2026-04) — UI 美化、sv-ttk 默认主题、章节列表 Treeview、LabelFrame 分组布局、内边距规范化、按钮图标统一
- **v2.6.0** (2026-04) — 新增 ASR 语音转文字、CosyVoice 外挂引擎、深色主题、多种格式支持、Piper CLI 并行加速、多文件导入
- **v2.5.0** (2026-02) — Piper TTS 新 API 兼容、docx/md 读取、滚动面板、macOS 深色适配
- **v2.4.0** — Piper TTS 本地引擎、便携存储目录、断点续传
- **v2.3.0** — 系统语音引擎（macOS/Windows/Linux）
- **v2.2.0** — edge-tts 在线引擎、批量生成、MP3 合并
- **v2.1.0** — 基础 GUI、文字转语音核心功能
- **v2.0.0** — 初始发布

## 许可

MIT License
