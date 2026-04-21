# -*- mode: python ; coding: utf-8 -*-
# Windows exe spec - v2.4.0: cross-platform offline TTS + portable storage

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

base_datas = [('icon.png', '.'), ('icon.ico', '.')]
all_datas = base_datas + piper_datas + onnx_datas
all_bins = piper_manual_bins + onnx_bins

base_hidden = [
    'edge_tts', 'aiohttp', 'aiosignal', 'frozenlist', 'multidict', 'yarl',
    'propcache', 'attr', 'attrs', 'certifi', 'requests', 'tqdm', 'tabulate',
]
all_hidden = base_hidden + piper_hidden + onnx_hidden

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
