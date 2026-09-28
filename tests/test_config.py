"""audiobook.config 配置 schema 测试（v6.0/B3）。

覆盖：默认值、类型校正、坏值回退、choices 白名单、未知键前向兼容，
以及「默认值与历史 .get() 回退一致」的行为不变契约。
"""

import audiobook.config as cfg


def test_defaults_have_all_keys_and_copies():
    d1 = cfg.defaults()
    d2 = cfg.defaults()
    assert set(d1) == set(cfg.SCHEMA)
    # 可变默认值不共享引用
    d1["extra_search_paths"].append("x")
    assert d2["extra_search_paths"] == []


def test_defaults_match_legacy_fallbacks():
    # 这些默认值必须与历史代码里的 .get(key, 默认值) 完全一致，保证接入后行为不变
    d = cfg.defaults()
    assert d["storage_dir"] == ""
    assert d["extra_search_paths"] == []
    assert d["theme"] == "light"
    assert d["concurrency"] == 0
    assert d["edge_concurrency"] == 0
    assert d["window_geometry"] == ""


def test_normalize_non_dict_returns_empty():
    assert cfg.normalize(None) == {}
    assert cfg.normalize([1, 2]) == {}
    assert cfg.normalize("nope") == {}


def test_normalize_keeps_unknown_keys():
    raw = {"totally_unknown": 42, "theme": "dark"}
    out = cfg.normalize(raw)
    assert out["totally_unknown"] == 42
    assert out["theme"] == "dark"


def test_normalize_does_not_inject_missing_keys():
    # 缺失键不注入，保持磁盘文件形态不变
    out = cfg.normalize({"theme": "dark"})
    assert set(out) == {"theme"}


def test_coerce_int_from_string():
    assert cfg.normalize({"concurrency": "5"})["concurrency"] == 5


def test_coerce_int_bad_value_falls_back():
    assert cfg.normalize({"concurrency": "abc"})["concurrency"] == 0
    assert cfg.normalize({"edge_concurrency": None})["edge_concurrency"] == 0


def test_theme_choices_enforced():
    assert cfg.normalize({"theme": "dark"})["theme"] == "dark"
    assert cfg.normalize({"theme": "neon"})["theme"] == "light"  # 非法回退默认


def test_list_bad_value_falls_back():
    assert cfg.normalize({"extra_search_paths": "not-a-list"})["extra_search_paths"] == []
    assert cfg.normalize({"extra_search_paths": ["/a", "/b"]})["extra_search_paths"] == ["/a", "/b"]


def test_str_coercion():
    assert cfg.normalize({"storage_dir": 123})["storage_dir"] == "123"


def test_effective_overlays_defaults():
    eff = cfg.effective({"theme": "dark", "extra": 1})
    assert eff["theme"] == "dark"
    assert eff["concurrency"] == 0        # 来自默认值
    assert eff["storage_dir"] == ""       # 来自默认值
    assert eff["extra"] == 1              # 未知键保留


def test_effective_on_non_dict_is_defaults():
    assert cfg.effective(None) == cfg.defaults()
