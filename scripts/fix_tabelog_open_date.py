"""既存の食べログ CSV のオープン日を「オープン日　2026/9/8」形式に直す。"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.tabelog import TabelogAdapter


def convert_file(path: Path) -> tuple[int, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "オープン日" not in fieldnames:
        return 0, 0

    changed = 0
    for row in rows:
        old = row.get("オープン日") or ""
        new = TabelogAdapter._clean_open_date(old)
        if new != old:
            row["オープン日"] = new
            changed += 1

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return len(rows), changed


def main() -> None:
    output_dir = PROJECT_ROOT / "output"
    files = sorted(output_dir.glob("tabelog_full_*.csv"))
    if not files:
        print("output フォルダに食べログの CSV が見つかりませんでした。")
        return

    for path in files:
        total, changed = convert_file(path)
        if total == 0:
            print(f"スキップ（オープン日列なし）: {path.name}")
        else:
            print(f"更新: {path.name}（{changed}/{total} 件のオープン日を変換）")
            print(f"  バックアップ: {path.name}.bak")


if __name__ == "__main__":
    main()
