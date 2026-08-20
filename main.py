"""半手動リスト収集ツール — エントリポイント。"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_utils import load_config, setup_logging
from ui.app_window import AppWindow


def _show_startup_error(exc: BaseException) -> None:
    """pythonw（VBS）起動でもエラーが見えるようにする。"""
    detail = "".join(traceback.format_exception(exc))
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "起動エラー",
            f"{exc}\n\n{detail[-1800:]}",
        )
        root.destroy()
    except Exception:
        print(detail, file=sys.stderr)


def main() -> None:
    try:
        config = load_config()
        log_dir = PROJECT_ROOT / config.get("log_dir", "logs")
        setup_logging(log_dir)

        app = AppWindow(config=config, project_root=PROJECT_ROOT)
        app.mainloop()
    except Exception as exc:
        _show_startup_error(exc)
        raise


if __name__ == "__main__":
    main()
