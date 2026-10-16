# 文字转有声读物 (Text-to-Audiobook Converter)

**v6.0.0** — 将文字转换为自然语音的有声读物制作工具，PySide6/Qt6 界面，支持多引擎 TTS、ASR 语音转文字、多人对话识别。保留 v5.2 全部能力（Piper 语音包在线目录、角色→音色映射、m4b 有声书导出含章节、srt 字幕、ASR 批量转录）。v6.0 为大版本重构 + 产品形态扩展：分层 `audiobook` 包 + 统一 `Engine` 抽象/注册表（全引擎具体化）、`audiobook` 命令行与库 API、集中式配置 schema、本地 Web UI（`audiobook serve`）；仅支持 Qt6。详见 CHANGELOG.md / ROADMAP.md。

> ⚠️ v6.0 起仅支持 Qt6 界面，已移除 Tkinter 回退后端（需安装 PySide6 或 PyQt6）。

## 功能特点

### 文字转语音 (TTS)
- **多引擎支持**：Edge（联网高质量）、系统语音（离线）、Piper（离线神经网络）、CosyVoice（实验性离线）、外挂引擎插件
- **完全离线可用**：所有引擎依赖（含 edge-tts）均为可选安装，离线环境只装需要的引擎即可运行
- **多文件导入**：支持 txt / markdown / docx / epub / html / pdf，可同时导入多个文件，逐个移除
- **智能章节检测**：自动识别中文章节标题（第X章、序章、楔子等），支持章节选择、过滤搜索
- **多人对话识别**：旁白/对话自动区分，支持角色→音色映射（从文本一键检测角色）
- **灵活拆分**：按章节拆分、按时长拆分、合并为单个文件
- **断点续传**：批量生成中断后可从中断处继续
- **丰富输出**：MP3 合并、m4b 有声书导出（内嵌章节标记）、ID3 元数据写入、srt 字幕同步生成
- **Piper 语音市场**：内置官方语音目录，界面内直接下载数百个多语言语音包（含镜像加速）

### 语音转文字 (ASR)
- 基于 faster-whisper 的高精度语音识别
- 支持多种 Whisper 模型（tiny ~ large-v3），首次自动下载（带进度显示 + hf-mirror 镜像回退）
- 批量推理管线（BatchedInferencePipeline）自动启用，长音频提速约 3-4 倍
- GPU 加速（CUDA）自动检测，可选精度（float16 / int8_float16 / int8）
- 多文件批量转录：结果自动保存到各音频文件旁
- 输出格式：纯文本 (txt)、字幕 (srt)、JSON
- 支持多种音频格式（mp3 / wav / m4a / flac / ogg / aac / wma）

### UI/UX
- 深色/浅色主题切换
- 键盘快捷键
- 章节搜索过滤
- 窗口位置记忆
- 依赖检测面板
- 模型管理器：统一查看 Piper / CosyVoice / Whisper 模型的占用空间，一处下载与删除
- 声音克隆（实验性）：用 5–15 秒参考音频复刻音色朗读整本书（基于 CosyVoice，需单独安装）
- 首次运行向导：选使用场景（联网 / 完全离线 / 离线 + 语音转文字 / 系统语音）→ 自动备好所需模型 → 试听确认；记住上次使用的引擎
- 实时进度显示；「生成队列」页逐章显示状态与失败原因，失败章节可单独重试

## 界面预览

