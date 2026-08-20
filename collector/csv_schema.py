"""全サイト共通の CSV 列。"""

from __future__ import annotations

import re
import unicodedata

from collector.jp_phone import format_jp_phone, phone_digits

COMMON_COLUMNS = [
    "企業名",
    "郵便番号",
    "住所",
    "電話番号",
    "メールアドレス",
    "担当者名",
    "掲載サイト",
    "詳細URL",
    "取得日時",
]

_POSTAL_HEAD = re.compile(r"^〒?\s*(\d{3})-?(\d{4})")
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MOBILE_PREFIXES = ("090", "080", "070")


def csv_columns(*extra: str) -> list[str]:
    """共通列に、サイト専用列（オープン日など）を詳細URLの前へ足す。"""
    columns = list(COMMON_COLUMNS)
    if not extra:
        return columns
    index = columns.index("詳細URL")
    return columns[:index] + [c for c in extra if c not in columns] + columns[index:]


def split_postal_address(address: str, postal_hint: str = "") -> tuple[str, str]:
    """6018017京都府… を 601-8017 と 京都府… に分ける。無ければ郵便番号は空。"""
    address = re.sub(r"\s+", " ", (address or "").strip())
    address = address.replace("大きな地図を見る", "").replace("周辺のお店を探す", "").strip()

    digits = re.sub(r"\D", "", postal_hint or "")
    if len(digits) == 7:
        postal = f"{digits[:3]}-{digits[3:]}"
        rest = _POSTAL_HEAD.sub("", address).strip()
        return postal, rest or address

    match = _POSTAL_HEAD.match(address)
    if match:
        postal = f"{match.group(1)}-{match.group(2)}"
        return postal, address[match.end() :].strip()
    return "", address


def extract_email(text: str) -> str:
    match = _EMAIL_PATTERN.search(text or "")
    return match.group(0) if match else ""


def is_mobile_phone(phone: str) -> bool:
    """090 / 080 / 070 で始まる携帯電話。075 などの市外局番は対象外。"""
    digits = phone_digits(phone)
    return digits.startswith(_MOBILE_PREFIXES)


def normalize_text(value: str) -> str:
    """全角半角をそろえ、空白を1つにまとめる。"""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("\u3000", " ")
    return " ".join(text.split())


def clean_contact_name(value: str) -> str:
    """「その他」から始まる担当者欄は空にする（行は残す）。"""
    text = (value or "").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    if text.startswith("その他") or first_line.startswith("その他"):
        return ""
    return text


def company_key(row: dict[str, str]) -> tuple[str, str] | None:
    """社名 + 住所。どちらか空なら重複判定しない。"""
    name = normalize_text(
        row.get("企業名")
        or row.get("社名")
        or row.get("会社名")
        or row.get("店名")
        or ""
    )
    address = normalize_text(
        row.get("住所") or row.get("本社所在地") or row.get("所在地") or ""
    )
    if not name or not address:
        return None
    return (name, address)


def build_row(
    *,
    site_name: str,
    detail_url: str,
    company_name: str = "",
    postal: str = "",
    address: str = "",
    phone: str = "",
    email: str = "",
    contact: str = "",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    postal_value, address_value = split_postal_address(address, postal)
    row = {
        "企業名": (company_name or "").strip(),
        "郵便番号": postal_value,
        "住所": address_value,
        "電話番号": format_jp_phone(phone),
        "メールアドレス": extract_email(email),
        "担当者名": clean_contact_name(contact),
        "掲載サイト": site_name,
        "詳細URL": detail_url,
    }
    if extra:
        row.update(extra)
    return row
