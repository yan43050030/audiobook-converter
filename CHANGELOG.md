# 项目情况日志 — 文字转有声读物 (Audiobook Converter)

## 当前版本：v6.1.0 (2026-10-18)

> **v6.1.0 发布说明**：v6.x 首个正式构建版（v6.0.0 已在源码中定版，但从未构建发布，6.0.0 的全部内容一并包含在本版中）。
> 相对 v6.0.0 新增：**阶段 3 全部完成**——C2 工作区重设计（模型管理器 / 生成队列可视化 / 首次运行向导）与 C1 声音克隆（CosyVoice 零样本复刻 + 应用内下载模型 + 「CosyVoice」引擎改走桥接进程）；**U4 多语言界面**（简体中文 / English，桌面版 + 命令行 + Web UI）；Piper / Edge 实现迁入各自引擎模块；以及多项缺陷修复（Piper 嵌套重试、未知引擎被当作 Edge、早期 Piper 语音包无法加载、`convert --help` 崩溃、CI 中 GUI 测试从未执行等）。

### U4 多语言界面（简体中文 / English：桌面版 + 命令行 + Web UI）

- **命令行**：新增全局参数 `--lang auto|zh|en`（在构建解析器前生效，`--help` 说明也随之切换），
  输出、参数说明、配置项说明、引擎显示名全部可翻译。
- **Web UI**：页面静态文字用 `{{中文原文}}` 标记、脚本动态文字用 `t("中文原文")`，服务端返回页面时
  按界面语言替换并注入译文表（单一来源，无需另维护清单）；`<html lang>` 随语言切换；引擎名与接口错误
  信息翻译；`/api/voices` **新增** `voice_labels` 字段给出语音译名，原 `voices` 字段不变（接口兼容）。
  任务状态从原始英文值（pending/running/done/error）改为本地化显示。
- 修复 `audiobook convert --help` 与 `audiobook clone say --help` 直接崩溃：帮助文字里的 `+0%`
  被 argparse 当作格式符（`ValueError: unsupported format character`），现转义为 `%%`。
- 词典共 723 条；测试新增 CLI 两种语言的 `--help` 均可渲染、`--lang` 两种写法、英文 `engines` /
  `config` 无中文、Web 页面与接口英文化且中文页面不变、页面中没有未标记的中文。
- **测试与构建不受系统语言影响**：按中文文案断言的测试模块自行固定 `AUDIOBOOK_LANG=zh`（unittest 不加载
  conftest），打包流程 `build.yml` 的测试步骤改用 pytest 并显式设置语言——否则 macOS / Windows 英文构建机
  上界面会自动切成英文，25 项测试失败、发布构建中断（发布前在本地以英文系统模拟复现并修复）。
  调用子进程的 i18n 测试统一 UTF-8 输出，避免 Windows 管道默认 cp1252 编码中文时报错。

- 新增 `audiobook.i18n`：以中文原文为键的 `tr()`，英文词典在 `audiobook/locales/en.py`；
  缺译时回落中文、不会空白。语言优先级：环境变量 `AUDIOBOOK_LANG` > 配置 `language` > 系统语言。
  GUI 进程优先用 Qt 的系统区域判断（macOS 从 Finder 启动时 `LANG` 常为空）。
- 「外观」面板新增「界面语言 / Language」：跟随系统 / 简体中文 / English，重启后生效
  （提示用双语写，切换后立刻看得懂）。配置 schema 新增 `language`。
- 覆盖范围：主窗口、生成队列、语音转文字、模型管理器、Piper 语音包目录、声音克隆、新建音色、
  首次运行向导的全部文案，以及会显示在界面上的引擎就绪提示、安装指引、依赖缺失清单、生成 / 下载 /
  导出的错误信息。英文界面实测扫描 346 条可见文字，除语言名「简体中文」外无中文残留。
- 语音下拉框显示译名（如 "Xiaoxiao (female, natural)"），取值仍用原始显示名作为查找键，
  已有配置与进度文件不受影响。
- 保持中文的内容：日志（便于排查）、送进 TTS 合成的试听例句（内置语音多为中文语音）、
  声音克隆桥接子进程的报错（它运行在用户自己的 CosyVoice 环境里，不能导入本程序的包）。
- 修复三处依赖界面文字做判断的逻辑（翻译后会失效）：输出模式单选、ASR 输出格式、ASR 语言
  改用控件属性 / itemData 取值。
- 修复英文下右栏被撑宽出现横向滚动条：存储目录说明改为自动换行（中文下也更宽裕）。
- 修复底栏一直显示「未选择语音」：首次选中引擎时底栏尚未创建，界面建好后补刷新一次。
- 新增 `tests/test_i18n.py`：语言解析与优先级、词典占位符一致性、**源码中每个 `tr()` 文案都有英文**
  （漏翻会报出位置）、导入后端模块不提前确定语言、英文界面无中文残留（子进程实际构建各窗口）、
  语言设置写入配置。

### 内部收尾：Piper / Edge 实现迁入各自引擎模块

- **Piper 实现整体迁入 `audiobook.engines.piper`**：语音列表与官方语音目录、模型下载、线程本地
  模型缓存、Python 包 / CLI 双后端都归 `PiperEngine` 所在模块；`tts_engine` 只保留同名再导出。
  再导出的可变状态（`PIPER_VOICES` / `PIPER_MODEL_PATHS`）改为原地更新，刷新后旧引用不会过期。
- **Edge 实现与整批异步编排迁入 `audiobook.engines.edge`**，经新的 `EdgeEngine.run_batch` 接入注册表。
- `Engine` 基类新增整批生成的三个可选钩子：`run_batch`（引擎自带整批编排）、`batch_parallelism`
  （通用编排的并行章节数）、`release`（整批结束后释放资源）。`convert_batch` / `_generate_one_safe`
  全部经注册表分发，不再出现 `if engine == "edge" / "piper"` 特判；通用编排拆成串行 / 并行两个助手。
- **修复 Piper 嵌套重试**：引擎单段原语此前调用自带重试的实现，与编排层重试叠加，持续失败的章节
  会尝试 16 次、单是退避等待就约 175 秒（界面像卡死）。现在引擎只尝试一次，重试统一归编排层（4 次）。
