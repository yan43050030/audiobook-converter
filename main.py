#!/usr/bin/env python3
"""文字转有声读物 - 程序入口"""

import tkinter as tk
from gui import AudiobookConverterApp


def main():
    root = tk.Tk()
    app = AudiobookConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