```
┌─────────────────────────────────────────────────┐
│  文字转有声读物 v5.0.0                          │
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

### 下载预构建版本（推荐）

从 [Releases](https://github.com/yan43050030/audiobook-converter/releases) 下载对应平台的压缩包，解压即用。

> **首次打开提示**：应用未做代码签名，系统会弹出安全提示，属正常现象：
> - **macOS**：首次启动请**右键点击 AudiobookConverter.app → 打开**（直接双击会被 Gatekeeper 拦截）；
>   若仍被阻止，到 系统设置 → 隐私与安全性 中点击「仍要打开」。
> - **Windows**：SmartScreen 弹窗时点击「更多信息 → 仍要运行」。
>
> 以上操作每台机器只需一次。

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

### 运行（图形界面）

```bash
python main.py
```

### 命令行 / 脚本调用（v6.0 新增）

无需图形界面，可用命令行批量转换、脚本化：

```bash
# 直接用（在仓库目录）
python -m audiobook engines                       # 列出引擎及就绪状态
python -m audiobook voices --engine local         # 列出某引擎的语音
python -m audiobook convert book.epub \           # 转成有声书
    --engine local --split chapter --out out --m4b --srt --metadata
python -m audiobook transcribe audio.mp3 --model base --format srt   # 语音转文字

# 或安装后用 `audiobook` 命令
pip install -e .
audiobook convert *.txt --engine edge --voice 晓晓（女声，自然） --out out --m4b
```

也可作为库调用：`from audiobook import convert_batch, transcribe, load_file_content`。

查看生效配置：`python -m audiobook config`。

模型管理（无界面服务器上也能预先备好模型）：

```bash
python -m audiobook models list                    # 已安装模型及占用空间
python -m audiobook models available               # 常用可下载模型（✓ 为已安装）
python -m audiobook models get whisper base        # 下载（Piper 也可填官方目录中的语音 key）
python -m audiobook models rm cosyvoice CosyVoice-300M-SFT   # 删除（仅限存储目录内的模型）
```

### 本地 Web UI（v6.0 新增，可选）

无需安装 Qt，用浏览器操作，适合跑在 NAS/服务器上批量转换：

```bash
# 安装 Web 可选依赖
pip install "audiobook-converter[web]"      # 或：pip install fastapi uvicorn python-multipart

# 启动本地服务（默认 127.0.0.1:8000；对外访问用 --host 0.0.0.0）
python -m audiobook serve --host 0.0.0.0 --port 8000
```

浏览器打开后：粘贴文本或上传文件（txt/md/docx/epub/html/pdf）→ 选引擎/语音/拆分方式 →
开始转换，进度实时刷新，完成后直接下载 mp3 / m4b / srt。转换在后台任务执行，产物保存在
便携存储目录的 `web_output/` 下。

> 该界面定位为**本地工具**，不含鉴权；如需对公网开放请自行置于反向代理与访问控制之后。

### 声音克隆（实验性）

用一段 5–15 秒的清晰人声，复刻出一个音色来朗读整本书。底层是阿里开源的
[CosyVoice](https://github.com/FunAudioLLM/CosyVoice)（推荐 Fun-CosyVoice3-0.5B，也支持 CosyVoice2-0.5B）的零样本复刻。

> ⚠️ 请只克隆**你本人**或**已获明确授权**的声音。
>
> 性能：强烈建议 NVIDIA 显卡；纯 CPU 也能跑，但一本书可能要数小时以上。

CosyVoice 依赖 torch，需要单独装在它自己的 Python 环境里；本程序以子进程方式调用它
（打包版 / 源码版通用，模型只加载一次）：

```bash
# 1. 安装 CosyVoice（按官方说明）
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice
conda create -n cosyvoice python=3.10 -y && conda activate cosyvoice
pip install -r requirements.txt

# 2. 下载模型（整库下载，HuggingFace → hf-mirror → ModelScope 自动回退，可断点续传）
python -m audiobook models get cosyvoice Fun-CosyVoice3-0.5B     # 或 CosyVoice2-0.5B

# 3. 告诉本程序用哪个 Python、仓库在哪（打包版必须设置）
python -m audiobook config --set cosyvoice_python=<conda 环境里的 python 路径> \
                           --set cosyvoice_repo=<CosyVoice 仓库路径>
python -m audiobook clone check --load          # 自检：解释器 / 仓库 / 模型都 ✓ 即可