- **修复未知引擎被静默当作 Edge**：`_generate_one_safe` 遇到未知引擎 id 时此前会走 Edge 分支；
  现在直接报 `不支持的引擎类型`，也不再进入重试循环白白等待。
- **兼容早期 Piper 语音包**：rhasspy/piper 早期 Release（v0.0.x，网上教程常引用）的配置文件把
  `phoneme_type` 写成 `"PhonemeType.ESPEAK"`，piper-tts 1.x 拒绝加载（报
  "'PhonemeType.ESPEAK' is not a valid PhonemeType"）。现在加载时自动使用规范化副本，不改动原文件。
- Piper 的 WAV→MP3 改用通用 `_wav_to_mp3`：与就绪检测一致只要求 ffmpeg（此前没装 pydub 时就绪检测
  显示可用、生成时却报"ffmpeg 未安装"）。
- **CI 的 GUI 测试此前从未真正执行**：full 腿缺 Qt 所需的 libEGL 等系统库，18 项 GUI 测试整体跳过。
  现在补装系统库并设置 offscreen 平台。
- 新增 `tests/test_engine_batch_hooks.py`（钩子默认值、重试归属、未知引擎、Edge 未安装、旧语音包配置、
  再导出状态一致性）。真实模型端到端验证：Piper 并行批量生成 3 章，有效 MP3、逐章事件正确、结束后卸载模型。

### 内部清理

- CosyVoice 单段合成实现从 `tts_engine` 迁入 `audiobook.engines.cosyvoice`（`CosyVoiceEngine.synthesize_segment`
  + `resolve_speaker`）；与 clone 引擎共用 `speed_from_rate`，去掉重复实现。
- `_raw_segment_synth` 只保留外挂插件引擎分支：内置引擎早已各自实现 `synthesize_segment`，旧的
  local / piper / cosyvoice / edge 兜底分支是死代码；随之移除 `tts_engine` 对 `_local_generate` 的再导入。
- 修复退出时的「Logging error / I/O operation on closed file」：`unload_whisper_model()` 未加载模型时不再
  做任何事（此前退出钩子每次都打日志，测试进程退出时日志流已关闭即报错）。
- 清理未用导入 / 变量（GUI、main、tts_engine、asr、测试），GUI 模块文档串去掉写死的版本号；
  pyflakes 除有意的再导出 / 可用性探测外全部清零。

### 「CosyVoice」引擎改走桥接进程

- 旧实现在本程序进程内 `from cosyvoice import CosyVoice`：打包版的 Python 里根本没有 torch / CosyVoice，
  源码版也与当前官方接口不符（方法名拼写、把 torch 张量当 numpy 拼接、librosa 变速），实际上跑不通。
- 现在与「声音克隆」共用同一套 CosyVoice 环境设置与常驻桥接进程，使用 **CosyVoice-300M-SFT 的预置说话人**
  （中文女 / 中文男 / 粤语女 / 英文女 / 英文男 / 日语男 / 韩语女）：
  - 桥接端新增预置说话人合成（`inference_sft`，语速用 CosyVoice 自身的 `speed`）与 `speakers` 命令；
    兼容 `list_available_spks` / 旧拼写 `list_avaliable_spks`；说话人不存在时报出可用列表。
  - 桥接端同一时间只保留一个模型：克隆与预置说话人交替使用时自动切换，避免两个模型同时占用显存。
  - 就绪检测：解释器 + `cosyvoice-models/` 下的 SFT 模型；未就绪时仍可回退外挂引擎 CLI，否则报出具体原因与安装指引。
  - 历史保存的语音 ID（`default_female` / `default_male`）继续可用。
- 移除进程内模型缓存与卸载逻辑；删除 CosyVoice 模型时结束桥接进程。不再在生成时自动下载模型
  （改为在模型管理 / 声音克隆设置里下载，后者的下载列表也加入了 300M-SFT）。
- 依赖检测面板显示「CosyVoice」与「声音克隆」各自的就绪状态。
- 测试：替身 CosyVoice 增加预置说话人接口，端到端覆盖桥接合成、模型切换、整本书生成（只加载一次模型）、
  未就绪回退外挂引擎等。

### 应用内下载 CosyVoice 模型

- 新增 `audiobook.io.hub`：纯 HTTP 的模型仓库整库下载（等同官方说明里的 `snapshot_download`，不依赖
  huggingface_hub / modelscope SDK）。按 HuggingFace → hf-mirror → ModelScope 魔搭 顺序取文件清单，
  单个文件失败时自动换源；先下到隐藏的 `.<名>.partial/`，全部完成才改名为正式目录（半成品不会被当成
  可用模型）；中断 / 暂停后再次下载跳过已完成文件、未完成的断点续传；下载前检查磁盘空间；校验文件大小；
  拒绝清单中的不安全路径。
- CosyVoice 模型清单改为官方仓库：**Fun-CosyVoice3-0.5B**（克隆推荐）、**CosyVoice2-0.5B**、
  CosyVoice-300M-SFT。原先的 300M-SFT 单文件 `.tar.gz` 地址官方并不提供，已移除。
- 「声音克隆 → 环境设置」页新增「下载模型」：选择型号、进度条、暂停（关闭对话框也会暂停，可续传）；
  下载完成后自动识别模型目录并刷新就绪状态。模型管理器与 `audiobook models get cosyvoice <名>` 同样可用。
- 模型管理器列出未下载完的半成品（「未下载完整，再次下载可续传」），可删除释放空间；克隆模型自动查找
  与 CosyVoice 语音列表都会跳过半成品目录。
- 新增 `tests/test_hub.py`（本地 HTTP 服务模拟 HF / ModelScope 接口，覆盖换源、续传、大小校验、
  磁盘空间、路径安全）及模型管理器 / 克隆 / 界面相关用例。

### C1-2 声音克隆界面

- 语音参数面板新增「🎙 克隆音色…」入口，打开「声音克隆」对话框：
  - **音色**页：列表（名称 / 时长 / 文字稿，缺文字稿会提示效果较差）、新建、编辑文字稿、重命名、删除；
    输入任意句子「试听所选音色」（后台线程合成，提示首次加载模型较慢）；环境未就绪时自动跳到设置页。
  - **环境设置**页：安装步骤说明；填写 / 浏览 Python 解释器、CosyVoice 仓库、模型目录（留空显示自动
    检测到的值）；「检测环境」会启动桥接进程并实际加载一次模型，逐项报告（解释器版本、cosyvoice 能否导入
    及原因、模型类型与采样率）。保存设置后自动按新配置重启桥接进程。
  - **新建音色**：选参考音频后立即按建档相同方式规整并显示实际时长（超 30 秒提示已截取、短于 3 秒禁止创建）；
    「自动识别（Whisper）」对规整后的那段音频识别文字稿，确保文字稿与实际使用的音频一致；默认用文件名命名。
  - 对话框始终显示「请只克隆你本人或已获明确授权的声音」。
