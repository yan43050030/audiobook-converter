"""audiobook.i18n — 界面多语言（简体中文 / English）。

以中文原文为键：界面代码写 ``tr("开始转换")``，英文词典里有对应译文就显示译文，
没有就原样显示中文——漏译只会显示中文，不会显示空白或报错。带变量的文案用具名
占位符：``tr("已选 {n} 个文件", n=len(paths))``。

语言优先级：环境变量 ``AUDIOBOOK_LANG`` > 配置项 ``language`` > 系统语言。
系统语言为中文（含繁体）时用中文，否则用英文。语言在进程内首次调用时确定，
切换语言需重启程序（界面在构建时取文案）。

本模块刻意不导入 ``tts_engine``（那会触发启动日志与重依赖），配置直接读 JSON 文件。
"""

import json
import locale
import os
import sys

SUPPORTED = ("zh", "en")
LANGUAGE_CHOICES = ("auto", "zh", "en")

# 设置界面用的语言名称：每种语言都用其自身书写，任何界面语言下都认得出来
LANGUAGE_NAMES = {
    "auto": ("跟随系统", "System default"),
    "zh": ("简体中文", "简体中文"),
    "en": ("English", "English"),
}

_lang = None


def _config_path() -> str:
    # 已加载 tts_engine 时以它为准（测试会把 CONFIG_PATH 指向临时目录）
    te = sys.modules.get("tts_engine")
    path = getattr(te, "CONFIG_PATH", None) if te is not None else None
    return path or os.path.join(os.path.expanduser("~"), ".audiobook_converter", "config.json")


def configured_language() -> str:
    """配置文件里的 language 设置（auto / zh / en），读不到或非法时为 auto。"""
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            value = json.load(f).get("language", "auto")
    except (OSError, ValueError, AttributeError):
        return "auto"
    return value if value in LANGUAGE_CHOICES else "auto"


def _system_locale_name() -> str:
    # GUI 进程里优先用 Qt 的系统区域：macOS 从 Finder 启动时 LANG 往往为空。
    # 只在 Qt 已加载时使用，避免 CLI 为了查语言去导入 Qt。
    for mod in ("PySide6.QtCore", "PyQt6.QtCore"):
        qtcore = sys.modules.get(mod)
        if qtcore is not None:
            try:
                return qtcore.QLocale.system().name()
            except Exception:
                pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var)
        if value:
            return value
    try:
        return locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        return ""


def detect_system_language() -> str:
    name = _system_locale_name().lower()
    # Windows 的 locale 名形如 "Chinese (Simplified)_China"
    return "zh" if name.startswith("zh") or name.startswith("chinese") else "en"


def resolve_language(setting: str) -> str:
    """把 auto / zh / en（大小写、zh_CN 之类写法都可）解析为实际语言。"""
    s = (setting or "auto").strip().lower().replace("-", "_")
    if s.startswith("zh"):
        return "zh"
    if s.startswith("en"):
        return "en"
    return detect_system_language()


def current_language() -> str:
    global _lang
    if _lang is None:
        _lang = resolve_language(os.environ.get("AUDIOBOOK_LANG") or configured_language())
    return _lang


def set_language(lang) -> None:
    """强制使用某种语言（测试 / 命令行参数用）；传 None 恢复为按优先级自动确定。"""
    global _lang
    _lang = None if lang is None else resolve_language(lang)


def _catalog() -> dict:
    if current_language() == "en":
        from audiobook.locales.en import MESSAGES
        return MESSAGES
    return {}


def tr(text: str, /, **kwargs) -> str:
    """翻译一条界面文案；kwargs 填入 {占位符}。"""
    translated = _catalog().get(text, text)
    return translated.format(**kwargs) if kwargs else translated


def language_display_name(code: str) -> str:
    zh_name, en_name = LANGUAGE_NAMES.get(code, (code, code))
    return en_name if current_language() == "en" else zh_name