# 4. 创建音色并使用（--text 填参考音频里说的话，能明显提升效果）
python -m audiobook clone add 小明 sample.wav --text "参考音频里说的那句话"
python -m audiobook clone say <音色ID> "你好，这是我的克隆声音。" -o hello.mp3
python -m audiobook convert book.txt --engine clone --voice 小明 --out out --m4b
```

同一套环境也驱动「CosyVoice」引擎（预置中文男女声、粤语、英文、日语、韩语，不需要参考音频）：
再下载 `CosyVoice-300M-SFT` 即可（`python -m audiobook models get cosyvoice CosyVoice-300M-SFT`）。

图形界面：点语音参数面板的「🎙 克隆音色…」——「环境设置」页可直接下载模型（带进度，可暂停 / 续传），填 Python / 仓库路径并一键检测（会实际加载模型）；「音色」页新建音色（选参考音频，可用 Whisper 自动识别文字稿）、编辑文字稿、重命名、删除、试听。之后在引擎里选「声音克隆」，语音下拉框就是你的音色，生成方式与其他引擎完全相同。

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
| CosyVoice | 离线神经网络（预置说话人，需单独安装，见「声音克隆」） | ★★★★★ | 慢 | 不需要 | 全平台（实验性） |
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

- **v6.0.0** (2026-10) — v6.0 正式版：大版本重构 + 产品形态扩展全部落地。**架构**：移除 Tkinter 后端（仅 Qt6）、删除遗留界面；分层 `audiobook/` 包（`core`/`io`/`engines`）；统一 `Engine` 抽象接口 + 注册表，local/edge/piper/cosyvoice **全部具体化为独立引擎子类**，通用适配器仅服务外挂插件；`_generate_one_safe` 分发改走注册表。**新形态**：`audiobook` 命令行与库 API（`convert`/`engines`/`voices`/`transcribe`/`config`/`serve`）、集中式配置 schema（`audiobook.config`）、本地 Web UI（FastAPI，`audiobook serve`）。**界面**：章节列表可拖动调整高度修复。全程行为不变、集成测试兜底（172 项），功能与 v5.2.x 一致。详见 ROADMAP.md / CHANGELOG.md
- **v6.0.0-beta.1** (2026-09) — v6.0 阶段 1 架构重构完成，作为可用版本发布（移除 Tkinter、分层包、Engine 注册表）。
- **v5.2.1** (2026-09) — 修复 Linux 本地语音（espeak-ng）检测选中不可用的 MBROLA 语音导致合成失败；界面优化：ASR 页隐藏 TTS 专用底部操作条、语音参数按钮换行避免窄窗裁切、章节列表/文本内容改为可调分隔条、文本区改用系统默认字体、直接输入文本时输出文件名不再出现 untitled
- **v5.2.0** (2026-07) — Piper 语音包在线目录（官方 voices.json，界面内下载多语言语音）、角色→音色多对多映射、Edge 引擎对话检测、ASR 多文件批量转录、m4b 有声书导出（内嵌章节）、ID3 元数据写入、srt 字幕同步生成、macOS 包体积从 1.4GB 大幅瘦身（PySide6 按需打包）、说话人归属修复
- **v5.1.0** (2026-07) — 离线引擎稳定性与性能：edge-tts 改为可选依赖（完全离线安装）、CosyVoice 模型实例缓存 + 语音选择生效、Piper Python 模式并行（并发数按 CPU 自适应，config.json 可配）、Whisper 模型镜像下载 + UI 进度 + 批量推理加速、系统语音懒加载提速启动、CI 双矩阵测试（83 项）
- **v5.0.0** (2026-05) — PySide6/Qt6 全新界面（侧栏导航 + 面板切换 + 底部快速栏）、暖色浅色主题 / 深色主题 QSS、引擎选择卡片化、所有引擎始终可见、文件读取模块独立、PySide6/PyQt6/Tkinter 三后端自动适配
- **v4.0.0** (2026-04) — 生成中引擎锁定 + 语音切换提示、CosyVoice 一键安装向导（含模型下载）、多人对话识别（旁白/对话不同音色）、pytest 回归测试（72 项）
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
