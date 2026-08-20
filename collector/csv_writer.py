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
        rows_per_file: int = 0,
    ) -> None:
        self._extra_columns = extra_columns or []
        if columns:
            self._columns = columns
        else:
            self._columns = BASE_COLUMNS + self._extra_columns
        self._flush_every = max(1, flush_every)
        self._row_count = 0
        self._file_row_count = 0
        self._part = 0
        self._known_list = known_list
        self._rows_per_file = max(0, int(rows_per_file))
        self._output_dir = output_dir
        self._site_id = site_id
        self._stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.file_path: Path | None = None
        self.closed_paths: list[Path] = []
        self.last_closed_path: Path | None = None
        self._file = None
        self._writer = None

        output_dir.mkdir(parents=True, exist_ok=True)
        self._open_new_file()

    def _open_new_file(self) -> None:
        self._part += 1
        if self._rows_per_file > 0:
            name = f"{self._site_id}_full_{self._stamp}_part{self._part:03d}.csv"
        else:
            name = f"{self._site_id}_full_{self._stamp}.csv"
        self.file_path = self._output_dir / name
        self._file = self.file_path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=self._columns, extrasaction="ignore")
        self._writer.writeheader()
        self._file.flush()
        self._file_row_count = 0

    def append(self, row: dict[str, str]) -> str:
        """1 行書く。書けなければ理由を返す（携帯・実装済みは書かない）。"""
        self.last_closed_path = None
        reason = csv_skip_reason(row, self._known_list)
        if reason:
            return reason
        if "取得日時" not in row or not row["取得日時"]:
            row = {**row, "取得日時": datetime.now().strftime("%Y/%m/%d %H:%M:%S")}
        assert self._writer is not None
        assert self._file is not None
        self._writer.writerow(row)
        self._row_count += 1
        self._file_row_count += 1
        if self._file_row_count % self._flush_every == 0:
            self._file.flush()
        if self._rows_per_file > 0 and self._file_row_count >= self._rows_per_file:
            closed = self.file_path
            self._close_current()
            self.last_closed_path = closed
            if closed:
                self.closed_paths.append(closed)
            self._open_new_file()
        return ""

    def _close_current(self) -> None:
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None
            self._writer = None

    def close(self) -> None:
        current = self.file_path
        rows_in_current = self._file_row_count
        self._close_current()
        if current is None:
            return
        if rows_in_current > 0:
            if current not in self.closed_paths:
                self.closed_paths.append(current)
            return
        if self._part > 1 and current.exists():
            try:
                current.unlink()
            except OSError:
                pass
            self.file_path = self.closed_paths[-1] if self.closed_paths else current
