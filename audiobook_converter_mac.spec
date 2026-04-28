# -*- mode: python ; coding: utf-8 -*-
# macOS .app bundle spec - v3.1.1: 体检稳定版（ID3 修正 + 子进程隐藏 + ASR atexit + 友好错误）

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
    # 深色主题
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
    runtime_hooks=[],
    excludes=[],
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
        'CFBundleVersion': '3.1.1',
        'CFBundleShortVersionString': '3.1.1',
        'NSHumanReadableCopyright': 'AudiobookConverter',
        'NSHighResolutionCapable': True,
    },
)
