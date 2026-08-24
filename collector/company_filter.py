"""中小・零細向けの会社フィルタ。"""

from __future__ import annotations

import re
from typing import Any


def parse_employee_count(text: str) -> int | None:
    """「50人」「1,000人以上」などから人数を取る。"""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


class CompanyFilter:
    def __init__(
        self,
        enabled: bool = True,
        max_employees: int | None = 50,
        exclude_name_keywords: list[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.max_employees = max_employees
        self.exclude_name_keywords = [k for k in (exclude_name_keywords or []) if k.strip()]

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CompanyFilter":
        raw = config.get("company_filter") or {}
        max_emp = raw.get("max_employees", 50)
        if max_emp in (None, 0, ""):
            max_emp = None
        else:
            max_emp = int(max_emp)
        return cls(
            enabled=bool(raw.get("enabled", True)),
            max_employees=max_emp,
            exclude_name_keywords=list(raw.get("exclude_name_keywords") or []),
        )

    def name_keyword_reason(self, name: str) -> str | None:
        """社名だけの大手キーワード判定（一覧スキャン用）。"""
        if not self.enabled or not name:
            return None
        for keyword in self.exclude_name_keywords:
            if keyword and keyword.casefold() in name.casefold():
                return f"大手キーワード「{keyword}」のため除外"
        return None

    def skip_reason(self, row: dict[str, str]) -> str | None:
        if not self.enabled:
            return None

        name = row.get("企業名") or row.get("社名") or row.get("会社名") or row.get("店名") or ""
        keyword_reason = self.name_keyword_reason(name)
        if keyword_reason:
            return keyword_reason

        employees = parse_employee_count(row.get("従業員数") or "")
        if self.max_employees is not None and employees is not None:
            if employees > self.max_employees:
                return f"従業員数 {employees} 人が上限 {self.max_employees} 人を超えるため除外"

        return None
