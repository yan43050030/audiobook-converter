# 功能审查与下一步提升计划

> 基于 v5.0.3 代码审查（2026-07），重点覆盖本地离线引擎。
>
> **实施状态（2026-07-11）**：
> - v5.1.0：P0 全部 5 项（S1-S5）与 P1 全部 4 项已完成（见 CHANGELOG.md v5.1.0 小节）
> - v5.2.0：P2 全部 6 项（F1-F6）+ macOS 包瘦身已完成（见 CHANGELOG.md v5.2.0 小节）
> - 剩余：P3（模型管理器、首次运行向导、生成队列可视化、i18n）为 v5.3 范围；
>   代码签名公证（U5）已取消——无 Apple Developer 账号/签名证书，README 提供未签名应用打开说明代替
>
> **v6.0 立项（2026-09）**：架构跨越 + 产品形态扩展，详见文末「v6.0 大版本升级计划」。
> 当前进度：阶段 1 进行中 —— A1/A2（删遗留与 Tkinter）、A3（包骨架）、A6（集成测试
> 基线）已并入 main；A4（单体拆分迁移）进行中，已迁 `file_reader → io/readers`、
> `asr_engine → engines/asr`、`tts_engine` 纯文本层 → `core/text`。

## 一、现有功能盘点

### TTS 引擎（5 种）

| 引擎 | 现状 | 审查结论 |
|------|------|----------|
| Edge（在线） | edge-tts，异步并发批量（并发数 3），重试 + 指数退避 | 成熟，但是**硬依赖**（见问题 1） |
| 系统语音（离线） | macOS `say` / Windows PowerShell+SAPI / Linux espeak-ng，启动时枚举中文语音 | 可用；枚举在模块导入时同步执行，拖慢启动（见问题 7） |
| Piper（离线神经） | Python API 优先、CLI 回退；模型自动下载（断点续传 + hf-mirror 镜像）；LRU 缓存 2 个模型；CLI 模式 3 线程并行 | 实现最完整的离线引擎；但仅 2 个中文语音，Python 模式无并行 |
| CosyVoice（离线神经，实验性） | Python API + 外部引擎回退；一键下载模型（≈600MB） | **每次合成重新加载模型**、voice 参数未传入推理（见问题 3、4） |
| 外挂引擎 | CLI 协议（`--list-voices` / `--voice --text --output --speed`），自动扫描注册 | 协议简单清晰，可用 |

### ASR 引擎

- faster-whisper，模型 tiny~large-v3 首次自动下载，CUDA 自动检测（float16/int8）
- 输出 txt / srt / json；进度按转录时间轴回调；模型缓存 + atexit 卸载
- 外挂 ASR 引擎协议（`asr-engines/` 目录）
- **缺口**：单文件模式（无批量）；Whisper 模型下载走 HuggingFace 原始源，无镜像回退、无 UI 进度（与 Piper/CosyVoice 的下载体验不一致）

### 文本处理管线

多格式读取（txt/md/docx/epub/html/pdf）、中文章节检测（大文本正则 / 小文本逐行双路径）、多人对话检测（旁白/对话二分 + 说话人提取）、按章节/时长/单文件拆分、断点续传（`.audiobook_progress.json`）、MP3 合并（跳过 ID3）、响度归一化（loudnorm）。

### GUI 与基础设施

- PySide6/Qt6 主界面（Tkinter 回退）：双标签页、面板化设置、QuickBar、深浅主题 QSS、QThread worker、下载进度订阅
- 便携模式（`bin/` + 模型目录），PyInstaller 三平台打包，CI tag 触发 macOS/Windows 构建

## 二、审查发现的问题

按严重程度排列，问题 1–5 与离线场景直接相关：

1. **`import edge_tts` 是硬依赖**（`tts_engine.py:20`）。离线优先的软件，不装在线引擎的包就整个启动不了；本次测试收集失败（72 项只跑了 20 项）正是这个原因。其他引擎（piper/cosyvoice/faster-whisper/pydub）都是可选导入，唯独 edge-tts 不是。
2. **CI 不跑测试**。`build.yml` 只安装依赖 + 打包，测试步骤一直是"预留"状态，回归全靠本地手跑。
3. **CosyVoice 无模型实例缓存**（`tts_engine.py:1701`）。每合成一章就重新 `_CosyVoiceCls(model_dir)` 加载一次 600MB 模型，批量生成时性能不可接受；对比 Piper 有 LRU 缓存。
4. **CosyVoice 的 voice 参数没有传入推理**。`_cosyvoice_generate` 里 voice 只用来选模型目录，`inference_sft(text, stream=False)` 未带说话人 ID——用户在界面选的"语音"实际不生效。
5. **Whisper 模型下载无镜像、无进度**。首次用 ASR 在国内网络环境容易长时间无响应，用户不知道发生了什么。
6. **Piper 并行分支静默吞异常**（`tts_engine.py:2471` `except Exception: pass`）。并行路径任何错误都被隐藏后悄悄回退串行，排查困难。
7. **启动时同步枚举系统语音**（模块导入即执行，Windows PowerShell 路径超时上限 15s），拖慢冷启动。
8. **小的健壮性问题**：
   - `tar.extractall` 解压 CosyVoice 模型无路径过滤（Python 3.12+ 应加 `filter="data"`）；
   - `output_path.replace('.mp3', '.wav')` 应改用 `os.path.splitext`（路径中间含 ".mp3" 会出错）；
   - ASR 临时 WAV 固定命名 `asr_input_{pid}.wav`，同进程并发会互相覆盖（当前单 worker 未触发，属隐患）。

