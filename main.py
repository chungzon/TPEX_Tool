"""TPEX Tool — 上櫃資料工具 entry point."""

import logging

from views.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main():
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
