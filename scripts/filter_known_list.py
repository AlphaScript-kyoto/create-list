"""収集 CSV から、実装済みリストと重複する行を除いた「未登録」CSV を作る。"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collector.known_list import KnownList


def default_known_path() -> Path:
    return PROJECT_ROOT / "output" / "現状のmplist" / "実装済みリスト.csv"


def filter_file(path: Path, known: KnownList) -> tuple[int, int, Path]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    kept = [row for row in rows if not known.contains(row)]
    out_path = path.with_name(path.stem + "_未登録.csv")
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)
    return len(rows), len(rows) - len(kept), out_path


def main() -> None:
    known_path = default_known_path()
    args = [Path(p) for p in sys.argv[1:]]
    if args and args[0].name == "実装済みリスト.csv":
        known_path = args.pop(0)

    if not known_path.is_file():
        print(f"実装済みリストが見つかりません: {known_path}")
        return

    print(f"実装済みリストを読み込みます: {known_path}")
    known = KnownList.load(known_path, log=print)

    targets = args or sorted((PROJECT_ROOT / "output").glob("*_full_*.csv"))
    if not targets:
        print("対象の CSV が見つかりませんでした。")
        return

    for path in targets:
        if not path.is_file() or path.name.endswith("_未登録.csv"):
            continue
        total, removed, out_path = filter_file(path, known)
        print(
            f"{path.name}: {total} 件中 {removed} 件が既出 → {out_path.name}（{total - removed} 件）"
        )


if __name__ == "__main__":
    main()