- 音色 / 环境变化后自动刷新引擎卡片就绪状态与语音列表（保持当前选中的引擎）。
- `profiles.prepare_reference()`：建档前预处理参考音频（供界面显示时长与识别文字稿）。
- 新增 `tests/test_gui_clone.py`（7 项，offscreen 驱动真实对话框；无 Qt 环境自动跳过）。

### C1-1 声音克隆核心（后端 + CLI）

- **音色档案**（`audiobook/cloning/profiles.py`）：参考音频经 ffmpeg 规整为 16 kHz 单声道 WAV，
  短于 3 秒拒收、超过 30 秒截取前 30 秒；保存文字稿；增删改查，ID 校验防路径穿越；重名音色自动区分。
- **桥接子进程**（`bridge_worker.py` + `client.py`）：CosyVoice 依赖 torch、以 git 仓库方式安装，
  打包版的冻结 Python 无法直接 import，因此在用户自己的 CosyVoice 环境里常驻一个桥接进程（模型只加载
  一次，逐行 JSON 协议）。按官方当前接口实现：`AutoModel` 自动识别 CosyVoice / CosyVoice2 / CosyVoice3；
  有文字稿走 `inference_zero_shot`、无文字稿走 `inference_cross_lingual`；CosyVoice3 自动加
  `<|endofprompt|>` 系统前缀；同一音色用 `add_zero_shot_spk` 只提取一次说话人特征（文字稿修改后自动
  重新登记）；老版本「提示音频须为张量」的接口自动回退。暂停时立即结束推理进程，下次自动重启；
  进程崩溃 / 超时给出带日志尾部的错误；库的输出导到日志，不污染协议。
- **clone 引擎**：注册为内置引擎「声音克隆（CosyVoice）」，语音即克隆音色（`clone:<id>`），走注册表
  同步分发，批量生成 / 生成队列 / 续传均可用。模型目录自动在 `cosyvoice-models/` 下找
  CosyVoice3 > CosyVoice2 > CosyVoice（排除不支持复刻的 SFT / Instruct）。
- **配置**：新增 `cosyvoice_python` / `cosyvoice_repo` / `clone_model_dir`；`audiobook config --set KEY=VALUE`
  可直接修改配置（按 schema 校正类型）。
- **CLI**：`audiobook clone list | add | rm | check [--load] | say`；`convert --engine clone --voice <音色>`。
- 打包：三份 spec 以数据文件形式附带 `bridge_worker.py`（它由外部 Python 执行，必须是真实 .py 文件）
  并补 hiddenimports；模型管理器删除 CosyVoice 模型时一并结束桥接进程。
- **测试**（`tests/test_clone.py`，25 项）：真实 CosyVoice 无法在 CI 运行，测试用一个仿官方接口的替身
  `cosyvoice` 包真实启动桥接子进程端到端验证协议与调用参数（前缀、说话人缓存、跨语种、旧接口回退、
  错误 / 崩溃 / 暂停恢复），并跑通 `convert_batch(engine="clone")` 与全部 CLI。
  **真实模型的出声效果需在装好 CosyVoice 的机器上验证。**

### 阶段 3 / C2 工作区重设计（U1 / U3 / U2）

### U2 首次运行向导

- 新增三步向导：① 选使用场景——联网 · 音质最好（Edge，零下载）/ 完全离线（Piper 中文语音）/
  离线 + 语音转文字（再加 Whisper base）/ 系统自带语音；引擎软件未安装的场景置灰并写明原因，
  默认选中推荐项；② 自动下载该场景还缺的模型（已装的跳过），逐项显示进度与结果，失败可只重试
  失败项，也可先继续；③ 试听验证，点「完成」后选中并记住该引擎。
- 自动弹出规则：仅新用户首次启动（配置里没有 `wizard_completed` 且没有窗口位置记忆）；跳过或
  关闭都记为已完成，不再打扰。老用户升级后不会自动弹出，可在「存储与依赖」面板手动打开。
- 新增 `audiobook/onboarding.py`（场景定义、缺失模型计算、可用性 / 推荐、弹出判定、完成记录），
  下载复用 `audiobook.models.download`。
- 配置 schema 新增 `default_engine`（启动默认引擎）与 `wizard_completed`（布尔）；schema 支持 bool
  类型（兼容手改配置写成的 "true"/"1" 等）。
- **修复**：引擎按钮在依赖刷新 / 模型增删时重建，此前总会跳回第一个引擎（Edge）——现在保持当前
  选择；用户手动切换的引擎会被记住，下次启动默认选中。
- 新增 `tests/test_onboarding.py`（15 项）与 `tests/test_gui_wizard.py`（5 项，offscreen 驱动真实向导：
  离线流程下载→试听→完成、联网跳过下载、失败重试、跳过；引擎选择保持 / 记忆的回归）。

### U3 生成队列可视化

- 新增「生成队列」页：逐章显示 等待 / 生成中 / 完成 / 失败 / 未勾选，失败行显示错误原因并带
  「重试」按钮，另有「重试全部失败项」与「打开输出目录」；标签页标题实时显示进度
  （如「生成队列 (12/20) · 失败 2」）。开始生成时自动切到该页，底部暂停 / 继续条保留可用
  （仅「语音转文字」页隐藏）。完成弹窗与状态行会提示失败数量。
- `convert_batch` 新增可选参数：`item_callback(event, data)` 推送逐章事件
  （init / running / done / error / pending / final，覆盖 Edge 异步、Piper 并行、同步三条分发路径；
  回调异常不影响生成）；`only_indices` 配合 `resume=True` 只重跑指定条目，其余未完成条目本次暂挂、
  状态不变（临时标记不写入进度文件）。重试时清掉该条目上次的错误信息。不传则行为与之前完全一致。
