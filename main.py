"""TPEX Tool — 上櫃資料工具 entry point."""

import logging
from logging.handlers import RotatingFileHandler

from views.main_window import MainWindow

# 同時寫檔：排程/批次下載整批失敗時，GUI 的 log 面板會隨程式關閉消失，
# 事後完全無法判斷是哪一步壞掉（驗證碼、403、欄位改版…）。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("tpex_tool.log", maxBytes=5_000_000,
                            backupCount=3, encoding="utf-8"),
    ],
)


def main():
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()