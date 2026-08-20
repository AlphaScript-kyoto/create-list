"""実装済みリストの索引を先に作る（収集の初回待ちを短くする）。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collector.known_list import KnownList


def main() -> None:
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else PROJECT_ROOT / "output" / "現状のmplist" / "実装済みリスト.csv"
    )
    if not path.is_file():
        print(f"ファイルが見つかりません: {path}")
        return
    known = KnownList.load(path, log=print)
    print(f"完了: 電話 {known.phone_count:,} 件")


if __name__ == "__main__":
    main()
