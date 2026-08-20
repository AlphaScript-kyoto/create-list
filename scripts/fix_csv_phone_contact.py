"""既存 CSV の電話番号にハイフンを付け、担当者の「その他」を空欄にする。"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collector.csv_schema import clean_contact_name
from collector.jp_phone import format_jp_phone


def convert_file(path: Path) -> tuple[int, int, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    phone_key = ""
    for key in ("電話番号", "企業代表番号", "専用電話番号"):
        if key in fieldnames:
            phone_key = key
            break
    contact_key = "担当者名" if "担当者名" in fieldnames else ""
    if not phone_key and not contact_key:
        return 0, 0, 0

    phone_changed = 0
    contact_cleared = 0
    for row in rows:
        if phone_key:
            old = row.get(phone_key) or ""
            new = format_jp_phone(old)
            if new != old:
                row[phone_key] = new
                phone_changed += 1
        if contact_key:
            old = row.get(contact_key) or ""
            new = clean_contact_name(old)
            if new != old:
                row[contact_key] = new
                contact_cleared += 1

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return len(rows), phone_changed, contact_cleared


def main() -> None:
    output_dir = PROJECT_ROOT / "output"
    targets = [Path(p) for p in sys.argv[1:]]
    if not targets:
        targets = sorted(output_dir.glob("*_full_*.csv"))
    if not targets:
        print("対象の CSV が見つかりませんでした。")
        return

    for path in targets:
        if not path.is_file():
            print(f"見つかりません: {path}")
            continue
        total, phones, contacts = convert_file(path)
        print(
            f"更新: {path.name}（{total} 件中、電話 {phones} 件・担当者 {contacts} 件を整形）"
        )
        print(f"  バックアップ: {path.name}.bak")


if __name__ == "__main__":
    main()