- 续传 / 重试沿用本次生成的文件名前缀（此前续传固定用「有声读物」，写入元数据时专辑名会变）。
- **修复（既有缺陷）**：Edge 批量生成结束时停止监视任务抛出的 `asyncio.CancelledError`
  （Python 3.8+ 继承 `BaseException`）未被 `except Exception` 捕获，会逃逸出 `convert_batch`：
  界面收不到完成信号、srt 字幕 / ID3 元数据 / 响度归一化后处理不执行、进度文件不清理。
  因 Edge 需联网，此前在 CI / 容器里没有端到端覆盖，本次用打桩的 Edge 合成补上回归测试。
- 新增 `tests/test_generation_queue.py`（10 项）与 `tests/test_gui_queue.py`（3 项，offscreen 驱动
  真实主窗口；无 Qt 环境自动跳过）。

### U1 模型管理器

- 新增 `audiobook/models.py`：统一盘点 Piper 语音包（`piper-models/` + 外部模型文件夹）、
  CosyVoice 模型（`cosyvoice-models/`）、Whisper 模型（预下载布局与 faster-whisper HF 缓存布局），
  给出类型 / 名称 / 占用空间 / 位置；`delete_model()` 删除前先卸载内存中的模型实例、删除后刷新
  语音列表；`downloadable()` / `download()` 统一分发到既有下载实现（首次运行向导将复用）。
- 安全边界：只有真实路径位于便携存储目录内的模型才可删；「添加模型文件夹」的外部目录只读展示，
  符号链接指向存储目录外的同样拒删；未下载完整的 Whisper 目录单独标注、不计为已安装。
- 界面：存储面板原先分散的「下载 Piper 模型 / 更多语音包… / 下载 CosyVoice 模型」三个按钮收进
  一个「🗂 模型管理器」对话框——按类型分组显示占用小计与总占用，可删除、打开所在文件夹、下载
  常用模型（含 Whisper 各规格），并可进入 Piper 多语言语音包目录；生成进行中禁止删除模型。
  依赖检测面板新增「模型总占用」一行。
- CLI：`audiobook models list | available | get | rm`。
- PyInstaller 三份 spec 补 `audiobook.config` / `audiobook.models` hiddenimports。
- 新增 `tests/test_models.py`（21 项：盘点各布局、外部/符号链接拒删、删除副作用、下载分发、CLI）。

## v6.0.0 (2026-10，源码定版；未单独构建发布，内容并入 v6.1.0)

> **v6.0.0 正式版发布说明**：v6.0 大版本重构 + 产品形态扩展全部完成，自 beta.1 转正式版。
> 功能与 v5.2.x 完全一致（多引擎 TTS / ASR / m4b / srt / 对话识别等），全程行为不变、
> 集成测试兜底（172 项）。
>
> **本版累计交付**（beta.1 → 6.0.0）：
> - **阶段 1 架构**：移除 Tkinter（仅 Qt6）、分层 `audiobook` 包（core/io/engines）、
>   统一 `Engine` 抽象接口 + 注册表、`_generate_one_safe` 分发改走注册表；
>   **A5b 全引擎具体化**——local/edge/piper/cosyvoice 均为独立 Engine 子类，通用适配器
>   仅服务外挂插件。
> - **阶段 2 产品形态**：`audiobook` 命令行 + 库 API（B2）、集中式配置 schema（B3，
>   `audiobook.config` + CLI `config`）、本地 Web UI（B4，FastAPI + `audiobook serve`）。
> - **界面修复**：章节列表可拖动调整高度（竖向分隔条把手 + 兜底高度）。
>
> PyInstaller 三平台 spec 已补 `audiobook` 包（含各引擎模块）hiddenimports 以确保打包。
> 各形态详见下方分节。

### 界面修复 — 章节列表高度（beta.1 后）

- 竖向分隔条（章节列表 / 文本内容之间）此前 QSS 只设了 `width`，而竖向把手厚度由
  `height` 决定，导致把手塌成细条、**无法拖动调整高度**；改为按方向分别设置
  `:horizontal{width}` / `:vertical{height}`（深浅两套 QSS），并 `setHandleWidth(6)`。
- 章节列表易被压到只显示一两行：给章节树 `setMinimumHeight(160)`（约 7 行兜底），
  分隔条 `setChildrenCollapsible(False)` 禁止塌陷，初始高度分配偏向章节列表，
  并在窗口显示后再套用一次初始比例（构造期 `setSizes` 常被忽略）。

## v6.0.0 大版本重构（阶段 1 已完成，见 ROADMAP.md「v6.0 大版本升级计划」）

主题：架构跨越 + 产品形态扩展。分三阶段，每阶段可独立发布。

### 阶段 1（地基重构）— 本次进度

- **A1 删除遗留死代码**：`gui.py`(2361 行)、`main_pyside6.py`(104 行)，无任何引用。
- **A2 移除 Tkinter 后端**（破坏性）：删除 `gui_tkinter_backup.py`(2354 行)；`main.py`
  改为要求 Qt6（PySide6/PyQt6），未安装时给出安装提示并退出，不再回退 Tkinter；
  移除 `sv-ttk` 依赖与三个 PyInstaller spec 中的相关引用。
- **A3 建立 `audiobook/` 包骨架**：`core/`(章节·对话·拆分·文本)、`engines/`、
  `io/`(读取·输出)、`ui/` 四个子包；包 `__init__` 用 PEP 562 延迟再导出暴露稳定的
  公共导入面（`from audiobook import VERSION, convert_batch, transcribe`），
  `import audiobook` 本身很轻，不触发底层重依赖加载。实现暂留顶层模块，后续阶段
  （A4）逐步迁入并以薄再导出保持兼容。

说明：本阶段行为不变（98 项单元测试保持通过、GUI 可正常启动），未改动构建流程。

- **A6 集成测试基线**（已并入）：新增包导入面 / IO / 本地引擎集成测试（+18），
  CI full 腿加装 ffmpeg+espeak-ng 使其真正执行。
