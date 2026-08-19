"""半手動リスト収集ツール — エントリポイント。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_utils import load_config, setup_logging
from ui.app_window import AppWindow


def main() -> None:
    config = load_config()
    log_dir = PROJECT_ROOT / config.get("log_dir", "logs")
    setup_logging(log_dir)

    app = AppWindow(config=config, project_root=PROJECT_ROOT)
    app.mainloop()


if __name__ == "__main__":
    main()