## 三、提升计划

### P0 — 稳定性与离线体验基石（建议下个版本 v5.1）

| 项 | 内容 | 工作量 |
|----|------|--------|
| S1 | edge-tts 改为可选导入，未安装时引擎显示"需安装"（与其他引擎一致）；顺带修复测试收集失败 | 0.5 天 |
| S2 | CI 增加测试步骤（Linux runner 上 `python -m unittest discover`，最小依赖矩阵：装/不装可选包各跑一遍） | 0.5 天 |
| S3 | CosyVoice 模型实例缓存（复用 Piper 的 LRU 模式）+ voice 参数正确传入 `inference_sft` | 1 天 |
| S4 | Whisper 模型下载接入现有下载基础设施：hf-mirror 镜像回退 + `add_download_listener` 进度推送（可用 `huggingface_hub` 的 `HF_ENDPOINT` 或预下载到 `whisper-models/`） | 1 天 |
| S5 | 消除 `except Exception: pass`；tarfile `filter="data"`；`os.path.splitext`；ASR 临时文件用 `tempfile.mkstemp` | 0.5 天 |

### P1 — 性能

| 项 | 内容 | 工作量 |
|----|------|--------|
| P1-1 | 启动加速：系统语音枚举改为懒加载/后台线程，首屏不阻塞 | 0.5 天 |
| P1-2 | Piper Python 模式并行：每线程独立 PiperVoice 实例（或进程池），对齐 CLI 模式的 3 并行 | 1 天 |
| P1-3 | 并发数自适应：Piper/Edge 的固定 3 并发改为按 CPU 核数/网络状况可配置 | 0.5 天 |
| P1-4 | ASR 提速：接入 faster-whisper `BatchedInferencePipeline`（长音频约 3-4x）；GPU 下提供 `int8_float16` 选项省显存 | 1 天 |

### P2 — 功能

| 项 | 内容 | 工作量 |
|----|------|--------|
| F1 | **离线语音包扩容**：从 HuggingFace `voices.json` 动态拉取 Piper 全部中文/多语言语音列表，界面内选择下载（替代硬编码 2 个） | 1-2 天 |
| F2 | 角色→音色多对多映射（对话检测增强，v4.0 保留项）：说话人列表提取 + 每角色指定语音 | 1-2 天 |
| F3 | Edge 引擎支持对话检测路径（保留项） | 1 天 |
| F4 | ASR 批量转录：多文件队列，逐个输出 | 1 天 |
| F5 | 输出增强：m4b 有声书格式（内嵌章节标记）、ID3 封面/元数据写入 | 1-2 天 |
| F6 | TTS↔ASR 联动：生成 MP3 同时产出 srt 字幕（离线引擎可直接用合成时间轴，无需二次识别） | 1-2 天 |

### P3 — 界面与易用性

| 项 | 内容 | 工作量 |
|----|------|--------|
| U1 | **模型管理器面板**：统一展示 Piper/CosyVoice/Whisper 模型的已装/占用空间/删除/下载，替代分散的按钮 | 1-2 天 |
| U2 | 首次运行向导：选场景（完全离线 / 在线高质量）→ 自动下载所需模型 → 试听验证 | 1-2 天 |
| U3 | 生成队列可视化：每章状态（等待/生成中/完成/失败），失败项单独重试按钮 | 1 天 |
| U4 | i18n 多语言界面（保留项） | 2-3 天 |

~~U5 代码签名公证~~：已取消（2026-07）——需要 Apple Developer 账号与 Windows 签名证书，当前不具备。
替代方案：README 提供未签名应用的首次打开说明（macOS 右键打开 / Windows SmartScreen 仍要运行）。

### 优先级依据

- P0 全部是"已确认的缺陷或体验断点"，其中 S1/S2 直接决定后续所有改动的回归安全网，应最先做。
- P1 聚焦离线引擎的实际吞吐：CosyVoice 缓存（S3）+ Piper Python 并行（P1-2）合计能把典型整本书离线生成时间缩短一半以上。
- P2/P3 按"离线能力扩容 → 对话/输出增强 → 界面打磨"排序，可按版本节奏拆分为 v5.2 / v5.3。