- **A4 单体拆分迁移（进行中）**：按「实现迁入包 + 顶层薄垫片 + 契约测试」范式逐个迁移：
  - `file_reader.py` → `audiobook/io/readers.py`
  - `asr_engine.py` → `audiobook/engines/asr.py`（对 tts_engine 的引用为函数内惰性导入，不受影响）
  顶层同名模块保留为薄再导出垫片（保持旧 import 与 PyInstaller 兼容）；每迁一个补垫片契约测试。
  - `tts_engine` 的**纯文本处理层** → `audiobook/core/text.py`：章节识别 /
    对话·说话人识别 / 文本分段 / 时长估算 / 文件名清理 / SRT 生成及其常量
    （CHAPTER_PATTERNS / DIALOGUE_PATTERNS / SPEAKER_PATTERN / _SENTENCE_SPLIT_RE）
    共约 260 行迁出；`tts_engine` 顶部再导入这些名字，`from tts_engine import
    detect_chapters, ...` 等旧用法与内部调用保持不变（导入方向反转，非整模块搬移）。
  - `tts_engine` 的**音频输出层** → `audiobook/io/audio.py`：`merge_mp3_files` /
    `_merge_mp3_files` / `get_audio_duration` / `export_m4b` / `build_ffmetadata_chapters` /
    `_ffmeta_escape` / `write_id3_tags` / `normalize_loudness`（约 206 行）。这些函数依赖
    tts_engine 的 ffmpeg 定位/子进程助手/pydub 配置，故在函数内以 `import tts_engine as _te`
    惰性引用同一批已配置对象——模块加载期无循环导入，行为不变；`tts_engine` 顶部再导入
    保持旧 import 与内部调用不变。

- **A5a 引擎抽象接口 + 注册表**（`audiobook/engines/base.py`）：定义统一的 `Engine`
  抽象基类（`is_ready` / `list_voices` / `synthesize`）与注册表（`register_engine` /
  `get_engine` / `all_engines`）。以**薄适配器** `_TtsEngineAdapter` 包住 `tts_engine`
  现有的按引擎分发（`check_engine_ready` / `get_voice_list` / `get_voice_id` /
  `_generate_one_safe`），**纯附加、零行为变化**。

- **A5b 引擎具体化（已完成）各引擎成为独立 Engine 子类**：
  - **本地系统语音** → `audiobook/engines/local.py`：`_local_generate` 及三个平台实现
    （macOS say/afconvert、Windows SAPI、Linux espeak-ng）迁出，新增 `LocalEngine`
    （Engine 子类）；`get_engine("local")` 经 `base._CONCRETE` 返回真实 `LocalEngine`。
    `tts_engine` 再导入 `_local_generate` 保持 local 分支不变。
  - **Edge / Piper / CosyVoice 具体化** → `audiobook/engines/{edge,piper,cosyvoice}.py`：
    新增 `EdgeEngine` / `PiperEngine` / `CosyVoiceEngine`，`is_ready` / `list_voices`
    沿用 `tts_engine` 既有分发，`synthesize_segment` 委托各自的现有合成函数
    （`_edge_generate` / `_piper_generate_safe` / `_cosyvoice_generate_safe`）。三者
    登记进 `base._CONCRETE`，`get_engine()` 返回具体子类，**通用 `_TtsEngineAdapter`
    自此只服务外挂插件引擎**。`display_name` 与注册表 name 一致，CLI/界面文案不变；
    piper/cosyvoice 的重实现（模型缓存、CLI/Python 双后端等）暂留 `tts_engine`，可后续
    整体迁入各自模块。Edge 的异步批量路径仍在 `_generate_one_safe`（编排层，不变）。
    三个新模块加入三份 PyInstaller spec 的 hiddenimports（懒加载需显式声明）。
    新增 `tests/test_engines_concrete.py`（解析为具体类、display_name、委托、外挂仍走适配器）。

- **A5c（关键步）分发改走注册表**：`Engine` 新增单段原语 `synthesize_segment`
  （抽象），`synthesize` 改为基类默认实现（分段+合并调用 `synthesize_segment`）。
  `_generate_one_safe` 的按引擎 if/elif（local/piper/cosyvoice/external，约 75 行）
  收敛为一处：`get_engine(engine).synthesize_segment` —— 注册表成为逐段合成的分发中枢，
  分段/合并/进度/重试的编排仍留在 `_generate_one_safe`。适配器 `synthesize_segment`
  经新增的 `tts_engine._raw_segment_synth(engine)` 解析原语，不再回调 `_generate_one_safe`
  （消除潜在递归）。**Edge 暂保留异步批量专用路径**（`_edge_generate_multi` 单事件循环），
  待其具体化后并入。行为不变，本地引擎经注册表分发的 convert_batch 端到端实测通过。

剩余：piper/cosyvoice 重实现整体迁入各自模块、Edge 异步批量编排并入注册表 见 ROADMAP。

### 阶段 2（产品形态）— B2 命令行 / 库 API

- 新增 `audiobook/cli.py` + `python -m audiobook` 入口 + 可安装的 `audiobook` 命令
  （pyproject 声明 `[project.scripts]`）。子命令：
  - `convert <文件...>`：多格式读取 → 章节/时长/单文件拆分 → mp3，可选 `--m4b`/`--srt`/
    `--metadata`/`--normalize`/`--dialogue`，`--engine`/`--voice`/`--rate`/`--split` 等。
  - `engines`：列出引擎与就绪状态；`voices --engine X`：列出语音；`transcribe`：ASR。
- 复用 v6.0 分层实现与引擎注册表（`get_engine`/`all_engines`/`LocalEngine`），无需 GUI/Qt。
- 新增 `test_cli`：列表命令、参数校验、本地引擎 convert 端到端（gated espeak+ffmpeg）。
  让"可编程"形态可用且可在 CI 真实测试。

### 阶段 2（产品形态）— B3 配置 schema

- 新增 `audiobook/config.py`：`SCHEMA` 一处权威定义 config.json 各项（默认值/类型/
  取值范围/说明），`defaults()`/`normalize()`/`effective()` 纯函数、零依赖。
- `tts_engine._load_config` 加载后经 `normalize` 规范化：已知键坏值回退默认、未知键
  原样保留（前向兼容）、缺失键不注入（磁盘文件形态不变）；规范化异常退回原始值。
- CLI 新增 `config` 子命令查看生效配置。默认值与历史 `.get()` 回退逐项一致，行为不变。

### 阶段 2（产品形态）— B4 本地 Web UI

