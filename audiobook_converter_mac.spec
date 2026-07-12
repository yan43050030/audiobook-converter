# -*- mode: python ; coding: utf-8 -*-
# macOS .app bundle spec - v5.2.0: 包体积瘦身（PySide6 按需打包，不再全量收集）

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

block_cipher = None

# --- Collect Piper resources ---
piper_datas = collect_data_files('piper')
piper_hidden = collect_submodules('piper')

# --- Collect Onnxruntime resources ---
onnx_bins = collect_dynamic_libs('onnxruntime')
onnx_datas = collect_data_files('onnxruntime')
onnx_hidden = collect_submodules('onnxruntime')

# --- pygame 内置播放器（含 SDL 原生库）---
pygame_datas, pygame_hidden, pygame_bins = [], [], []
try:
    import pygame  # noqa: F401
    pygame_datas = collect_data_files('pygame')
    pygame_hidden = collect_submodules('pygame')
    pygame_bins = collect_dynamic_libs('pygame')
except Exception:
    pass

# --- 文档读取扩展（可选，按存在性收集）---
def _collect_optional_module(mod_name: str):
    try:
        __import__(mod_name)
    except Exception:
        return [], []
    try:
        return collect_data_files(mod_name), collect_submodules(mod_name)
    except Exception:
        return [], []

_doc_datas, _doc_hidden = [], []
for _mn in ("docx", "ebooklib", "fitz", "pdfplumber"):
    d, h = _collect_optional_module(_mn)
    _doc_datas += d
    _doc_hidden += h

# --- Manual binary files ---
manual_bins = []
try:
    import piper
    piper_dir = os.path.dirname(piper.__file__)
    manual_bins = [
        # Piper's espeakbridge native library (not picked up by collect_dynamic_libs)
        (os.path.join(piper_dir, 'espeakbridge.so'), 'piper'),
    ]
except ImportError:
    pass

# audioop's _audioop.abi3.so (Python 3.13+ compat)
try:
    import audioop
    audioop_dir = os.path.dirname(audioop.__file__)
    audioop_so = os.path.join(audioop_dir, '_audioop.abi3.so')
    if os.path.exists(audioop_so):
        manual_bins.append((audioop_so, 'audioop'))
except ImportError:
    pass

# --- PySide6：只打包实际用到的 QtWidgets/QtCore/QtGui（经 hiddenimports 声明，
# 由 PyInstaller 的 PySide6 钩子自动收集对应框架和平台插件）。
# 切勿使用 collect_data_files/collect_submodules('PySide6') 全量收集：
# 那会把 QtWebEngine（内嵌完整 Chromium）、QtQuick/QML、Qt3D、QtCharts、
# QtMultimedia 等全部打进 .app，包体积会膨胀到 1.4GB+（v5.1.0 实测）。

# --- Combine all resources ---
base_datas = [('icon.png', '.'), ('icon.ico', '.'), ('icon.icns', '.')]
all_datas = base_datas + piper_datas + onnx_datas + pygame_datas + _doc_datas

all_bins = manual_bins + onnx_bins + pygame_bins

base_hidden = [
    # 在线引擎
    'edge_tts', 'aiohttp', 'aiosignal', 'frozenlist', 'multidict', 'yarl',
    'propcache', 'attr', 'attrs', 'certifi', 'charset_normalizer', 'idna', 'urllib3',
    # 通用网络 / 进度
    'requests', 'tqdm', 'tabulate',
    # 音频
    'pydub', 'wave', 'audioop',
    # Piper 生态
    'onnxruntime', 'piper',
    # 内置播放器
    'pygame',
]
all_hidden = base_hidden + piper_hidden + onnx_hidden + pygame_hidden + _doc_hidden + [
    # ASR 语音转文字
    'faster_whisper', 'ctranslate2',
    # Qt6 GUI（PySide6 推荐 / PyQt6 回退）
    'PySide6', 'PySide6.QtWidgets', 'PySide6.QtCore', 'PySide6.QtGui',
    'shiboken6',
    # 深色主题（Tkinter 回退用）
    'sv_ttk',
    # 电子书读取
    'docx', 'ebooklib', 'fitz', 'pdfplumber',
    # GPU 检测
    'torch',
    # CosyVoice（可选）
    'cosyvoice', 'soundfile', 'librosa',
]

# --- Build Analysis ---
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=all_bins,
    datas=all_datas,
    hiddenimports=all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(SPECPATH, 'runtime_hook.py')],
    excludes=[
        'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.sip',
        # 排除未用到的重型 Qt 模块（防止依赖分析间接引入）
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick',
        'PySide6.QtWebChannel', 'PySide6.QtWebSockets',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQuickWidgets',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DAnimation',
        'PySide6.Qt3DExtras', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtLocation', 'PySide6.QtPositioning', 'PySide6.QtSensors',
        'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
        'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtStateMachine',
        'PySide6.QtTextToSpeech', 'PySide6.QtDesigner', 'PySide6.QtUiTools',
        'PySide6.QtHelp', 'PySide6.QtSql', 'PySide6.QtTest',
        'PySide6.QtNetworkAuth', 'PySide6.QtHttpServer',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='audiobook_converter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.icns',
)

app = BUNDLE(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='AudiobookConverter.app',
    icon='icon.icns',
    bundle_identifier='com.audiobookconverter.app',
    info_plist={
        'CFBundleName': 'AudiobookConverter',
        'CFBundleDisplayName': '文字转有声读物',
        'CFBundleVersion': '5.2.0',
        'CFBundleShortVersionString': '5.2.0',
        'NSHumanReadableCopyright': 'AudiobookConverter',
        'NSHighResolutionCapable': True,
    },
)
