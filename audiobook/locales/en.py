"""English UI strings. Keys are the original Chinese text passed to `tr()`.

Edit this file directly when adding or changing UI text: keep placeholders ({name})
identical to the key; tests/test_i18n.py lists any tr() string that has no translation.
Strings missing here fall back to Chinese — the TTS preview sample sentences are left
out on purpose, because the bundled voices are Chinese voices.
"""

MESSAGES = {
    # ---- gui_pyside6.py ----
    '文字转语音':
        'Text to Speech',
    '生成队列':
        'Generation Queue',
    '语音转文字':
        'Speech to Text',
    '章节列表（勾选要生成的章节）':
        'Chapters (check the ones to generate)',
    '全选':
        'Select all',
    '全不选':
        'Select none',
    '搜索章节...':
        'Search chapters...',
    '文本内容':
        'Text',
    '● 就绪':
        '● Ready',
    '选择引擎':
        'Engine',
    '语音参数':
        'Voice',
    '试听':
        'Preview',
    '刷新':
        'Refresh',
    '🎙 克隆音色…':
        '🎙 Clone a voice…',
    '用一段 5–15 秒的人声复刻音色（基于 CosyVoice）':
        'Clone a voice from a 5–15 second recording (powered by CosyVoice)',
    '+ 添加 / 配置外挂引擎':
        '+ Add / configure plugin engines',
    '语速控制':
        'Speed',
    '正常':
        'Normal',
    '输出模式':
        'Output',
    '30 分钟':
        '30 min',
    '响度归一化（统一各文件音量，需 ffmpeg）':
        'Normalize loudness across files (needs ffmpeg)',
    '同时生成 srt 字幕（按句估算时间轴）':
        'Also write .srt subtitles (per-sentence timing)',
    '写入 ID3 元数据（专辑/章节标题/音轨号，需 ffmpeg）':
        'Write ID3 tags: album / chapter / track (needs ffmpeg)',
    '快捷操作':
        'Tools',
    '🔊 试听全文（可暂停）':
        '🔊 Preview full text (pausable)',
    '多人对话':
        'Multi-speaker dialogue',
    '启用对话检测（为旁白/对话分配不同语音）':
        'Detect dialogue (separate narration / dialogue voices)',
    '角色音色映射（可选）':
        'Character voices (optional)',
    '🔍 从文本检测角色':
        '🔍 Detect characters in text',
    '未映射的角色使用统一的对话语音':
        'Unmapped characters use the dialogue voice',
    '检测到 {v1} 个角色；未映射的角色使用统一的对话语音':
        'Found {v1} characters; unmapped characters use the dialogue voice',
    '便携存储目录':
        'Portable storage folder',
    '选择文件夹':
        'Choose folder',
    '打开':
        'Open',
    '恢复默认':
        'Reset to default',
    'bin/ 存放可执行文件；piper-models/ 存放语音包。程序自动在子目录搜索。':
        'bin/ holds executables; piper-models/ holds voices. Subfolders are searched automatically.',
    '依赖检测':
        'Dependencies',
    '🗂 模型管理器（下载 / 删除 / 占用）':
        '🗂 Model manager (download / delete / disk usage)',
    '🧭 首次运行向导（选场景 / 备模型 / 试听）':
        '🧭 First-run wizard (setup / models / preview)',
    '已加载文件':
        'Loaded files',
    '未加载文件':
        'No files loaded',
    '主题':
        'Theme',
    '🔄 切换深色/浅色主题':
        '🔄 Toggle dark / light theme',
    '界面语言 / Language':
        'Language',
    '▸ 未选择语音':
        '▸ No voice selected',
    '▶ 开始转换':
        '▶ Start',
    '⏹ 停止':
        '⏹ Stop',
    '▶ 继续生成':
        '▶ Resume',
    '⏳ 等待':
        '⏳ Waiting',
    '🔊 生成中':
        '🔊 Generating',
    '✅ 完成':
        '✅ Done',
    '❌ 失败':
        '❌ Failed',
    '— 未勾选':
        '— Not selected',
    '尚未开始生成。开始转换后，这里会逐章显示状态；失败的章节可单独重试。':
        "Nothing generated yet. Once you start, each chapter's status shows here; failed chapters can be retried individually.",
    '🔁 重试全部失败项':
        '🔁 Retry all failed',
    '📂 打开输出目录':
        '📂 Open output folder',
    '未选择文件':
        'No file selected',
    'faster-whisper（内置）':
        'faster-whisper (built-in)',
    'auto: CPU 用 int8、GPU 用 float16\nint8_float16: GPU 上更省显存，速度接近 float16':
        'auto: int8 on CPU, float16 on GPU\nint8_float16: uses less GPU memory, nearly as fast as float16',
    '▶ 开始识别':
        '▶ Start transcription',
    '{val} 分钟':
        '{val} min',
    '本地':
        'System',
    '{dot} {v1} · {v2} 语音 · v{VERSION}':
        '{dot} {v1} · {v2} voices · v{VERSION}',
    '选择一个或多个文本文件':
        'Choose one or more text files',
    '支持的文档 (*.txt *.md *.markdown *.docx *.epub *.html *.htm *.pdf);;所有文件 (*.*)':
        'Supported documents (*.txt *.md *.markdown *.docx *.epub *.html *.htm *.pdf);;All files (*.*)',
    '已加载 {v1} 个文件（{total}字）':
        'Loaded {v1} files ({total} characters)',
    '选择便携存储文件夹':
        'Choose portable storage folder',
    'Piper 语音包: {v1} 个':
        'Piper voices: {v1}',
    '\n{v1} 当前引擎: {msg}':
        '\n{v1} Current engine: {msg}',
    '模型管理器':
        'Model Manager',
    '🔄 刷新':
        '🔄 Refresh',
    '🗑 删除选中':
        '🗑 Delete selected',
    '📂 打开所在文件夹':
        '📂 Open containing folder',
    '下载模型':
        'Download models',
    '⬇ 下载':
        '⬇ Download',
    '🌐 Piper 语音包目录…':
        '🌐 Piper voice catalog…',
    '关闭':
        'Close',
    '声音克隆':
        'Voice Cloning',
    '⚠ 请只克隆你本人或已获明确授权的声音。':
        '⚠ Only clone your own voice or voices you have explicit permission to use.',
    '＋ 新建音色…':
        '+ New voice…',
    '编辑文字稿':
        'Edit transcript',
    '重命名':
        'Rename',
    '删除':
        'Delete',
    '▶ 试听所选音色':
        '▶ Preview selected voice',
    '首次试听需要加载模型（GPU 约十几秒，CPU 可能一分钟以上），之后会快很多。':
        'The first preview loads the model (about 10–20 s on GPU, possibly over a minute on CPU); later ones are much faster.',
    '音色':
        'Voices',
    '声音克隆基于开源的 CosyVoice（依赖 torch，建议 NVIDIA 显卡），需要单独安装：<ol><li>按官方说明 <code>git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git</code> 并在独立的 conda / venv 环境里安装依赖；</li><li>用下面的「下载模型」获取 <b>Fun-CosyVoice3-0.5B</b>（或 CosyVoice2-0.5B），也可自行下载到存储目录的 <code>cosyvoice-models/</code> 下；</li><li>在下面填写该环境的 Python 与仓库路径，点「检测环境」。</li></ol>这里的环境同时用于「CosyVoice」引擎（预置中文男女声等，需下载 CosyVoice-300M-SFT）。详细命令见 README「声音克隆」一节。':
        "Voice cloning uses the open-source CosyVoice (requires torch; an NVIDIA GPU is recommended) and is installed separately:<ol><li>Follow the official instructions: <code>git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git</code> and install its dependencies in a separate conda / venv environment;</li><li>Use “Download models” below to get <b>Fun-CosyVoice3-0.5B</b> (or CosyVoice2-0.5B), or download it yourself into <code>cosyvoice-models/</code> in the storage folder;</li><li>Enter that environment's Python and repository paths below, then click “Check environment”.</li></ol>The same environment also powers the “CosyVoice” engine (preset Chinese male/female voices and more; needs CosyVoice-300M-SFT). See the “Voice cloning” section of the README for the exact commands.",
    'CosyVoice 仓库目录（git clone 下来的文件夹）':
        'CosyVoice repository folder (the git clone)',
    '暂停':
        'Pause',
    '保存设置':
        'Save settings',
    '🔍 检测环境（会实际加载一次模型）':
        '🔍 Check environment (loads the model once)',
    '检测结果会显示在这里。':
        'Results will appear here.',
    '环境设置':
        'Environment',
    '已暂停；再次点击「下载」会从断点继续。':
        'Paused; click “Download” again to resume where it left off.',
    '新建克隆音色':
        'New Cloned Voice',
    '例如：我的声音':
        'e.g. My voice',
    '未选择':
        'Not selected',
    '选择参考音频…':
        'Choose reference audio…',
    '建议 5–15 秒、单人、无背景音乐的清晰人声；短于 {MIN_SECONDS:.0f} 秒无法使用，超过 {MAX_SECONDS:.0f} 秒只取前 {MAX_SECONDS:.0f} 秒。':
        "Use 5–15 seconds of clear speech from one person with no background music; under {MIN_SECONDS:.0f} s won't work, and only the first {MAX_SECONDS:.0f} s of longer clips is used.",
    '🎧 自动识别（Whisper）':
        '🎧 Auto-transcribe (Whisper)',
    '逐字填写效果明显更好；留空也能用（只取音色）。':
        'A word-for-word transcript works noticeably better; leave it empty to clone the timbre only.',
    '取消':
        'Cancel',
    '创建':
        'Create',
    '首次运行向导':
        'First-Run Wizard',
    '跳过向导':
        'Skip wizard',
    '← 上一步':
        '← Back',
    '下一步 →':
        'Next →',
    'Edge 在线语音':
        'Edge online voices',
    'Piper 离线语音':
        'Piper offline voices',
    '系统语音':
        'System voices',
    '第 1 步 / 共 3 步 · 选择使用场景':
        'Step 1 of 3 · Choose a setup',
    '第 2 步 / 共 3 步 · 准备模型':
        'Step 2 of 3 · Get models',
    '第 3 步 / 共 3 步 · 试听验证':
        'Step 3 of 3 · Preview',
    '先选一个使用场景，向导会帮你备好需要的东西。之后可在「存储与依赖」面板随时重新运行本向导。':
        'Pick a setup and the wizard will get everything it needs. You can rerun the wizard any time from the storage / dependencies section.',
    '🔁 重试失败项':
        '🔁 Retry failed',
    '▶ 试听':
        '▶ Preview',
    '听到声音就说明一切就绪。点「完成」后默认使用该引擎，之后仍可在右侧面板随时切换。':
        "If you hear the sample, you're all set. After you click “Finish” this engine becomes the default; you can switch any time in the right-hand panel.",
    '已选择：<b>{title}</b>（引擎：{engine}）':
        'Selected: <b>{title}</b> (engine: {engine})',
    'Piper 语音包下载':
        'Download Piper Voices',
    '🔄 刷新目录':
        '🔄 Refresh catalog',
    '正在获取语音目录…':
        'Fetching voice catalog…',
    '⬇ 下载选中语音':
        '⬇ Download selected voice',
    '选择包含语音模型的文件夹':
        'Choose a folder containing voice models',
    '选择包含可执行文件的文件夹':
        'Choose a folder containing executables',
    '正在生成语音试听...':
        'Generating voice preview...',
    '语音试听播放中...':
        'Playing voice preview...',
    '⏹ 中止生成':
        '⏹ Stop generating',
    '正在生成首段试听...（{v1}字）':
        'Generating the first segment... ({v1} characters)',
    '全部段已生成，等待播放完毕':
        'All segments generated, waiting for playback to finish',
    '选择保存目录':
        'Choose output folder',
    '有声读物':
        'Audiobook',
    '选择之前保存的目录（包含进度文件）':
        'Choose the previous output folder (with the progress file)',
    '正在生成: {current}/{total} {elapsed}':
        'Generating: {current}/{total} {elapsed}',
    '完成! 共生成 {count} 个文件 {elapsed}':
        'Done! Generated {count} files {elapsed}',
    '\n  ... 等共 {count} 个文件':
        '\n  ... {count} files in total',
    '\n\n⚠ 另有 {failed} 个章节失败，可在「生成队列」页单独重试。':
        '\n\n⚠ {failed} more chapters failed; retry them individually on the “Generation Queue” tab.',
    '生成失败':
        'Generation failed',
    '正在暂停...':
        'Pausing...',
    '选择音频文件（可多选，批量转录）':
        'Choose audio files (multiple files are transcribed in a batch)',
    '音频文件 (*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma);;所有文件 (*.*)':
        'Audio files (*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma);;All files (*.*)',
    '识别中...':
        'Transcribing...',
    '正在准备...':
        'Preparing...',
    '识别完成':
        'Transcription complete',
    '保存识别结果':
        'Save transcript',
    '文本文件 (*.txt);;所有文件 (*.*)':
        'Text files (*.txt);;All files (*.*)',
    '选择要合并的MP3文件':
        'Choose MP3 files to merge',
    'MP3文件 (*.mp3);;所有文件 (*.*)':
        'MP3 files (*.mp3);;All files (*.*)',
    '保存合并后的MP3':
        'Save merged MP3',
    '合并_有声读物.mp3':
        'merged_audiobook.mp3',
    'MP3文件 (*.mp3)':
        'MP3 files (*.mp3)',
    '选择要合并为 m4b 的 MP3 文件（按文件名排序为章节顺序）':
        'Choose MP3 files for the m4b (chapter order follows file names)',
    '保存 m4b 有声书':
        'Save m4b audiobook',
    'M4B 有声书 (*.m4b)':
        'M4B audiobook (*.m4b)',
    '正在导出 m4b（重新编码为 AAC，可能需要几分钟）...':
        'Exporting m4b (re-encoding to AAC, this may take a few minutes)...',
    '外挂引擎':
        'Plugin engines',
    '请在便携存储目录的 engines/ 子目录中放置外挂引擎。\n详见 README.md 中的「外挂引擎插件」章节。':
        'Put plugin engines in the engines/ subfolder of the portable storage folder.\nSee “Plugin engines” in README.md for details.',
    '语音:':
        'Voice:',
    '按章节拆分':
        'One file per chapter',
    '按时间拆分':
        'Split by duration',
    '合并为一个文件':
        'Single file',
    '🔀 合并MP3':
        '🔀 Merge MP3s',
    '📚 导出 m4b':
        '📚 Export m4b',
    '📋 日志':
        '📋 Log',
    '旁白语音:':
        'Narration voice:',
    '对话语音:':
        'Dialogue voice:',
    '角色':
        'Character',
    '语音':
        'Voice',
    '提示':
        'Notice',
    '请先导入或输入文本':
        'Please import or enter some text first',
    '未检测到带名字的对话（形如「XX说：“…”」）':
        'No named dialogue found (e.g. “XX说：“…””)',
    '（使用对话语音）':
        '(use dialogue voice)',
    '⚙ 重新扫描':
        '⚙ Rescan',
    '📁 添加模型文件夹':
        '📁 Add model folder',
    '📁 添加程序文件夹':
        '📁 Add program folder',
    '📂 添加文件（可多选）':
        '📂 Add files (multiple)',
    '🗑 移除选中':
        '🗑 Remove selected',
    '文件名':
        'File name',
    '大小':
        'Size',
    '状态':
        'Status',
    '语速:':
        'Speed:',
    '章节':
        'Chapter',
    '说明':
        'Details',
    '重试':
        'Retry',
    '共 {active} 项':
        '{active} items',
    '完成 {done}':
        '{done} done',
    '生成队列 ({done}/{active})':
        'Generation Queue ({done}/{active})',
    '无法重试':
        "Can't retry",
    '找不到本次生成的进度文件（可能已被删除或目录已移动），请重新开始转换。':
        "The progress file for this run can't be found (it may have been deleted or the folder moved). Please start the conversion again.",
    '音频文件:':
        'Audio file:',
    '📂 选择音频文件':
        '📂 Choose audio files',
    'ASR 引擎:':
        'ASR engine:',
    'Whisper 模型:':
        'Whisper model:',
    '精度:':
        'Precision:',
    'tiny=最快 ~150MB | base=推荐 ~300MB | small ~1GB | medium ~3GB | large-v3=最准 ~6GB':
        'tiny = fastest ~150MB | base = recommended ~300MB | small ~1GB | medium ~3GB | large-v3 = most accurate ~6GB',
    '语言:':
        'Language:',
    '自动检测':
        'Auto-detect',
    '中文':
        'Chinese',
    '英文':
        'English',
    '日文':
        'Japanese',
    '韩文':
        'Korean',
    '法文':
        'French',
    '德文':
        'German',
    '西班牙文':
        'Spanish',
    '俄文':
        'Russian',
    '{code}（{name}）':
        '{code} ({name})',
    '输出格式:':
        'Output format:',
    '纯文本 (txt)':
        'Plain text (txt)',
    '字幕 (srt)':
        'Subtitles (srt)',
    '📋 复制结果':
        '📋 Copy result',
    '💾 保存到文件':
        '💾 Save to file',
    '联网 · 快速':
        'Online · fast',
    '离线 · 系统内置':
        'Offline · built into the OS',
    '离线高质量':
        'Offline · high quality',
    '离线神经网络':
        'Offline · neural',
    '复刻你的音色':
        'Clone your own voice',
    '正在生成中，引擎/语音切换将在下次生成时生效':
        'Generation in progress; engine / voice changes apply to the next run',
    '就绪':
        'Ready',
    '快 +{val}%':
        'Faster +{val}%',
    '慢 {val}%':
        'Slower {val}%',
    '已选 {checked}/{total} 章':
        '{checked}/{total} chapters selected',
    '未检测到章节':
        'No chapters detected',
    '将生成 1 个合并文件':
        'Will produce 1 merged file',
    '已加载 {n} 个文件':
        '{n} files loaded',
    '确认':
        'Confirm',
    '恢复为默认存储目录（用户主目录）？':
        'Reset to the default storage folder (in your home folder)?',
    '{mark} Piper Python 包: 已安装':
        '{mark} Piper Python package: installed',
    '{mark} Piper Python 包: 未安装':
        '{mark} Piper Python package: not installed',
    '模型总占用: {v1}（{v2} 个，详见「模型管理器」）':
        'Models on disk: {v1} ({v2} items; see “Model Manager”)',
    '缺少:':
        'Missing:',
    '下载地址:':
        'Downloads:',
    '名称':
        'Name',
    '位置':
        'Location',
    '更多多语言 Piper 语音包：':
        'More Piper voices in many languages:',
    '已安装 <b>{v1}</b> 个模型，共占用 <b>{v2}</b>\u3000·\u3000存储目录：{v3}':
        '<b>{v1}</b> models installed, using <b>{v2}</b> · Storage folder: {v3}',
    '已删除 {name}，释放 {v1}':
        'Deleted {name}, freed {v1}',
    '正在下载 {model_id}…（进度见主窗口底部）':
        'Downloading {model_id}… (progress at the bottom of the main window)',
    '时长':
        'Duration',
    '文字稿':
        'Transcript',
    '留空 = 当前解释器（{default_py}）':
        'Leave empty = current interpreter ({default_py})',
    '必填：CosyVoice 环境里的 python，如 …/envs/cosyvoice/bin/python':
        'Required: the python in your CosyVoice environment, e.g. …/envs/cosyvoice/bin/python',
    '留空 = 自动：{auto_md}':
        'Leave empty = auto: {auto_md}',
    '留空 = 自动在 cosyvoice-models/ 下查找（当前未找到）':
        'Leave empty = search cosyvoice-models/ automatically (nothing found yet)',
    '选择 Python 解释器':
        'Choose Python interpreter',
    '浏览…':
        'Browse…',
    '参考音频里说的话（逐字填写效果更好）：':
        'What is said in the reference audio (word for word works best):',
    '音色名称：':
        'Voice name:',
    '正在用「{name}」合成试听…':
        'Synthesizing a preview with “{name}”…',
    '正在检测：启动 CosyVoice 环境并加载模型，可能需要一两分钟…':
        'Checking: starting the CosyVoice environment and loading the model, this can take a minute or two…',
    '正在获取 {key} 的文件清单…（模型较大，可随时暂停，下次继续下载会续传）':
        'Fetching the file list for {key}… (large model; pause any time, the next download resumes)',
    '参考音频':
        'Reference audio',
    '文字稿（参考音频里说的话）':
        'Transcript (what is said in the reference audio)',
    '需要安装 faster-whisper（语音转文字）才能自动识别':
        'Auto-transcription needs faster-whisper (speech to text) installed',
    '选择参考音频':
        'Choose reference audio',
    '音频 (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.opus);;所有文件 (*)':
        'Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.opus);;All files (*)',
    '（已截取前 30 秒）':
        ' (trimmed to the first 30 s)',
    '{v1}\u3000{dur:.1f} 秒{trimmed}':
        '{v1}  {dur:.1f} s{trimmed}',
    '正在识别文字稿…（首次使用会下载 Whisper base 模型）':
        'Transcribing… (the Whisper base model is downloaded on first use)',
    '<h2>欢迎使用文字转有声读物</h2>':
        '<h2>Welcome to Audiobook Converter</h2>',
    '不可用：{why}':
        'Unavailable: {why}',
    '推荐 · ':
        'Recommended · ',
    '{title}\u3000（{tag}{extra}）':
        '{title}  ({tag}{extra})',
    '<h3>准备模型</h3>':
        '<h3>Get models</h3>',
    '<h3>试听验证</h3>':
        '<h3>Preview</h3>',
    '✗ 失败：{err}':
        '✗ Failed: {err}',
    '正在下载…':
        'Downloading…',
    '「{title}」需要下载以下模型（{size_hint}）。下载完成后点「下一步」试听。':
        '“{title}” needs the models below ({size_hint}). Click “Next” to preview once they finish downloading.',
    "<br><span style='color:#C62828'>引擎不可用：{why}</span>":
        "<br><span style='color:#C62828'>Engine unavailable: {why}</span>",
    '正在生成试听…（Piper 首次使用需要几秒加载模型）':
        'Generating preview… (Piper takes a few seconds to load the model the first time)',
    '中文 (zh)':
        'Chinese (zh)',
    '英文 (en)':
        'English (en)',
    '日文 (ja)':
        'Japanese (ja)',
    '韩文 (ko)':
        'Korean (ko)',
    '法文 (fr)':
        'French (fr)',
    '德文 (de)':
        'German (de)',
    '西班牙文 (es)':
        'Spanish (es)',
    '俄文 (ru)':
        'Russian (ru)',
    '全部语言':
        'All languages',
    '共 {v1} 个语音；下载后自动加入语音列表':
        '{v1} voices; downloaded voices are added to the voice list',
    '正在下载 {key}…（进度见主窗口底部）':
        'Downloading {key}… (progress at the bottom of the main window)',
    '已添加':
        'Added',
    '下载中 {desc}: {v1:.1f}/{v2:.1f} MB':
        'Downloading {desc}: {v1:.1f}/{v2:.1f} MB',
    '下载中 {desc}: {v1:.1f} MB':
        'Downloading {desc}: {v1:.1f} MB',
    '引擎不可用':
        'Engine unavailable',
    '请先输入或导入文字内容':
        'Please enter or import some text first',
    '⏸ 暂停播放':
        '⏸ Pause',
    '请至少勾选一个章节':
        'Please select at least one chapter',
    '该目录下没有找到进度文件':
        'No progress file found in this folder',
    '正在分析文本并拆分...':
        'Analyzing and splitting the text...',
    '准备继续转换...':
        'Preparing to resume...',
    '（已用 {mins} 分 {secs} 秒）':
        '({mins} min {secs} s elapsed)',
    '（已用 {secs} 秒）':
        '({secs} s elapsed)',
    '（用时 {mins} 分 {secs} 秒）':
        '(took {mins} min {secs} s)',
    '（用时 {secs} 秒）':
        '(took {secs} s)',
    '未生成文件，请查看日志了解详情':
        'No files were generated; see the log for details',
    '生成结果':
        'Result',
    '未能生成任何文件。\n\n可能原因：\n  · 网络不通（Edge 引擎需要访问 speech.platform.bing.com）\n  · 引擎未就绪\n  · 文本内容为空\n\n请查看日志文件了解详细错误。':
        "No files were generated.\n\nPossible causes:\n  · No network (the Edge engine needs speech.platform.bing.com)\n  · The engine isn't ready\n  · The text is empty\n\nSee the log file for detailed errors.",
    '完成 {count} 个，失败 {failed} 个（见「生成队列」）{elapsed}':
        '{count} done, {failed} failed (see “Generation Queue”) {elapsed}',
    '完成':
        'Done',
    '已生成 {count} 个MP3文件 {elapsed}:\n{names}{tail}{fail_note}\n\n是否打开文件夹？':
        'Generated {count} MP3 files {elapsed}:\n{names}{tail}{fail_note}\n\nOpen the folder?',
    '当前: {theme} 模式':
        'Current: {theme} theme',
    '已选 {v1} 个文件（共 {total_mb:.1f} MB，批量转录结果自动存到各文件旁）':
        '{v1} files selected ({total_mb:.1f} MB in total; batch results are saved next to each file)',
    '请先选择音频文件':
        'Please choose an audio file first',
    '外挂 ASR 引擎识别中...':
        'Transcribing with plugin ASR engine...',
    'ASR 不可用':
        'ASR unavailable',
    '正在转录 [{idx}/{total}]: {filename}':
        'Transcribing [{idx}/{total}]: {filename}',
    '没有可保存的内容':
        'Nothing to save',
    '请选择至少2个文件':
        'Please choose at least 2 files',
    '已合并 {v1} 个文件:\n{v2}\n大小: {size_mb:.1f}MB':
        'Merged {v1} files:\n{v2}\nSize: {size_mb:.1f}MB',
    '日志文件不存在':
        'Log file not found',
    '保存失败：{e}':
        'Save failed: {e}',
    '生成中 {v1}':
        '{v1} generating',
    '等待 {v1}':
        '{v1} waiting',
    "<span style='color:#C62828'>失败 {failed}</span>":
        "<span style='color:#C62828'>{failed} failed</span>",
    '未勾选 {v1}':
        '{v1} not selected',
    ' · 失败 {failed}':
        ' · {failed} failed',
    '\n（需安装）':
        '\n(not installed)',
    '⚡ {name}\n外挂引擎':
        '⚡ {name}\nPlugin engine',
    '将生成 {v1} 个文件（每章一个）':
        'Will produce {v1} files (one per chapter)',
    '{v1} 字':
        '{v1} chars',
    '已加载':
        'Loaded',
    '错误':
        'Error',
    '目录':
        'Folder',
    '扫描失败: {e}':
        'Scan failed: {e}',
    '○ CosyVoice: 检测失败':
        '○ CosyVoice: check failed',
    '✓ 已安装 · ':
        '✓ Installed · ',
    '正在生成':
        'Generating',
    '生成进行中，暂不能删除模型。请先停止或等待完成。':
        "Generation is in progress; models can't be deleted right now. Stop it or wait for it to finish.",
    '删除模型':
        'Delete model',
    '删除「{name}」？\n将释放 {v1}，删除后需重新下载才能使用。':
        "Delete “{name}”?\nThis frees {v1}; you'll need to download it again to use it.",
    '{model_id} 下载完成':
        '{model_id} downloaded',
    '下载失败: {err}':
        'Download failed: {err}',
    'Python 解释器':
        'Python interpreter',
    'CosyVoice 仓库':
        'CosyVoice repository',
    '模型目录':
        'Model folder',
    '已创建音色「{name}」。':
        'Created voice “{name}”.',
    '删除音色':
        'Delete voice',
    '删除克隆音色「{name}」？':
        'Delete cloned voice “{name}”?',
    '✗ 试听失败：{e}':
        '✗ Preview failed: {e}',
    'Python 解释器：{v1}':
        'Python interpreter: {v1}',
    'CosyVoice 仓库：{v1}':
        'CosyVoice repository: {v1}',
    '检测失败：{e}':
        'Check failed: {e}',
    '正在暂停…':
        'Pausing…',
    '✗ 太短（{dur:.1f} 秒），至少需要 {MIN_SECONDS:.0f} 秒。':
        '✗ Too short ({dur:.1f} s); at least {MIN_SECONDS:.0f} s is needed.',
    '✗ 识别失败：{e}':
        '✗ Transcription failed: {e}',
    '需下载 {size_hint}':
        '{size_hint} to download',
    '完成 ✓':
        'Finish ✓',
    '{v1}\u3000—\u3000{status}':
        '{v1}  —  {status}',
    '{desc}：{v1:.1f} / {v2:.1f} MB':
        '{desc}: {v1:.1f} / {v2:.1f} MB',
    '有 {failed} 项下载失败。可以重试，也可以先继续（之后在「模型管理器」里也能下载）。':
        '{failed} downloads failed. You can retry, or continue for now (you can also download later in “Model Manager”).',
    '✓ 全部就绪':
        '✓ All set',
    '等待':
        'Waiting',
    '「{title}」所需模型已全部就绪。':
        'All models for “{title}” are ready.',
    '{lab}\u3000—\u3000等待':
        '{lab}  —  waiting',
    '✗ 该引擎没有可用语音。':
        '✗ This engine has no voices available.',
    ', {v1} 说话人':
        ', {v1} speakers',
    '请先选中一个语音':
        'Please select a voice first',
    '{key} 下载完成，已加入语音列表':
        '{key} downloaded and added to the voice list',
    '已添加: {path}':
        'Added: {path}',
    '已存在: {path}':
        'Already added: {path}',
    '⚡ {v1}（外挂）':
        '⚡ {v1} (plugin)',
    '{v1}识别进度: {c}/{t}':
        '{v1}Progress: {c}/{t}',
    '合并失败: {e}':
        'Merge failed: {e}',
    'm4b 导出完成: {v1}（{v2} 章, {size_mb:.1f}MB）':
        'm4b exported: {v1} ({v2} chapters, {size_mb:.1f}MB)',
    '（已停止，完成 {v1}/{v2}）':
        '(stopped, {v1}/{v2} done)',
    '批量转录完成:\n':
        'Batch transcription complete:\n',
    '将生成约 {count} 个文件（每段 ≈ {mins} 分钟）':
        'Will produce about {count} files (≈ {mins} min each)',
    '未找到':
        'not found',
    '{kind}（{n}）':
        '{kind} ({n})',
    '删除失败':
        'Delete failed',
    '{duration:.1f} 秒':
        '{duration:.1f} s',
    '（无文字稿 · 效果会差一些）':
        '(no transcript · results will be weaker)',
    '✓ 正在播放「{name}」的试听。':
        '✓ Playing the preview of “{name}”.',
    '模型目录：{v1}':
        'Model folder: {v1}',
    '模型加载：{v1}，采样率 {v2} ✓':
        'Model loaded: {v1}, sample rate {v2} ✓',
    '\n错误：{v1}':
        '\nError: {v1}',
    '\n✓ 环境可用':
        '\n✓ Environment ready',
    '\n✗ 环境未就绪，请按上面的提示检查':
        '\n✗ Environment not ready; check the hints above',
    '留空 = 自动：{md}':
        'Leave empty = auto: {md}',
    '{desc}\u3000{v1:.1f} / {v2:.1f} MB':
        '{desc}  {v1:.1f} / {v2:.1f} MB',
    '✗ 下载失败：{err}':
        '✗ Download failed: {err}',
    '✓ 音频可用。':
        '✓ Audio is usable.',
    '✓ 已识别，请核对后再创建（错字会影响效果）。':
        '✓ Transcribed; please check it before creating (typos hurt the result).',
    '没有识别到文字，请手动填写。':
        'No speech recognized; please type the transcript.',
    '模型已就绪':
        'Models ready',
    '⬇ 下载中…':
        '⬇ Downloading…',
    '✓ 完成':
        '✓ Done',
    '✓ 正在播放。听到声音就说明一切就绪。':
        "✓ Playing. If you hear the sample, you're all set.",
    '目录获取失败: {e}':
        'Failed to fetch catalog: {e}',
    'ASR 识别失败: {e}':
        'Transcription failed: {e}',
    'm4b 导出失败: {e}':
        'm4b export failed: {e}',
    '读取文件失败: {e}':
        'Failed to read file: {e}',
    '{name}  [外部·只读]':
        '{name}  [external · read-only]',
    '来自「添加模型文件夹」的外部目录，为安全起见不在此删除':
        'From an external folder added via “Add model folder”; not deleted here for safety',
    '（未安装）':
        '(not installed)',
    '选择 CosyVoice 仓库目录':
        'Choose CosyVoice repository folder',
    '选择 CosyVoice 模型目录':
        'Choose CosyVoice model folder',
    '试听已生成，但播放失败：{e}':
        'Preview generated, but playback failed: {e}',
    '桥接进程：Python {v1}，cosyvoice ':
        'Bridge process: Python {v1}, cosyvoice ',
    '✓ {key} 下载完成。':
        '✓ {key} downloaded.',
    '建议填写或自动识别文字稿。':
        'Consider entering or auto-transcribing the transcript.',
    '下载失败: {e}':
        'Download failed: {e}',
    '▶ 继续播放':
        '▶ Resume playback',
    '外挂 ASR 识别失败: {e}':
        'Plugin ASR transcription failed: {e}',
    '（未配置）':
        '(not configured)',
    '可导入 ✓':
        'importable ✓',
    '无法导入 ✗\n  {v1}':
        'not importable ✗\n  {v1}',
    '（未找到）':
        '(not found)',
    # ---- main.py ----
    '文字转有声读物':
        'Audiobook Converter',
    '文字转有声读物 v{version}':
        'Audiobook Converter v{version}',
    # ---- tts_engine.py ----
    '暂不支持的平台：{_PLATFORM}':
        'Unsupported platform: {_PLATFORM}',
    'CosyVoice 需要单独安装（依赖 torch）：\n1. 按 README「声音克隆」一节安装 CosyVoice，并在「🎙 克隆音色… → 环境设置」里填写其 Python 与仓库路径；\n2. 在「模型管理」下载 CosyVoice-300M-SFT 模型；\n3. 或将 CosyVoice 可执行文件放入 engines/cosyvoice/ 作为外挂引擎':
        'CosyVoice is installed separately (requires torch):\n1. Install CosyVoice following the “Voice cloning” section of the README, then enter its Python and repository paths under “🎙 Clone a voice… → Environment”;\n2. Download the CosyVoice-300M-SFT model in the Model Manager;\n3. Or put a CosyVoice executable in engines/cosyvoice/ as a plugin engine',
    'Linux: sudo apt install ffmpeg（或对应发行版包管理器）':
        "Linux: sudo apt install ffmpeg (or your distribution's package manager)",
    '{v1} 可用（{v2} 个语音）':
        '{v1} available ({v2} voices)',
    'Windows: 从 https://www.gyan.dev/ffmpeg/builds/ 下载 ffmpeg.exe，放入便携 bin 目录或系统 PATH':
        'Windows: download ffmpeg.exe from https://www.gyan.dev/ffmpeg/builds/ and put it in the portable bin folder or on PATH',
    '未知引擎: {engine}':
        'Unknown engine: {engine}',
    '所有下载源均失败: {last_exception}':
        'All download sources failed: {last_exception}',
    'ffmpeg（Piper/本地语音 MP3 转换依赖）':
        'ffmpeg (needed to make MP3s for Piper / system voices)',
    'Piper 可执行文件（pip 未装 piper-tts 时需外置 piper）':
        "Piper executable (needed when the piper-tts pip package isn't installed)",
    'Piper 语音包（.onnx 模型）':
        'Piper voices (.onnx models)',
    "外部引擎 '{engine_id}' 未安装。\n请将引擎可执行文件放入便携存储目录的 engines/{engine_id}/ 中。":
        "Plugin engine '{engine_id}' is not installed.\nPut its executable in engines/{engine_id}/ inside the portable storage folder.",
    '引擎可执行文件不存在: {executable}':
        'Engine executable not found: {executable}',
    "外部引擎 '{engine_id}' 失败 (code {rc}): {stderr}":
        "Plugin engine '{engine_id}' failed (code {rc}): {stderr}",
    "外部引擎 '{engine_id}' 生成文件为空: {output_path}":
        "Plugin engine '{engine_id}' produced an empty file: {output_path}",
    '{voice_name}（{lang}）':
        '{voice_name} ({lang})',
    'macOS 未检测到中文语音。\n请在 系统设置 → 辅助功能 → 语音内容 → 系统语音 中下载中文语音包。':
        'No Chinese voices found on macOS.\nDownload one in System Settings → Accessibility → Spoken Content → System Voice.',
    '未找到 powershell，无法调用 Windows 系统语音。':
        "PowerShell not found; can't use Windows system voices.",
    'Windows 未检测到中文语音。\n请在 设置 → 时间和语言 → 语言 → 添加中文 → 安装语音包；\n或 设置 → 辅助功能 → 讲述人 → 添加自然语音 中下载中文语音。':
        'No Chinese voices found on Windows.\nInstall one in Settings → Time & Language → Language → Add Chinese → Speech;\nor download a natural Chinese voice in Settings → Accessibility → Narrator → Add natural voices.',
    '未检测到 espeak-ng。请安装：\n  Debian/Ubuntu: sudo apt install espeak-ng\n  Fedora:        sudo dnf install espeak-ng\n  Arch:          sudo pacman -S espeak-ng':
        'espeak-ng not found. Install it with:\n  Debian/Ubuntu: sudo apt install espeak-ng\n  Fedora:        sudo dnf install espeak-ng\n  Arch:          sudo pacman -S espeak-ng',
    '不支持的 CosyVoice 模型: {model_key}（可选：{v1}）':
        'Unsupported CosyVoice model: {model_key} (choices: {v1})',
    'Edge TTS 可用（微软在线语音，需要联网）':
        'Edge TTS available (Microsoft online voices, needs internet)',
    '未检测到中文系统语音':
        'No Chinese system voices found',
    '本地语音不可用：\n{reason}':
        'System voices unavailable:\n{reason}',
    'Piper 可用（首次使用将自动下载语音模型）':
        'Piper available (voice models download automatically on first use)',
    "外部引擎 '{engine}' 不可用":
        "Plugin engine '{engine}' is unavailable",
    '没有可预览的文本':
        'No text to preview',
    'Edge TTS 未安装。\n请运行: pip install edge-tts':
        'Edge TTS is not installed.\nRun: pip install edge-tts',
    '{plat_name} 系统语音可用（{v1} 个中文语音）':
        '{plat_name} system voices available ({v1} Chinese voices)',
    'CosyVoice 外挂引擎可用（{v1}）':
        'CosyVoice plugin engine available ({v1})',
    '用户暂停':
        'Paused by user',
    'ffmpeg 不可用，无法转换为 MP3。\n':
        "ffmpeg is unavailable; can't convert to MP3.\n",
    '不支持的引擎类型: {engine}':
        'Unsupported engine type: {engine}',
    '全部 {error_count} 个文件生成失败，请检查后重试。\n\n{detail}':
        'All {error_count} files failed to generate. Please check and try again.\n\n{detail}',
    '{voice_name}（中文）':
        '{voice_name} (Chinese)',
    '女声':
        'female',
    '{name}（{culture}，{gender}）':
        '{name} ({culture}, {gender})',
    '{name}（{culture}）':
        '{name} ({culture})',
    'Piper 未安装。\n':
        'Piper is not installed.\n',
    'ffmpeg 未安装（Piper 需要 ffmpeg 转换音频）。\n':
        'ffmpeg is not installed (Piper needs it to convert audio).\n',
    '生成文件为空: {output_path}':
        'Generated file is empty: {output_path}',
    '男声':
        'male',
    '{file_prefix}_第{file_idx}部分':
        '{file_prefix}_part{file_idx}',
    '响度归一化':
        'Loudness normalization',
    # ---- audiobook/engines/piper.py ----
    '方式 1: pip install piper-tts\n方式 2: 从 https://github.com/rhasspy/piper/releases 下载 piper 可执行文件，\n        放入便携 bin 目录或系统 PATH':
        'Option 1: pip install piper-tts\nOption 2: download the piper executable from https://github.com/rhasspy/piper/releases\n          and put it in the portable bin folder or on PATH',
    'Piper 语音目录':
        'Piper voice catalog',
    'Piper模型 {voice_name}':
        'Piper model {voice_name}',
    'Piper配置 {voice_name}':
        'Piper config {voice_name}',
    'Piper API 不兼容：未找到 synthesize 或 synthesize_stream_raw 方法。\n请升级或重装 piper-tts。':
        'Incompatible Piper API: neither synthesize nor synthesize_stream_raw was found.\nPlease upgrade or reinstall piper-tts.',
    '语音目录中没有: {voice_key}':
        'Not in the voice catalog: {voice_key}',
    '语音 {voice_key} 的目录条目中没有 .onnx 模型文件':
        'The catalog entry for {voice_key} has no .onnx model file',
    'Piper TTS 不可用。\n':
        'Piper TTS is unavailable.\n',
    '不支持的Piper语音: {voice_name}':
        'Unsupported Piper voice: {voice_name}',
    'Piper CLI失败 (code {rc}): {stderr}':
        'Piper CLI failed (code {rc}): {stderr}',
    'Piper配置文件不存在: {config_path}':
        'Piper config file not found: {config_path}',
    'Piper CLI 不可用。\n':
        'Piper CLI is unavailable.\n',
    'ffmpeg 未安装，Piper 引擎需要 ffmpeg 进行 WAV→MP3 转换。\n':
        'ffmpeg is not installed; the Piper engine needs it to convert WAV to MP3.\n',
    # ---- audiobook/engines/edge.py ----
    'Edge TTS 未安装。请运行: pip install edge-tts':
        'Edge TTS is not installed. Run: pip install edge-tts',
    'Edge TTS 未安装。请运行: pip install edge-tts，或切换到离线引擎（系统语音 / Piper / CosyVoice）。':
        'Edge TTS is not installed. Run: pip install edge-tts, or switch to an offline engine (System / Piper / CosyVoice).',
    '生成文件为空: {out_path}':
        'Generated file is empty: {out_path}',
    # ---- audiobook/engines/local.py ----
    '未找到 PowerShell，无法调用 Windows 系统语音':
        "PowerShell not found; can't use Windows system voices",
    '未找到 espeak-ng，请先安装（sudo apt install espeak-ng）':
        'espeak-ng not found; please install it (sudo apt install espeak-ng)',
    'say 失败: {v1}':
        'say failed: {v1}',
    'afconvert 失败: {v1}':
        'afconvert failed: {v1}',
    'Windows SAPI 合成失败: {v1}':
        'Windows SAPI synthesis failed: {v1}',
    'espeak 失败: {v1}':
        'espeak failed: {v1}',
    '暂不支持的平台: {PLATFORM}':
        'Unsupported platform: {PLATFORM}',
    # ---- audiobook/engines/cosyvoice.py ----
    'CosyVoice 不可用：{msg}':
        'CosyVoice unavailable: {msg}',
    # ---- audiobook/engines/clone.py ----
    '克隆音色不存在或已删除：{voice}':
        'Cloned voice not found or deleted: {voice}',
    # ---- audiobook/engines/asr.py ----
    'ASR 引擎就绪':
        'ASR engine ready',
    'faster-whisper 未安装。\n请运行: pip install faster-whisper\n（需要约 800MB 磁盘空间，首次运行自动下载模型）':
        'faster-whisper is not installed.\nRun: pip install faster-whisper\n(needs about 800MB of disk space; models download on first run)',
    'ffmpeg 未安装，ASR 需要 ffmpeg 转换音频格式。':
        'ffmpeg is not installed; ASR needs it to convert audio.',
    'ffmpeg 不可用，无法转换音频格式。':
        "ffmpeg is unavailable; can't convert audio.",
    'faster-whisper 未安装':
        'faster-whisper is not installed',
    "外挂 ASR 引擎 '{engine_id}' 未找到。请放入 asr-engines/{engine_id}/ 目录。":
        "Plugin ASR engine '{engine_id}' not found. Put it in asr-engines/{engine_id}/.",
    "外挂 ASR 引擎 '{engine_id}' 未生成输出内容":
        "Plugin ASR engine '{engine_id}' produced no output",
    "外挂 ASR 引擎 '{engine_id}' 失败 (code {rc}): {stderr}":
        "Plugin ASR engine '{engine_id}' failed (code {rc}): {stderr}",
    # ---- audiobook/io/audio.py ----
    '没有要合并的文件':
        'No files to merge',
    'loudnorm 失败 (rc={rc})':
        'loudnorm failed (rc={rc})',
    'ffmpeg 未安装，无法导出 m4b。\n':
        "ffmpeg is not installed; can't export m4b.\n",
    'm4b 导出失败 (rc={rc}): {v1}':
        'm4b export failed (rc={rc}): {v1}',
    'ffmpeg 未安装，无法写入元数据。\n':
        "ffmpeg is not installed; can't write metadata.\n",
    '写入元数据失败 (rc={rc}): {v1}':
        'Failed to write metadata (rc={rc}): {v1}',
    'ffmpeg 未安装，无法做响度归一化。\n':
        "ffmpeg is not installed; can't normalize loudness.\n",
    # ---- audiobook/io/readers.py ----
    '无法解码 Markdown 文件':
        "Can't decode the Markdown file",
    'ebooklib 未安装 (pip install ebooklib)':
        'ebooklib is not installed (pip install ebooklib)',
    '需要安装 PyMuPDF 或 pdfplumber 来读取 PDF (pip install PyMuPDF)':
        'Reading PDFs needs PyMuPDF or pdfplumber (pip install PyMuPDF)',
    '无法读取文件，编码不支持':
        "Can't read the file: unsupported encoding",
    # ---- audiobook/io/hub.py ----
    '仓库文件路径不安全，已拒绝：{path}':
        'Unsafe file path in repository, rejected: {path}',
    '{source}: 文件清单为空':
        '{source}: empty file list',
    '无法从任何下载源获取模型文件清单（请检查网络）：\n':
        "Couldn't get the model file list from any download source (check your network):\n",
    '磁盘空间不足：需要约 {v1:.1f} GB，可用 {v2:.1f} GB（{probe}）':
        'Not enough disk space: about {v1:.1f} GB needed, {v2:.1f} GB free ({probe})',
    'ModelScope 返回错误：{v1}':
        'ModelScope returned an error: {v1}',
    '下载失败：{path}\n':
        'Download failed: {path}\n',
    '文件大小不符（应为 {size}，实得 {got}）':
        'File size mismatch (expected {size}, got {got})',
    # ---- audiobook/models.py ----
    '{v1}（未下载完整，再次下载可续传）':
        '{v1} (incomplete; download again to resume)',
    '（未下载完整）':
        ' (incomplete)',
    '「{name}」不在便携存储目录内，为安全起见不在此删除：{location}':
        "“{name}” is outside the portable storage folder and isn't deleted here for safety: {location}",
    'Whisper {size}（HF 缓存）':
        'Whisper {size} (HF cache)',
    '未知的模型类型: {kind}':
        'Unknown model type: {kind}',
    '未知的 Whisper 模型规格: {model_id}':
        'Unknown Whisper model size: {model_id}',
    # ---- audiobook/cloning/client.py ----
    '尚未配置 CosyVoice 的 Python 解释器。\n请在「🎙 克隆音色… → 环境设置」里指定装好 CosyVoice 的 python（conda / venv 中的 python）。':
        "The CosyVoice Python interpreter isn't configured yet.\nSet the python that has CosyVoice installed (in conda / venv) under “🎙 Clone a voice… → Environment”.",
    '配置的 Python 解释器不存在：{py}':
        'Configured Python interpreter not found: {py}',
    '声音克隆可用（{v1}，{n} 个音色）':
        'Voice cloning available ({v1}, {n} voices)',
    'CosyVoice 可用（预置说话人，{v1}）':
        'CosyVoice available (preset speakers, {v1})',
    '未找到可用于克隆的 CosyVoice 模型（Fun-CosyVoice3-0.5B / CosyVoice2-0.5B）。\n可在「声音克隆 → 环境设置」点「下载」获取（或命令行 audiobook models get cosyvoice Fun-CosyVoice3-0.5B），也可自行放到存储目录的 cosyvoice-models/ 下，或在设置里指定模型目录。':
        'No CosyVoice model for cloning found (Fun-CosyVoice3-0.5B / CosyVoice2-0.5B).\nClick “Download” under “Voice Cloning → Environment” (or run audiobook models get cosyvoice Fun-CosyVoice3-0.5B), put one in cosyvoice-models/ in the storage folder, or set the model folder in the settings.',
    '还没有克隆音色：先用一段 5–15 秒的清晰人声创建一个。':
        'No cloned voices yet: create one from 5–15 seconds of clear speech.',
    '未找到 CosyVoice-300M-SFT 模型（预置中文男女声等）。\n可在「模型管理」下载，或命令行 audiobook models get cosyvoice CosyVoice-300M-SFT。':
        'CosyVoice-300M-SFT model not found (preset Chinese male/female voices and more).\nDownload it in the Model Manager, or run audiobook models get cosyvoice CosyVoice-300M-SFT.',
    '找不到声音克隆桥接脚本：{WORKER}（安装包不完整？）':
        'Voice cloning bridge script not found: {WORKER} (incomplete installation?)',
    '无法启动 CosyVoice 桥接进程（{python}）：{e}':
        "Can't start the CosyVoice bridge process ({python}): {e}",
    'CosyVoice 响应超时（{cmd}，{v1} 秒）':
        'CosyVoice timed out ({cmd}, {v1} s)',
    'CosyVoice 桥接进程已退出。':
        'The CosyVoice bridge process has exited.',
    'CosyVoice 桥接进程意外退出。':
        'The CosyVoice bridge process exited unexpectedly.',
    'CosyVoice 处理失败':
        'CosyVoice failed',
    '\n日志：\n{tail}':
        '\nLog:\n{tail}',
    # ---- audiobook/cloning/profiles.py ----
    '无效的音色 ID: {pid!r}':
        'Invalid voice ID: {pid!r}',
    '{name}（{v1}）':
        '{name} ({v1})',
    '无法读取参考音频（格式不支持或文件损坏）：{v1}':
        "Can't read the reference audio (unsupported format or damaged file): {v1}",
    '参考音频不存在: {audio_path}':
        'Reference audio not found: {audio_path}',
    '请给音色起个名字':
        'Please give the voice a name',
    '音色不存在: {pid}':
        'Voice not found: {pid}',
    '需要 ffmpeg 才能处理参考音频。\n':
        'ffmpeg is needed to process the reference audio.\n',
    '参考音频太短（{duration:.1f} 秒），至少需要 {MIN_SECONDS:.0f} 秒，建议 5–15 秒的清晰人声。':
        'Reference audio is too short ({duration:.1f} s); at least {MIN_SECONDS:.0f} s is needed, 5–15 s of clear speech is recommended.',
    '名字不能为空':
        "The name can't be empty",
    # ---- （显示处翻译的模块级数据） ----
    'Piper 语音包':
        'Piper voices',
    'CosyVoice 模型':
        'CosyVoice models',
    'Whisper 模型':
        'Whisper models',
    '联网 · 音质最好':
        'Online · best quality',
    '使用微软 Edge 在线语音，自然度最高，无需下载模型；生成时需要能访问互联网。':
        'Uses Microsoft Edge online voices: the most natural, no model download; needs internet access while generating.',
    '无需下载':
        'No download',
    '完全离线':
        'Fully offline',
    '下载 Piper 中文神经语音，断网也能生成，音质较好。':
        'Downloads a Piper Chinese neural voice: works without internet, good quality.',
    '约 60 MB':
        'about 60 MB',
    '离线 + 语音转文字':
        'Offline + speech to text',
    '在「完全离线」基础上再下载 Whisper base 识别模型，可把音频转回文字 / 字幕。':
        'Everything in “Fully offline” plus the Whisper base model, so you can turn audio back into text / subtitles.',
    '约 360 MB':
        'about 360 MB',
    '系统自带语音（零下载）':
        'Built-in system voices (no download)',
    '直接使用操作系统自带的语音（macOS / Windows 内置，Linux 需 espeak-ng），音质一般。':
        'Uses the voices built into your OS (macOS / Windows; Linux needs espeak-ng). Average quality.',
    'auto（推荐）':
        'auto (recommended)',
    'float16（GPU）':
        'float16 (GPU)',
    'int8_float16（GPU 省显存）':
        'int8_float16 (GPU, less memory)',
    'int8（CPU）':
        'int8 (CPU)',
    'tiny（最快，~150MB）':
        'tiny (fastest, ~150MB)',
    'base（推荐，~300MB）':
        'base (recommended, ~300MB)',
    'small（~1GB）':
        'small (~1GB)',
    'medium（~3GB）':
        'medium (~3GB)',
    'large-v3（最准，~6GB）':
        'large-v3 (most accurate, ~6GB)',
    'Fun-CosyVoice3-0.5B（声音克隆推荐，效果最好；数 GB）':
        'Fun-CosyVoice3-0.5B (recommended for cloning, best quality; several GB)',
    'CosyVoice2-0.5B（声音克隆；数 GB）':
        'CosyVoice2-0.5B (for cloning; several GB)',
    'CosyVoice-300M-SFT（预置中文男女声等，供「CosyVoice」引擎）':
        'CosyVoice-300M-SFT (preset Chinese male/female voices etc., for the “CosyVoice” engine)',
    'Piper中文女声（中等质量）':
        'Piper Chinese female (medium quality)',
    'Piper中文女声（较低质量）':
        'Piper Chinese female (lower quality)',
    'hf-mirror 镜像':
        'hf-mirror (mirror)',
    'ModelScope 魔搭':
        'ModelScope',
    '晓晓（女声，自然）':
        'Xiaoxiao (female, natural)',
    '云希（男声，自然）':
        'Yunxi (male, natural)',
    '云健（男声，播音）':
        'Yunjian (male, broadcaster)',
    '晓伊（女声，活泼）':
        'Xiaoyi (female, lively)',
    '云扬（男声，温暖）':
        'Yunyang (male, warm)',
    '晓辰（女声，温柔）':
        'Xiaochen (female, gentle)',
    '中文女声':
        'Chinese female',
    '中文男声':
        'Chinese male',
    '粤语女声':
        'Cantonese female',
    '英文女声':
        'English female',
    '英文男声':
        'English male',
    '日语男声':
        'Japanese male',
    '韩语女声':
        'Korean female',
    'espeak-ng 中文':
        'espeak-ng Chinese',
}