- 新增 `audiobook/web/`（FastAPI）：浏览器界面，可跑在 NAS/服务器批量转换，绕开
  PyInstaller 打包。接口 `engines`/`voices`/`convert`（后台线程任务 + 进度轮询）/
  `jobs`/下载；前端单页 `index.html`（深浅色自适应）。
- CLI 新增 `serve` 子命令（`audiobook serve --host --port`，需 `[web]` 可选依赖）。
- 复用 `tts_engine.convert_batch` 与引擎注册表、读取器，不重复实现转换逻辑；FastAPI 等
  重依赖延迟导入，`import audiobook` 仍很轻。产物落在 `<存储目录>/web_output/<job>/`，
  下载接口做了目录穿越防护。
- `fastapi`/`uvicorn`/`python-multipart` 为可选依赖（pyproject `[project.optional-dependencies].web`）。
- 新增 `test_web`（TestClient，打桩 convert_batch，不依赖 espeak/ffmpeg）。
- CI 测试运行器由 `unittest discover` 改为 `pytest`，并在两腿装 `pytest`/`httpx`、full 腿
  装 Web 依赖，使此前未被 unittest 收集的函数式用例（含 B3/B4）在 CI 真正执行。

## v5.2.1 (2026-09-22)

## v5.2.1 本次升级内容（Bug 修复 + 界面打磨）

### B1：Linux 本地语音（espeak-ng）检测修复
- 旧逻辑用 `lang.startswith("zh")` 过滤 `--voices=zh` 输出，在新版 espeak-ng(1.51) 上
  只会命中 `chinese-mb-cn1`（MBROLA 语音），而取 VoiceName 列作 voice_id 后，缺少
  mbrola 二进制与语音数据时合成直接失败；可用的普通话语音 `cmn` / `cmn-latn-pinyin`
  反因语言列为 `cmn` 被过滤掉。
- 新逻辑：接受 `zh` / `cmn` / `yue` 中文语系，跳过 `mb/` 开头的 MBROLA 条目，改用
  语言标识符（espeak-ng 可直接以 `-v <lang>` 解析）作为 voice_id。

### U1：界面布局与易用性优化（gui_pyside6.py）
- ASR「语音转文字」页隐藏底部 TTS 专用操作条（开始转换/停止/继续生成）与状态行，
  消除与页内「开始识别」的重复与误导（新增 `currentChanged` 处理）。
- 语音参数面板：下拉框改为可压缩（最小宽 120），「试听/刷新」按钮独立成行，
  窄窗口下不再被挤出可视区域而裁切。
- 左栏「章节列表」与「文本内容」改用竖向分隔条，用户可自由分配二者高度
  （文本内容为主要编辑区，默认占比更大）。
- 文本编辑区改用系统默认字体（仅指定字号），对中文回退更可靠，替换硬编码的 Helvetica。

### B2：输出文件名瑕疵修复（tts_engine.py）
- 直接输入文本（无来源文件）按章节拆分时，输出文件名不再插入 "untitled"
  （空 source 经 `sanitize_filename` 回退为 "untitled" 所致，现先判空）。

## v5.2.0 (2026-07-11)

## v5.2.0 本次升级内容（P2 功能扩展 + macOS 包瘦身，按 ROADMAP.md 实施）

### F0：macOS 包体积瘦身（v5.1.0 实测 1.4GB → 预期 ~200MB）
- mac spec 移除 `collect_data_files/collect_submodules/collect_dynamic_libs('PySide6')` 全量收集
  （曾把 QtWebEngine 内嵌 Chromium、QtQuick、Qt3D 等全部打进包）
- 改为 hiddenimports 按需声明（与 Windows spec 一致），excludes 显式排除全部重型 Qt 模块
- 清理指向本地 Homebrew 的 PyQt6 插件收集死代码；info_plist 版本号同步

### F1：Piper 语音包在线目录
- `fetch_piper_voice_catalog()`：下载官方 voices.json（数百个多语言语音），本地缓存 + 镜像回退
- `download_piper_voice_from_catalog()`：按目录下载 .onnx + .onnx.json
- `refresh_piper_voices()`：懒扫描 piper-models/ 与额外搜索路径，已下载模型自动并入语音列表
  （`PIPER_MODEL_PATHS` 支持任意位置的模型文件）
- GUI「🌐 更多语音包…」对话框：语言筛选、大小显示、后台下载（进度经全局监听器）

### F2：角色→音色多对多映射
- `extract_speakers()` 从文本提取说话人名单（按出现频次排序）
- `_resolve_segment_voice()` 统一语音解析：speakers 映射 > 旧格式顶层键 > 类型默认
- GUI 对话面板新增「从文本检测角色」+ 角色/语音映射表
- 修复说话人归属缺陷：连续多角色对话时归属取窗口内最后一个紧邻的「X说：」
  （此前取第一个，会归给上一个说话人）；SPEAKER_PATTERN 排除 ASCII 引号

### F3：Edge 引擎对话检测
- `_edge_generate_dialogue()`：Edge 异步批量路径支持 segments+voice_map 按段切换语音

### F4：ASR 批量转录
- AsrWorker 支持多文件队列（内置 + 外挂引擎），批量结果自动保存到各音频文件旁
- 文件选择改多选，逐文件进度显示

### F5：m4b 有声书 + ID3 元数据
- `export_m4b()`：ffmpeg concat + FFMETADATA 章节标记，输出 .m4b（AAC 64k）
- `write_id3_tags()`：ffmpeg 流复制写入标题/专辑/音轨/封面（无损快速）
- `get_audio_duration()`：ffprobe → pydub → 码率估算三级回退
- convert_batch 新增 `write_metadata` 选项（专辑=书名，标题=章节名，音轨=顺序）
- GUI：「📚 导出 m4b」按钮 + 「写入 ID3 元数据」复选框

### F6：srt 字幕同步生成
- `generate_srt_from_text()`：按句切分 + 字数比例分配时间轴（离线估算，引擎通用）
- convert_batch 新增 `generate_subtitles` 选项，字幕存到 MP3 同名 .srt
- GUI：「同时生成 srt 字幕」复选框

### 测试
- 新增 15 项测试（角色提取、语音解析、字幕、FFMETADATA、语音目录解析），共 98 项

## v5.1.0 本次升级内容（P0 稳定性 + P1 性能，按 ROADMAP.md 实施）

