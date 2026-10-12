"""audiobook.web — 本地 Web UI（v6.0/B4）。

基于 FastAPI 的浏览器界面，复用 v6.0 分层库与引擎注册表，可跑在 NAS/服务器上
批量转换，绕开 PyInstaller 打包。属可选形态，需额外安装 `fastapi`/`uvicorn`
（`pip install "audiobook-converter[web]"`）。

对外仅暴露 `create_app()`；FastAPI 等重依赖延迟到该函数内导入，保证
`import audiobook` 与不使用 Web UI 的路径无需安装这些包。
"""


def create_app():
    """构造并返回 FastAPI 应用实例（延迟导入实现，避免顶层依赖 fastapi）。"""
    from audiobook.web.app import create_app as _create
    return _create()


__all__ = ["create_app"]
