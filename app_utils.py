"""設定読み込み・ログ初期化。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def load_config(path: Path | None = None) -> dict:
    config_path = path or PROJECT_ROOT / "config.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    root = logging.getLogger("list_collector")
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def timestamp_log(message: str) -> str:
    return f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