### S1：edge-tts 改为可选依赖
- `import edge_tts` 包裹 try/except（`EDGE_TTS_AVAILABLE` 标志），与其他引擎一致
- 未安装时 `check_engine_ready("edge")` 返回安装提示，UI 显示"需安装"
- 修复了因此导致的测试收集失败（此前 72 项只能跑 20 项）

### S2：CI 测试
- 新增 `.github/workflows/test.yml`：push/PR 到 main 触发，minimal（零可选依赖）+ full（完整 requirements）双矩阵
- `build.yml` 两个打包 job 在 PyInstaller 之前先跑测试，测试不过不出包

### S3：CosyVoice 模型缓存 + 语音选择生效
- `_load_cosyvoice_model()` 模型实例缓存（此前逐章重新加载 600MB 模型）
- `_cosyvoice_resolve_spk()` 把界面语音解析为说话人 ID 传入 `inference_sft`（此前 voice 参数不生效）
- 批量结束 `_unload_cosyvoice_model()` 释放内存

### S4：Whisper 模型下载接入镜像 + 进度
- `_ensure_whisper_model()` 用 tts_engine 的下载基础设施预下载 Systran/faster-whisper-* 模型
  （断点续传 + hf-mirror 回退 + `add_download_listener` UI 进度）
- 已有 HF 缓存布局模型的老用户自动复用，不重复下载；预下载失败回退 faster-whisper 自带下载

### S5：健壮性修复
- Piper 并行分支不再 `except Exception: pass` 吞异常（改为记录日志 + 显式回退串行）
- CosyVoice tar 解压加 `filter="data"`（Python <3.12 回退）
- `.mp3`→`.wav` 路径替换改用 `os.path.splitext`
- ASR 临时文件改用 `tempfile.mkstemp` 唯一命名

### P1-1：系统语音懒加载
- 模块导入期不再同步枚举系统语音（Windows PowerShell 枚举最长 15s），改为首次访问时线程安全检测

### P1-2/P1-3：Piper 并行 + 并发自适应
- Piper 模型缓存改为线程本地（每线程 LRU 2 个），Python 模式也走 ThreadPoolExecutor 并行（此前仅 CLI 模式）
- `_ensure_piper_model` 加下载锁，防止并行线程同时下载同一模型
- `get_local_concurrency()`（CPU 自适应，config.json `concurrency` 覆盖，上限 16）
- `get_edge_concurrency()`（config.json `edge_concurrency` 覆盖，上限 10）

### P1-4：ASR 加速
- `transcribe()` 默认启用 `BatchedInferencePipeline`（batch_size=8，长音频约 3-4x），不可用时自动回退
- 新增 `compute_type` 参数 + PySide6 界面"精度"下拉框（auto/float16/int8_float16/int8）

### 测试
- 新增 11 项测试（并发边界、edge 降级、Piper 线程缓存、CosyVoice 说话人解析、Whisper 元数据），共 83 项

## 项目概览

一个桌面端文字转语音/语音转文字工具（PySide6/Qt6 GUI），将电子书文本转换为自然语音的 MP3 有声读物。支持 5 种 TTS 引擎和 ASR 引擎，运行于 macOS / Windows / Linux。

- **仓库**: https://github.com/yan43050030/audiobook-converter
- **Python**: 3.12+（打包用 3.12，开发使用 3.14）
- **测试**: 98 项 unittest（`python3 -m unittest discover -s tests -v`）
- **CI**: GitHub Actions，push/PR 到 main 跑测试（minimal/full 双矩阵）；推送 `v*` tag 触发 macOS + Windows 双平台 PyInstaller 打包（打包前先跑测试）

## 文件架构

```
audiobook_converter/
├── main.py              # 入口：HiDPI、Tk scaling、sv-ttk 主题初始化
├── gui.py               # Tkinter GUI（~2100行）：双标签页、右侧滚动面板、章节选择
├── tts_engine.py         # TTS 核心（~2600行）：5 种引擎、章节检测、对话检测、批量生成
├── asr_engine.py         # ASR 引擎（~276行）：faster-whisper 语音转文字
├── audio_player.py       # 内置播放器（~312行）：pygame.mixer 优先，系统播放器回退
├── requirements.txt      # 核心依赖
├── pyproject.toml        # pytest 配置
├── tests/                # 72 项单元测试
│   ├── conftest.py
│   ├── test_tts_engine.py
│   └── test_asr_engine.py
├── .github/workflows/
│   └── build.yml         # CI：tag 推送 → macOS/Windows PyInstaller 打包 → Release 直传
├── audiobook_converter_mac.spec   # macOS .app 打包
├── audiobook_converter_win.spec   # Windows .exe 打包
└── audiobook_converter.spec       # 通用打包
```

## 引擎矩阵

| 引擎 ID | 名称 | 类型 | 质量 | 速度 | 网络需求 | 模型下载 |
|---------|------|------|------|------|----------|----------|
| `edge` | Edge TTS | 在线云端 | ★★★★★ | 快 | 需要 | 无需 |
| `local` | 系统语音 | 离线系统 | ★★★ | 快 | 不需要 | 无需 |
| `piper` | Piper | 离线神经网络 | ★★★★ | 中 | 不需要 | 自动下载（≈50MB） |
| `cosyvoice` | CosyVoice | 离线神经网络 | ★★★★★ | 慢 | 不需要 | 一键下载（≈600MB） |
| 外挂引擎 | 外部插件 | CLI 协议 | 可变 | 可变 | 可变 | 用户自行管理 |

### Piper 引擎详情
- Python 包 `piper-tts>=1.0.0` 优先，失败回退 CLI（便携 bin/ 目录或系统 PATH）
- 模型自动下载到 `{storage}/piper-models/`，支持断点续传 + hf-mirror.com 镜像
- 2 个中文语音：huayan-medium、huayan-low
- LRU 缓存最多 2 个模型

### CosyVoice 引擎详情（v4.0 新增一等支持）
- Python 包 `cosyvoice` 优先，失败回退外部引擎 CLI
- 模型一键下载（GUI 按钮）到 `{storage}/cosyvoice-models/`
- tar.gz 自动解压，支持断点续传 + 镜像
- 语音从已下载模型中动态扫描
- 速度调节依赖可选的 `librosa`

### 外挂引擎协议
引擎放入 `{storage}/engines/{name}/`，需实现 CLI 接口：
```bash
{engine} --list-voices          # 返回 JSON 数组
{engine} --voice V --text T --output O.mp3 [--speed 1.0]
```

