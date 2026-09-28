"""`python -m audiobook` 入口 → 命令行接口。"""
import sys

from audiobook.cli import main

if __name__ == "__main__":
    sys.exit(main())
