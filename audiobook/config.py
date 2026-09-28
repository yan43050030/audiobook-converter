"""audiobook.config — 配置 schema（v6.0/B3）。

集中描述 `~/.audiobook_converter/config.json` 里各配置项的默认值、类型与取值范围，
并提供规范化（normalize）与默认值（defaults）工具。目标是：

* 有一处权威定义配置项，取代散落在各处的 ``cfg.get(key, 默认值)``；
* 加载时对已知键做类型/取值校正，坏值回退默认，避免脏配置导致运行异常；
* 前向兼容：未知键原样保留，不会被丢弃（老/新版本互读配置不丢数据）。

设计为纯函数、零第三方依赖，可被 `tts_engine`、CLI、GUI 复用。

各默认值刻意与历史代码里的 ``.get(key, 默认值)`` 回退完全一致，保证接入后行为不变。
"""

from typing import Any, Dict


# 键 -> 规格：type（用于强制类型）、default（缺省/坏值回退）、
# choices（可选，取值白名单）、help（人类可读说明）。
SCHEMA: Dict[str, Dict[str, Any]] = {
    "storage_dir": {
        "type": str,
        "default": "",
        "help": "便携存储目录；空表示用默认 ~/.audiobook_converter",
    },
    "extra_search_paths": {
        "type": list,
        "default": [],
        "help": "额外的模型/程序搜索路径列表",
    },
    "theme": {
        "type": str,
        "default": "light",
        "choices": ["light", "dark"],
        "help": "界面主题：light / dark",
    },
    "concurrency": {
        "type": int,
        "default": 0,
        "help": "本地引擎并发数覆盖（0=自动，上限 16）",
    },
    "edge_concurrency": {
        "type": int,
        "default": 0,
        "help": "Edge 在线引擎并发数覆盖（0=自动，上限 10）",
    },
    "window_geometry": {
        "type": str,
        "default": "",
        "help": "窗口位置/大小记忆（由界面维护）",
    },
}


def _default_value(spec: Dict[str, Any]) -> Any:
    """返回该项默认值的独立副本（可变默认值不共享引用）。"""
    d = spec["default"]
    return list(d) if isinstance(d, list) else d


def defaults() -> Dict[str, Any]:
    """全部已知配置项的默认值字典（每次返回全新副本）。"""
    return {key: _default_value(spec) for key, spec in SCHEMA.items()}


def _coerce(key: str, value: Any) -> Any:
    """按 schema 把单个已知键的值校正为期望类型/取值；坏值回退默认。"""
    spec = SCHEMA[key]
    target = spec["type"]
    fallback = _default_value(spec)
    try:
        if target is int:
            # 允许 "5" 这类字符串数字；bool 是 int 子类，原样通过
            coerced: Any = int(value)
        elif target is str:
            coerced = value if isinstance(value, str) else str(value)
        elif target is list:
            if isinstance(value, (list, tuple)):
                coerced = list(value)
            else:
                return fallback
        else:
            coerced = value
    except (TypeError, ValueError):
        return fallback
    choices = spec.get("choices")
    if choices and coerced not in choices:
        return fallback
    return coerced


def normalize(raw: Any) -> Dict[str, Any]:
    """规范化一份配置：

    * 非 dict 输入 → 返回空 dict；
    * 已知键：按类型/取值校正，坏值回退默认；
    * 未知键：原样保留（前向兼容）；
    * 缺失的已知键不会被注入（保持磁盘文件形态不变，避免写回时膨胀）。
    """
    if not isinstance(raw, dict):
        return {}
    out = dict(raw)
    for key in list(out.keys()):
        if key in SCHEMA:
            out[key] = _coerce(key, out[key])
    return out


def effective(raw: Any) -> Dict[str, Any]:
    """在默认值之上叠加规范化后的配置，得到「生效值」全集，供展示/检视用。"""
    eff = defaults()
    eff.update(normalize(raw))
    return eff
