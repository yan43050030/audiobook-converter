"""PyInstaller runtime hook — 设置 Qt 平台插件路径"""
import os
import sys

if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
    # macOS .app: 可执行文件在 Contents/MacOS/，资源和框架在 Contents/
    if sys.platform == 'darwin':
        contents_dir = os.path.dirname(app_dir)
        search_dirs = [
            os.path.join(contents_dir, 'Frameworks'),
            os.path.join(contents_dir, 'Resources'),
        ]
    else:
        search_dirs = [os.path.join(app_dir, '_internal')]

    for base in search_dirs:
        # PySide6打包后可能在 Frameworks/PySide6/Qt/plugins/ 或 Resources/PySide6/Qt/plugins/
        for sub in ['PySide6', 'PyQt6']:
            qt_plugins = os.path.join(base, sub, 'Qt', 'plugins')
            if os.path.isdir(qt_plugins):
                os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(qt_plugins, 'platforms')
                os.environ['QT_PLUGIN_PATH'] = qt_plugins
                break
        if os.environ.get('QT_QPA_PLATFORM_PLUGIN_PATH'):
            break
