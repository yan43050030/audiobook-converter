# -*- mode: python ; coding: utf-8 -*-
# Windows exe spec - v2.6.0: 内置播放器 + 全文试听 + 可拖拽分隔条

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

block_cipher = None

# --- Optional: bundle Piper + onnxruntime if available ---
piper_datas, piper_hidden, piper_manual_bins = [], [], []
onnx_bins, onnx_datas, onnx_hidden = [], [], []

try:
    import piper
    piper_datas = collect_data_files('piper')
    piper_hidden = collect_submodules('piper')
    piper_dir = os.path.dirname(piper.__file__)
    # espeakbridge native lib (.pyd on Windows, .so on mac/linux)
    for name in ('espeakbridge.pyd', 'espeakbridge.so'):
        src = os.path.join(piper_dir, name)
        if os.path.exists(src):
            piper_manual_bins.append((src, 'piper'))
            break
except Exception:
    pass

try:
    import onnxruntime  # noqa: F401
    onnx_bins = collect_dynamic_libs('onnxruntime')
    onnx_datas = collect_data_files('onnxruntime')
    onnx_hidden = collect_submodules('onnxruntime')
except Exception:
    pass

# 内置播放器（pygame + SDL）
pygame_datas, pygame_hidden, pygame_bins = [], [], []
try:
    import pygame  # noqa: F401
    pygame_datas = collect_data_files('pygame')
    pygame_hidden = collect_submodules('pygame')
    pygame_bins = collect_dynamic_libs('pygame')
except Exception:
    pass

base_datas = [('icon.png', '.'), ('icon.ico', '.')]
all_datas = base_datas + piper_datas + onnx_datas + pygame_datas
all_bins = piper_manual_bins + onnx_bins + pygame_bins

base_hidden = [
    # 在线引擎
    'edge_tts', 'aiohttp', 'aiosignal', 'frozenlist', 'multidict', 'yarl',
    'propcache', 'attr', 'attrs', 'certifi', 'charset_normalizer', 'idna', 'urllib3',
    # 通用网络 / 进度
    'requests', 'tqdm', 'tabulate',
    # 音频与压缩
    'pydub', 'wave', 'audioop',
    # 内置播放器
    'pygame',
]
all_hidden = base_hidden + piper_hidden + onnx_hidden + pygame_hidden

# audioop-lts（Python 3.13+ 兼容）
try:
    import audioop  # 有可能是 audioop-lts
    audioop_dir = os.path.dirname(audioop.__file__)
    # *.pyd / *.so（不同平台）
    for fname in ('_audioop.pyd', '_audioop.abi3.pyd', '_audioop.abi3.so'):
        src = os.path.join(audioop_dir, fname)
        if os.path.exists(src):
            all_bins.append((src, 'audioop'))
            break
except Exception:
    pass

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
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='audiobook_converter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
