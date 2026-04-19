# -*- mode: python ; coding: utf-8 -*-
# macOS .app bundle spec

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.png', '.'), ('icon.ico', '.')],
    hiddenimports=['edge_tts', 'aiohttp', 'aiosignal', 'frozenlist', 'multidict', 'yarl', 'propcache', 'attr', 'attrs', 'certifi'],
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
    icon='icon.ico',
)

app = BUNDLE(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='AudiobookConverter.app',
    icon='icon.ico',
    bundle_identifier='com.audiobookconverter.app',
    info_plist={
        'CFBundleName': 'AudiobookConverter',
        'CFBundleDisplayName': '文字转有声读物',
        'CFBundleVersion': '2.0.0',
        'CFBundleShortVersionString': '2.0.0',
        'NSHighResolutionCapable': True,
    },
)