---

# v6.0 大版本升级计划（2026-09 起）

> 立项背景：v5.x 已把离线引擎能力和输出格式补齐，但代码仍是"脚本集合"形态——
> `tts_engine.py`(3155 行)、`gui_pyside6.py`(2268 行) 两个单体文件职责糅杂，且仓库里
> 还留有约 4700 行遗留界面（`gui.py`、`gui_tkinter_backup.py`）。v6.0 的主题是
> **架构跨越 + 产品形态扩展**，而非继续堆功能（P3 渐进功能仍归 v5.3 线）。
>
> 原则：**每阶段可独立发布**，行为回归有测试兜底，大改动走 PR 审阅。
> 版本节奏：阶段 1 → `6.0.0-alpha`，阶段 2 → `6.0.0-beta`，阶段 3 → `6.0.0`。

## 主题与三阶段

### 阶段 1 — 地基重构（低风险先行，目标 6.0.0-alpha）

| 项 | 内容 | 破坏性 | 工作量 |
|----|------|--------|--------|
| A1 | 删除遗留死代码：`gui.py`、`main_pyside6.py` | 无（无引用） | 0.2 天 |
| A2 | **移除 Tkinter 后端**：删 `gui_tkinter_backup.py`，`main.py` 改为要求 Qt（PySide6/PyQt6），去掉 `sv-ttk` 依赖与 spec 引用 | 是（不再有 Tkinter 回退） | 0.3 天 |
| A3 | 建立 `audiobook/` 包骨架：`core/`(章节·对话·拆分·文本)、`engines/`、`io/`(readers·mp3·m4b·srt·id3)、`ui/`；`__init__` 提供稳定导入面（兼容再导出） | 无（再导出保持兼容） | 0.5 天 |
| A4 | 逐步迁移：把 `tts_engine.py` 按职责拆入 `audiobook/core` 与 `audiobook/io`；顶层 `tts_engine`/`asr_engine` 保留为薄再导出（兼容旧 import 与 PyInstaller spec） | 内部结构变，外部 import 兼容 | 2-3 天 |
| A5 | **引擎插件化**：定义 `Engine` 抽象基类（`list_voices/ready/synthesize`），内置引擎与外挂引擎走同一注册表接口 | 引擎接口重定义 | 1-2 天 |
| A6 | 测试网加固：补引擎层与 IO 层集成测试（拆包前先立回归基线）；GUI 冒烟测试（xvfb） | 无 | 1-2 天 |

### 阶段 2 — 产品形态：从桌面程序到可编程工具（目标 6.0.0-beta）

| 项 | 内容 | 工作量 |
|----|------|--------|
| B1 | 稳定的库 API：`audiobook.convert(...)`、`audiobook.transcribe(...)` 脱离 GUI 可独立调用 | 1 天 |
| B2 | `audiobook` CLI：`audiobook convert book.epub --engine piper --split chapter --m4b --srt`，天然支持批量与自动化 | 2-3 天 |
| B3 | 配置 schema 化：集中式配置（引擎/路径/并发）带校验与迁移；`config.json` 升级路径 | 1 天 |
| B4 | （可选）本地 Web UI：FastAPI + 浏览器界面，可跑在 NAS/服务器批量转换，绕开 PyInstaller 打包痛点 | 3-5 天 |

### 阶段 3 — 旗舰用户功能（门面，目标 6.0.0 正式版）

二选一或组合，作为 6.0 的对外卖点：

| 方向 | 内容 | 工作量 |
|------|------|--------|
| C1 | **神经语音提质 / 声音克隆**：CosyVoice2 / instruct 一等公民，少量样本克隆音色，流式试听 | 3-5 天 |
| C2 | **工作区重设计**：模型管理器（统一装/删/占用）+ 首次运行向导 + 生成队列可视化（每章状态/失败重试），把 P3 项整合为一次界面跃迁 | 3-5 天 |

## 破坏性变更清单（够格 6.0）

- 删除 Tkinter 后端（仅保留 Qt）；最低 Python 提升到 3.11+
- 包结构重排（`audiobook/` 包），引擎接口重定义（`Engine` 抽象基类）
- 配置文件 schema 调整（带自动迁移）
- 移除依赖：`sv-ttk`

## 风险与兜底

- 拆包易引回归 → **阶段 1 先补集成测试基线（A6）再动结构（A4/A5）**
- 单体拆分体量大 → 顶层模块保留薄再导出，PyInstaller spec 与旧 import 不断
- 打包链路 → 本轮不改构建；spec 仅同步清理已删依赖，实际构建在各平台单独验证