## 关键数据流

### 文字转语音 (TTS) 流程
```
加载文本 → detect_chapters() → 用户选择章节/模式
→ convert_batch() 构建 item 列表
→ [可选] detect_dialogue_segments() 对话检测
→ 引擎 dispatch（Edge 异步并发 / Piper CLI 线程池 / 其他同步逐条）
→ _generate_one_safe() 分段合成 → pydub/ffmpeg WAV→MP3
→ [可选] normalize_loudness() 响度归一化
→ 输出 MP3 文件
```

### 对话检测流程（v4.0 新增）
```
文本 → DIALOGUE_PATTERNS 扫描引号 → detect_dialogue_segments()
→ [{text, type: "narration"|"dialogue", speaker: str|None}]
→ _generate_one_safe_multi_voice() 按段选语音
→ 逐段 _generate_one_safe() → 合并为单个 MP3
```

### 进度与断点续传
- 进度文件：`{output_dir}/.audiobook_progress.json`
- item 结构：`{title, text, filename, status, chapter_idx, part_idx?, segments?, voice_map?}`
- 暂停 → save_progress() → 下次选择同目录 → 自动检测进度文件 → 继续

## v5.0.0 本次升级内容

### Qt6 全新界面
- 从 Tkinter 迁移到 PySide6/PyQt6（Qt6），保留 Tkinter 回退
- 侧栏导航 + QStackedWidget 面板切换，7 个设置面板互不干扰
- 底部 QuickBar（引擎/语音/语速/开始停止始终可见）+ StatusBar
- 引擎选择改为卡片网格，所有注册引擎（含 CosyVoice + 外部引擎）动态显示
- 暖色浅色主题 / 深色主题 QSS 样式表，圆角卡片、蓝色强调色
- QThread + Signal/Slot 替代 threading.Thread + root.after 线程模式
- 文件读取方法抽离为独立的 `file_reader.py` 模块

### 界面优化
- 引擎与语音：所有引擎始终可见，未安装显示"需安装"
- 依赖面板：高度 8→14 行，缺失项显示下载地址
- 字体统一：9pt→10pt，deps_text 10pt→11pt
- 新增"添加模型文件夹"和"添加程序文件夹"按钮，递归搜索
- 合并依赖显示：移除 engine_status_label，状态整合到依赖面板

## v4.0.0 升级内容

### B2：生成中引擎/语音锁定
- `_on_engine_change()` 在 `self.is_converting` 时被阻止
- `_set_engine_controls_state()` 统一管理所有引擎控件的启用/禁用
- 状态栏提示"切换将在下次生成时生效"

### D18：pytest 回归测试
- 新建 `tests/` 目录，72 项测试覆盖核心纯函数
- 重点测试：split_by_duration、detect_chapters、load_progress 迁移、ID3 合并、对话检测
- CI 预留了测试步骤（需网络安装 pytest）

### C11：CosyVoice 一键安装向导
- 模型下载基础设施：`COSYVOICE_MODEL_URLS`、`_download_cosyvoice_model()`、`_ensure_cosyvoice_model()`
- 复用 Piper 的下载进度监听器 + 镜像回退
- GUI 下载按钮 + 依赖面板 CosyVoice 状态行
- `_cosyvoice_generate()` 和 `_cosyvoice_generate_safe()` — Python API 路径 + 外部引擎回退

### C13：多人对话识别
- `DIALOGUE_PATTERNS`：中文双引号「""」、单引号「''」、日式引号「」、ASCII 双引号
- `SPEAKER_PATTERN`：从"XX说："提取角色名
- `detect_dialogue_segments(text)` → `[{text, type, speaker}]`
- `_generate_one_safe_multi_voice()`：按片段类型/说话人切换语音合成后合并
- GUI：启用复选框 + 旁白/对话语音下拉框

## 待实施（升级计划保留项）

| ID | 内容 | 优先级 | 预计工作量 | 备注 |
|----|------|--------|-----------|------|
| D19 | macOS 签名公证 + Windows codesign | 低 | 1-2天 | 需 Apple Developer 账号 + 代码签名证书 |
| - | C13 增强：说话人音色映射界面 | 中 | 1天 | 目前仅旁白/对话二分，需角色→音色多对多映射 |
| - | Edge TTS 对话检测支持 | 中 | 1天 | 当前对话检测仅同步引擎路径验证过 |
| - | Piper 更多语音包 | 低 | 半天 | 目前仅 2 个中文模型 |
| - | 多语言界面（i18n） | 低 | 2-3天 | 当前仅中文 UI |

## 已知注意事项

1. **macOS 语音检测**：启动时 `_detect_local_voices_macos` 可能因 `_quiet_popen_kwargs` 未定义而失败（模块加载顺序问题），不影响其他引擎
2. **网络代理**：pip 安装依赖需要通过代理（当前环境 `localhost:54503`），直接 pip 可能失败
3. **Python 3.14**：当前开发使用 3.14，audioop-lts 已处理 3.13+ 兼容；PyInstaller 打包使用 3.12
4. **大文本章节检测**：≥100k 字符走正则扫描路径，<100k 走逐行遍历，两个分支行为应一致（已有测试覆盖）
5. **对话检测准确性**：中文对话模式多样（直接引语、间接引语、无引号对话），当前正则方案是起点，未来可考虑 NLP 方案
6. **进度文件兼容性**：`load_progress()` 会自动补全老格式的 `chapter_idx` 和 `status` 字段，新字段 `segments`/`voice_map` 可安全序列化

## 发布流程

```bash
# 1. 确保所有测试通过
python3 -m unittest discover -s tests -v

# 2. 更新版本号（tts_engine.py VERSION + README.md）

# 3. 提交并打 tag
git add -A
git commit -m "v5.0.0: <描述>"
git push origin main
git tag v5.0.0
git push origin v5.0.0

# 4. CI 自动触发 macOS + Windows 构建，产物上传到 GitHub Release
```

## 本地打包命令

```bash
# macOS
pyinstaller audiobook_converter_mac.spec --clean --noconfirm
# → dist/AudiobookConverter.app

# Windows（在 Windows 上执行）
pyinstaller audiobook_converter_win.spec --clean --noconfirm
# → dist/audiobook_converter.exe
```
