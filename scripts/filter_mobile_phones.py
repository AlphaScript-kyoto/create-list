"""既存 CSV から 090 / 080 / 070 の携帯電話行を除く。"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collector.csv_schema import is_mobile_phone


def convert_file(path: Path) -> tuple[int, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    phone_key = ""
    for key in ("電話番号", "企業代表番号", "専用電話番号"):
        if key in fieldnames:
            phone_key = key
            break
    if not phone_key:
        return 0, 0

    kept = [row for row in rows if not is_mobile_phone(row.get(phone_key) or "")]
    removed = len(rows) - len(kept)

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    return len(rows), removed


def main() -> None:
    output_dir = PROJECT_ROOT / "output"
    files = sorted(output_dir.glob("*_full_*.csv"))
    if not files:
        print("output フォルダに CSV が見つかりませんでした。")
        return

    for path in files:
        total, removed = convert_file(path)
        if total == 0:
            print(f"スキップ（電話番号列なし）: {path.name}")
        else:
            print(f"更新: {path.name}（{removed}/{total} 件を携帯電話のため除外）")
            print(f"  バックアップ: {path.name}.bak")


if __name__ == "__main__":
    main()
