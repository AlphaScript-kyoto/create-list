"""実装済みリスト（既存 CSV）との重複判定。"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from collector.csv_schema import company_key, normalize_text
from collector.jp_phone import format_jp_phone, phone_digits

LogFn = Callable[[str], None]

_HEADER_NAMES = {"企業名", "会社名", "社名", "店名"}
_CORP_PREFIX = re.compile(
    r"^(株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|"
    r"特定非営利活動法人|\(株\)|（株）|\(有\)|（有）)"
)
_INDEX_HEADER = "KNOWN_INDEX_V1"


class KnownList:
    """電話番号を主、社名＋郵便番号を副として既出判定する。"""

    def __init__(self) -> None:
        self.phones: set[str] = set()
        self.name_keys: set[str] = set()
        self.source_path: Path | None = None

    @property
    def phone_count(self) -> int:
        return len(self.phones)

    def contains(self, row: dict[str, str]) -> bool:
        phone = phone_digits(
            row.get("電話番号") or row.get("企業代表番号") or row.get("専用電話番号") or ""
        )
        if len(phone) >= 10 and phone in self.phones:
            return True
        key = _name_postal_key(row)
        if key and key in self.name_keys:
            return True
        ident = company_key(row)
        if ident is None:
            return False
        return _hash_key(ident[0] + "\t" + ident[1]) in self.name_keys

    def append_new(self, row: dict[str, str]) -> None:
        """未登録の 1 件を実装済みリストの末尾に足し、照合用の記憶も更新する。"""
        if not self.source_path:
            raise FileNotFoundError("実装済みリストのパスがありません")
        fields = _master_fields(row)
        _add_record(self, fields[:4], None)
        _ensure_trailing_newline(self.source_path)
        with self.source_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
            writer.writerow(fields)

    def refresh_index(self) -> None:
        """追記後のファイル時刻に合わせて索引を書き直す。"""
        if not self.source_path or not self.source_path.is_file():
            return
        stat = self.source_path.stat()
        index_path = self.source_path.with_suffix(self.source_path.suffix + ".known_index")
        self._write_index(index_path, stat.st_mtime_ns, stat.st_size)

    @classmethod
    def load(cls, path: Path, log: LogFn | None = None) -> "KnownList":
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(str(path))

        index_path = path.with_suffix(path.suffix + ".known_index")
        stat = path.stat()
        loaded = cls._try_load_index(index_path, stat.st_mtime_ns, stat.st_size)
        if loaded is not None:
            loaded.source_path = path
            if log:
                log(f"実装済みリストの索引を読み込みました: {index_path.name}")
            return loaded

        if log:
            log("実装済みリストの索引が無い（または古い）ので作成します。初回は少し待ちます…")
        built = cls._build_from_csv(path, log=log)
        built.source_path = path
        try:
            built._write_index(index_path, stat.st_mtime_ns, stat.st_size)
            if log:
                log(f"索引を保存しました: {index_path.name}")
        except Exception as exc:
            logging.getLogger("list_collector").warning("known index の保存に失敗: %s", exc)
        return built

    @classmethod
    def _try_load_index(cls, index_path: Path, mtime_ns: int, size: int) -> "KnownList | None":
        if not index_path.is_file():
            return None
        try:
            lines = index_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None
        if not lines or lines[0].strip() != _INDEX_HEADER:
            return None
        meta_mtime = ""
        meta_size = ""
        phones: set[str] = set()
        name_keys: set[str] = set()
        for line in lines[1:]:
            if line.startswith("mtime:"):
                meta_mtime = line.split(":", 1)[1].strip()
            elif line.startswith("size:"):
                meta_size = line.split(":", 1)[1].strip()
            elif line.startswith("P\t"):
                phones.add(line[2:].strip())
            elif line.startswith("N\t"):
                name_keys.add(line[2:].strip())
        if meta_mtime != str(mtime_ns) or meta_size != str(size):
            return None
        known = cls()
        known.phones = phones
        known.name_keys = name_keys
        return known

    def _write_index(self, index_path: Path, mtime_ns: int, size: int) -> None:
        tmp = index_path.with_suffix(index_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{_INDEX_HEADER}\n")
            handle.write(f"mtime:{mtime_ns}\n")
            handle.write(f"size:{size}\n")
            for phone in self.phones:
                handle.write(f"P\t{phone}\n")
            for key in self.name_keys:
                handle.write(f"N\t{key}\n")
        tmp.replace(index_path)

    @classmethod
    def _build_from_csv(cls, path: Path, log: LogFn | None = None) -> "KnownList":
        known = cls()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                first = next(reader)
            except StopIteration:
                return known
            named = _header_map(first)
            if named:
                rows = reader
            else:
                _add_record(known, first, None)
                rows = reader

            for i, row in enumerate(rows, start=2):
                _add_record(known, row, named)
                if log and i % 500000 == 0:
                    log(f"  実装済みリストを読み込み中… {i:,} 行")
        return known


def _master_fields(row: dict[str, str]) -> list[str]:
    name = (
        row.get("企業名") or row.get("社名") or row.get("会社名") or row.get("店名") or ""
    ).strip()
    postal = (row.get("郵便番号") or "").strip()
    address = (row.get("住所") or row.get("本社所在地") or row.get("所在地") or "").strip()
    phone = format_jp_phone(
        row.get("電話番号") or row.get("企業代表番号") or row.get("専用電話番号") or ""
    )
    raw_date = (row.get("取得日時") or "").strip()
    date = raw_date.split()[0] if raw_date else datetime.now().strftime("%Y/%m/%d")
    if len(date) > 10:
        date = date[:10]
    return [name, postal, address, phone, date]


def _ensure_trailing_newline(path: Path) -> None:
    with path.open("rb+") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            return
        handle.seek(-1, 2)
        last = handle.read(1)
        if last not in (b"\n", b"\r"):
            handle.write(b"\r\n")


def _header_map(row: list[str]) -> dict[str, int] | None:
    if not row:
        return None
    first = (row[0] or "").strip()
    if first not in _HEADER_NAMES:
        return None
    mapping: dict[str, int] = {}
    for i, col in enumerate(row):
        name = (col or "").strip()
        if name in _HEADER_NAMES:
            mapping["name"] = i
        elif name in {"郵便番号"}:
            mapping["postal"] = i
        elif name in {"住所", "本社所在地", "所在地"}:
            mapping["address"] = i
        elif name in {"電話番号", "企業代表番号", "専用電話番号", "TEL"}:
            mapping["phone"] = i
    return mapping or None


def _add_record(known: KnownList, row: list[str], named: dict[str, int] | None) -> None:
    if named:
        name = _cell(row, named.get("name"))
        postal = _cell(row, named.get("postal"))
        address = _cell(row, named.get("address"))
        phone = _cell(row, named.get("phone"))
    else:
        if len(row) < 4:
            return
        name, postal, address, phone = row[0], row[1], row[2], row[3]

    digits = phone_digits(phone)
    if len(digits) >= 10:
        known.phones.add(digits)
    key = _name_postal_key(
        {"企業名": name, "郵便番号": postal, "住所": address}
    )
    if key:
        known.name_keys.add(key)
    ident = company_key({"企業名": name, "住所": address})
    if ident:
        known.name_keys.add(_hash_key(ident[0] + "\t" + ident[1]))


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index] or ""


def _name_postal_key(row: dict[str, str]) -> str:
    name = _normalize_company_name(
        row.get("企業名") or row.get("社名") or row.get("会社名") or row.get("店名") or ""
    )
    postal = phone_digits(row.get("郵便番号") or "")
    if len(postal) >= 7:
        postal = postal[:7]
    else:
        postal = ""
        address = row.get("住所") or ""
        match = re.search(r"(\d{3})-?(\d{4})", address)
        if match:
            postal = match.group(1) + match.group(2)
    if not name or len(postal) != 7:
        return ""
    return _hash_key(name + "\t" + postal)


def _normalize_company_name(value: str) -> str:
    text = normalize_text(value)
    text = _CORP_PREFIX.sub("", text)
    return re.sub(r"[\s　]", "", text)


def _hash_key(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
