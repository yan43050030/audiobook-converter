# 项目情况日志 — 文字转有声读物 (Audiobook Converter)

## 当前版本：v5.0.0 (2026-05-01)

## 项目概览

一个桌面端文字转语音/语音转文字工具（PySide6/Qt6 GUI），将电子书文本转换为自然语音的 MP3 有声读物。支持 5 种 TTS 引擎和 ASR 引擎，运行于 macOS / Windows / Linux。

- **仓库**: https://github.com/yan43050030/audiobook-converter
- **Python**: 3.12+（打包用 3.12，开发使用 3.14）
- **测试**: 72 项 unittest（`python3 -m unittest discover -s tests -v`）
- **CI**: GitHub Actions，推送 `v*` tag 触发 macOS + Windows 双平台 PyInstaller 打包

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
