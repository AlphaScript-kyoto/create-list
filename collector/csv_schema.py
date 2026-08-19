"""全サイト共通の CSV 列。"""

from __future__ import annotations

import re

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
        "電話番号": (phone or "").strip(),
        "メールアドレス": extract_email(email),
        "担当者名": (contact or "").strip(),
        "掲載サイト": site_name,
        "詳細URL": detail_url,
    }
    if extra:
        row.update(extra)
    return row
