"""サイトアダプタの抽象基底クラス。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SiteAdapter(ABC):
    """1 サイト = 1 アダプタ。一覧・詳細ページの解析を担当する。"""

    site_id: str
    display_name: str
    top_url: str
    list_url_hint: str = ""

    @abstractmethod
    def validate_list_page(self, page: Any) -> bool:
        """現在ページが収集可能な一覧か判定する。"""

    @abstractmethod
    def extract_list_links(self, page: Any) -> list[str]:
        """一覧ページから詳細 URL を列挙する。"""

    @abstractmethod
    def go_next_page(self, page: Any) -> bool:
        """次ページへ遷移する。なければ False。"""

    @abstractmethod
    def extract_detail(self, page: Any, url: str) -> dict[str, str]:
        """詳細ページから項目 dict を返す。"""

    def extra_columns(self) -> list[str]:
        """CSV の追加列名（任意）。"""
        return []

    def set_company_filter(self, company_filter: Any) -> None:
        self._company_filter = company_filter

    def csv_columns(self) -> list[str] | None:
        """サイト専用 CSV 列。None なら共通フォーマット（BASE_COLUMNS + extra_columns）。"""
        return None

    def explain_list_page_failure(self, page: Any) -> str | None:
        """一覧ページ判定失敗時の追加ヒント（任意）。"""
        return None

    def supports_collection(self) -> bool:
        """False の場合は [開く] のみ（接続テスト用）。"""
        return True
