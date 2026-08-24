"""実装済みリスト（既存 CSV）との重複判定。

索引（.known_index）とメタ（.known_meta）をキャッシュとして使い、
CSV が増えた分だけ追記読み込みする。全件の作り直しは、索引が無い／壊れた／
ファイルが小さくなったときだけ行う。
"""

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
_INDEX_HEADER_V1 = "KNOWN_INDEX_V1"
_INDEX_HEADER_V2 = "KNOWN_INDEX_V2"
_META_HEADER = "KNOWN_META_V1"


class KnownList:
    """電話番号を主、社名＋郵便番号を副として既出判定する。"""

    def __init__(self) -> None:
        self.phones: set[str] = set()
        self.name_keys: set[str] = set()
        self.source_path: Path | None = None
        self._indexed_size: int = 0
        self._pending_phones: set[str] = set()
        self._pending_name_keys: set[str] = set()

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
        self._remember_fields(fields[:4], track_pending=True)
        _ensure_trailing_newline(self.source_path)
        with self.source_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
            writer.writerow(fields)

    def refresh_index(self) -> None:
        """追記分だけ索引へ足し、メタの size/mtime を現在の CSV に合わせる。"""
        if not self.source_path or not self.source_path.is_file():
            return
        index_path = _index_path(self.source_path)
        meta_path = _meta_path(self.source_path)
        if not index_path.is_file():
            self._write_full_index(index_path, meta_path)
            return

        if self._pending_phones or self._pending_name_keys:
            with index_path.open("a", encoding="utf-8", newline="\n") as handle:
                for phone in sorted(self._pending_phones):
                    handle.write(f"P\t{phone}\n")
                for key in sorted(self._pending_name_keys):
                    handle.write(f"N\t{key}\n")
            self._pending_phones.clear()
            self._pending_name_keys.clear()

        stat = self.source_path.stat()
        self._indexed_size = stat.st_size
        _write_meta(meta_path, stat.st_mtime_ns, stat.st_size)

    def matches_source_file(self, path: Path) -> bool:
        """メモリ上のリストが、いまの CSV と増分でつながる状態か。"""
        path = Path(path)
        if self.source_path is None or path.resolve() != Path(self.source_path).resolve():
            return False
        if not path.is_file():
            return False
        size = path.stat().st_size
        return size >= self._indexed_size

    def sync_from_disk(self, log: LogFn | None = None) -> None:
        """CSV が増えていれば末尾だけ読んでメモリと索引を更新する。"""
        if not self.source_path or not self.source_path.is_file():
            return
        size = self.source_path.stat().st_size
        if size < self._indexed_size:
            if log:
                log("実装済みリストが小さくなっていたので、索引を作り直します…")
            rebuilt = KnownList.load(self.source_path, log=log, force_rebuild=True)
            self.phones = rebuilt.phones
            self.name_keys = rebuilt.name_keys
            self._indexed_size = rebuilt._indexed_size
            self._pending_phones.clear()
            self._pending_name_keys.clear()
            return
        if size == self._indexed_size:
            return
        if log:
            log(
                f"実装済みリストの増分を読み込みます"
                f"（+{size - self._indexed_size:,} バイト）…"
            )
        added = _ingest_csv_tail(self, self.source_path, self._indexed_size, log=log)
        self._indexed_size = size
        if log:
            log(f"  増分 {added:,} 行をメモリに反映しました")
        self.refresh_index()

    @classmethod
    def load(
        cls,
        path: Path,
        log: LogFn | None = None,
        force_rebuild: bool = False,
    ) -> "KnownList":
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(str(path))

        index_path = _index_path(path)
        meta_path = _meta_path(path)
        stat = path.stat()

        if not force_rebuild:
            loaded = cls._try_load_cached(path, index_path, meta_path, stat, log=log)
            if loaded is not None:
                return loaded

        if log:
            log("実装済みリストの索引が無い（または古い）ので作成します。初回は少し待ちます…")
        built = cls._build_from_csv(path, log=log)
        built.source_path = path
        built._indexed_size = stat.st_size
        try:
            built._write_full_index(index_path, meta_path)
            if log:
                log(f"索引を保存しました: {index_path.name}")
        except Exception as exc:
            logging.getLogger("list_collector").warning("known index の保存に失敗: %s", exc)
        return built

    @classmethod
    def _try_load_cached(
        cls,
        path: Path,
        index_path: Path,
        meta_path: Path,
        stat,
        log: LogFn | None = None,
    ) -> "KnownList | None":
        meta = _read_meta(meta_path)
        phones: set[str] = set()
        name_keys: set[str] = set()
        indexed_size = 0

        if meta is not None and index_path.is_file():
            indexed_size = meta["size"]
            if indexed_size == stat.st_size and meta["mtime_ns"] == stat.st_mtime_ns:
                if not cls._read_index_body(index_path, phones, name_keys):
                    return None
                known = cls()
                known.phones = phones
                known.name_keys = name_keys
                known.source_path = path
                known._indexed_size = indexed_size
                if log:
                    log(f"実装済みリストの索引を読み込みました: {index_path.name}")
                return known

            if 0 < indexed_size < stat.st_size:
                if not cls._read_index_body(index_path, phones, name_keys):
                    return None
                known = cls()
                known.phones = phones
                known.name_keys = name_keys
                known.source_path = path
                known._indexed_size = indexed_size
                if log:
                    log(
                        f"実装済みリストの索引を読み込み、増分だけ CSV から足します"
                        f"（+{stat.st_size - indexed_size:,} バイト）…"
                    )
                added = _ingest_csv_tail(known, path, indexed_size, log=log)
                known._indexed_size = stat.st_size
                if log:
                    log(f"  増分 {added:,} 行を反映しました")
                try:
                    known.refresh_index()
                except Exception as exc:
                    logging.getLogger("list_collector").warning(
                        "known index の増分保存に失敗: %s", exc
                    )
                return known

        # 旧形式 V1（索引ファイル内に mtime/size）との互換
        v1_exact = cls._try_load_index_v1(index_path, stat.st_mtime_ns, stat.st_size)
        if v1_exact is not None:
            v1_exact.source_path = path
            v1_exact._indexed_size = stat.st_size
            try:
                # 全件書き直しはしない。メタだけ足して次回から増分できるようにする
                _write_meta(meta_path, stat.st_mtime_ns, stat.st_size)
            except Exception:
                pass
            if log:
                log(f"実装済みリストの旧索引を読み込みました: {index_path.name}")
            return v1_exact

        legacy_size = cls._peek_v1_size(index_path) if index_path.is_file() else None
        if legacy_size and 0 < legacy_size < stat.st_size:
            phones.clear()
            name_keys.clear()
            if cls._read_index_body(index_path, phones, name_keys, skip_v1_meta=True):
                known = cls()
                known.phones = phones
                known.name_keys = name_keys
                known.source_path = path
                known._indexed_size = legacy_size
                if log:
                    log("旧索引＋増分で実装済みリストを更新します…")
                _ingest_csv_tail(known, path, legacy_size, log=log)
                known._indexed_size = stat.st_size
                try:
                    known.refresh_index()
                except Exception:
                    pass
                return known

        return None

    @classmethod
    def _read_index_body(
        cls,
        index_path: Path,
        phones: set[str],
        name_keys: set[str],
        skip_v1_meta: bool = False,
    ) -> bool:
        try:
            with index_path.open("r", encoding="utf-8") as handle:
                first = handle.readline().strip()
                if first not in {_INDEX_HEADER_V1, _INDEX_HEADER_V2}:
                    return False
                for line in handle:
                    if skip_v1_meta or first == _INDEX_HEADER_V1:
                        if line.startswith("mtime:") or line.startswith("size:"):
                            continue
                    if line.startswith("P\t"):
                        phones.add(line[2:].strip())
                    elif line.startswith("N\t"):
                        name_keys.add(line[2:].strip())
        except Exception:
            return False
        return True

    @classmethod
    def _peek_v1_size(cls, index_path: Path) -> int | None:
        try:
            with index_path.open("r", encoding="utf-8") as handle:
                if handle.readline().strip() != _INDEX_HEADER_V1:
                    return None
                for _ in range(8):
                    line = handle.readline()
                    if not line:
                        break
                    if line.startswith("size:"):
                        return int(line.split(":", 1)[1].strip())
        except Exception:
            return None
        return None

    @classmethod
    def _try_load_index_v1(
        cls, index_path: Path, mtime_ns: int, size: int
    ) -> "KnownList | None":
        if not index_path.is_file():
            return None
        try:
            with index_path.open("r", encoding="utf-8") as handle:
                first = handle.readline().strip()
                if first != _INDEX_HEADER_V1:
                    return None
                meta_mtime = ""
                meta_size = ""
                phones: set[str] = set()
                name_keys: set[str] = set()
                for line in handle:
                    if line.startswith("mtime:"):
                        meta_mtime = line.split(":", 1)[1].strip()
                    elif line.startswith("size:"):
                        meta_size = line.split(":", 1)[1].strip()
                    elif line.startswith("P\t"):
                        phones.add(line[2:].strip())
                    elif line.startswith("N\t"):
                        name_keys.add(line[2:].strip())
        except Exception:
            return None
        if meta_mtime != str(mtime_ns) or meta_size != str(size):
            return None
        known = cls()
        known.phones = phones
        known.name_keys = name_keys
        return known

    def _write_full_index(self, index_path: Path, meta_path: Path) -> None:
        if not self.source_path:
            return
        stat = self.source_path.stat()
        tmp = index_path.with_suffix(index_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{_INDEX_HEADER_V2}\n")
            for phone in self.phones:
                handle.write(f"P\t{phone}\n")
            for key in self.name_keys:
                handle.write(f"N\t{key}\n")
        tmp.replace(index_path)
        _write_meta(meta_path, stat.st_mtime_ns, stat.st_size)
        self._indexed_size = stat.st_size
        self._pending_phones.clear()
        self._pending_name_keys.clear()

    def _remember_fields(self, row: list[str], track_pending: bool = False) -> None:
        if len(row) < 4:
            return
        name, postal, address, phone = row[0], row[1], row[2], row[3]
        digits = phone_digits(phone)
        if len(digits) >= 10:
            if track_pending and digits not in self.phones:
                self._pending_phones.add(digits)
            self.phones.add(digits)
        key = _name_postal_key(
            {"企業名": name, "郵便番号": postal, "住所": address}
        )
        if key:
            if track_pending and key not in self.name_keys:
                self._pending_name_keys.add(key)
            self.name_keys.add(key)
        ident = company_key({"企業名": name, "住所": address})
        if ident:
            hashed = _hash_key(ident[0] + "\t" + ident[1])
            if track_pending and hashed not in self.name_keys:
                self._pending_name_keys.add(hashed)
            self.name_keys.add(hashed)

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


def _index_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(csv_path.suffix + ".known_index")


def _meta_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(csv_path.suffix + ".known_meta")


def _write_meta(meta_path: Path, mtime_ns: int, size: int) -> None:
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    tmp.write_text(
        f"{_META_HEADER}\nmtime:{mtime_ns}\nsize:{size}\n",
        encoding="utf-8",
    )
    tmp.replace(meta_path)


def _read_meta(meta_path: Path) -> dict[str, int] | None:
    if not meta_path.is_file():
        return None
    try:
        lines = meta_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    if not lines or lines[0].strip() != _META_HEADER:
        return None
    mtime_ns = None
    size = None
    for line in lines[1:]:
        if line.startswith("mtime:"):
            mtime_ns = int(line.split(":", 1)[1].strip())
        elif line.startswith("size:"):
            size = int(line.split(":", 1)[1].strip())
    if mtime_ns is None or size is None:
        return None
    return {"mtime_ns": mtime_ns, "size": size}


def _ingest_csv_tail(
    known: KnownList,
    path: Path,
    start_offset: int,
    log: LogFn | None = None,
) -> int:
    """CSV の start_offset 以降だけ読んで known に足す。戻り値は行数。"""
    added = 0
    with path.open("rb") as raw:
        if start_offset > 0:
            raw.seek(max(0, start_offset - 1))
            prev = raw.read(1)
            if prev not in (b"\n", b"\r"):
                # 行の途中から始まらないよう、次の改行まで捨てる
                raw.readline()
        text = raw.read().decode("utf-8-sig", errors="replace")
    if not text.strip():
        return 0
    reader = csv.reader(text.splitlines())
    for i, row in enumerate(reader, start=1):
        if len(row) >= 4:
            known._remember_fields(row[:4], track_pending=True)
            added += 1
        if log and i % 100000 == 0:
            log(f"  増分読み込み中… {i:,} 行")
    return added


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
        known._remember_fields([name, postal, address, phone], track_pending=False)
        return
    if len(row) < 4:
        return
    known._remember_fields(row[:4], track_pending=False)


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
