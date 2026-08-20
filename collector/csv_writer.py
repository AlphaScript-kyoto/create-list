"""CSV 書き込み（UTF-8 BOM）。"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from collector.csv_schema import COMMON_COLUMNS, csv_skip_reason

BASE_COLUMNS = list(COMMON_COLUMNS)


class CsvWriter:
    def __init__(
        self,
        output_dir: Path,
        site_id: str,
        extra_columns: list[str] | None = None,
        columns: list[str] | None = None,
        flush_every: int = 5,
        known_list: object | None = None,
    ) -> None:
        self._extra_columns = extra_columns or []
        if columns:
            self._columns = columns
        else:
            self._columns = BASE_COLUMNS + self._extra_columns
        self._flush_every = max(1, flush_every)
        self._row_count = 0
        self._known_list = known_list

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.file_path = output_dir / f"{site_id}_full_{timestamp}.csv"

        self._file = self.file_path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=self._columns, extrasaction="ignore")
        self._writer.writeheader()
        self._file.flush()

    def append(self, row: dict[str, str]) -> str:
        """1 行書く。書けなければ理由を返す（携帯・実装済みは書かない）。"""
        reason = csv_skip_reason(row, self._known_list)
        if reason:
            return reason
        if "取得日時" not in row or not row["取得日時"]:
            row = {**row, "取得日時": datetime.now().strftime("%Y/%m/%d %H:%M:%S")}
        self._writer.writerow(row)
        self._row_count += 1
        if self._row_count % self._flush_every == 0:
            self._file.flush()
        return ""

    def close(self) -> None:
        self._file.flush()
        self._file.close()
