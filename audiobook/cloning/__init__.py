"""audiobook.cloning — 声音克隆（v6.0/C1，基于 CosyVoice 零样本音色复刻）。

组成：
* `profiles`：音色档案（参考音频 + 文字稿）的存取与音频规整；
* `bridge_worker`：运行在用户 CosyVoice 环境里的独立桥接脚本（只依赖标准库 + cosyvoice）；
* `client`：管理桥接子进程（常驻、模型只加载一次、JSON 行协议），供 `clone` 引擎调用。

为什么用子进程：CosyVoice 以 git 仓库 + torch 方式安装，打包后的应用（PyInstaller 冻结的
Python）无法直接 import 它；让它在用户自己的 Python 环境里常驻运行，打包版与源码版都能用。
"""
