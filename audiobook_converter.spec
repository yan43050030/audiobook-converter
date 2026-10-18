# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.png', '.'), ('icon.ico', '.'), ('audiobook/cloning/bridge_worker.py', 'audiobook/cloning')],
    hiddenimports=['edge_tts', 'aiohttp', 'aiosignal', 'frozenlist', 'multidict', 'yarl', 'propcache', 'attr', 'attrs', 'certifi',
                   'faster_whisper', 'ebooklib', 'fitz', 'pdfplumber', 'torch', 'cosyvoice', 'soundfile', 'librosa',
                   'audiobook', 'audiobook.config', 'audiobook.models', 'audiobook.io.hub', 'audiobook.onboarding', 'audiobook.core', 'audiobook.core.text', 'audiobook.io', 'audiobook.io.readers',
                   'audiobook.io.audio', 'audiobook.engines', 'audiobook.engines.base', 'audiobook.engines.local',
                   'audiobook.engines.edge', 'audiobook.engines.piper', 'audiobook.engines.cosyvoice', 'audiobook.engines.clone', 'audiobook.cloning', 'audiobook.cloning.profiles', 'audiobook.cloning.client',
                   'audiobook.engines.asr', 'audiobook.i18n', 'audiobook.locales', 'audiobook.locales.en'],
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
    icon='icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='audiobook_converter',
)
