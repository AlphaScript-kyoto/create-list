"""sites.json から読み込むサイトアダプタ。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from adapters.base import SiteAdapter
from collector.csv_schema import build_row, csv_columns

# 収集に必要なセレクタキー
_SELECTOR_KEYS = ("list_link", "company_name", "address", "phone")
_OPTIONAL_SELECTOR_KEYS = ("list_url_contains", "next_page")


class JsonSiteAdapter(SiteAdapter):
    """sites.json の 1 エントリに対応するアダプタ。"""

    def __init__(self, entry: dict[str, Any]) -> None:
        self.site_id: str = entry["site_id"]
        self.display_name: str = entry["display_name"]
        self.top_url: str = entry["top_url"]
        self._selectors: dict[str, str] = entry.get("selectors") or {}
        self.notes: str = entry.get("notes", "")

    def supports_collection(self) -> bool:
        return all(self._selectors.get(key) for key in _SELECTOR_KEYS)

    def validate_list_page(self, page) -> bool:
        if not self.supports_collection():
            return False

        url_hint = self._selectors.get("list_url_contains")
        if url_hint and url_hint in page.url:
            return True

        list_link = self._selectors.get("list_link")
        if list_link:
            return page.locator(list_link).count() > 0

        return False

    def extract_list_links(self, page) -> list[str]:
        list_link = self._selectors.get("list_link", "")
        links: list[str] = []
        for el in page.locator(list_link).all():
            href = el.get_attribute("href")
            if href:
                links.append(urljoin(page.url, href))
        return links

    def go_next_page(self, page) -> bool:
        next_sel = self._selectors.get("next_page")
        if not next_sel:
            return False

        next_el = page.locator(next_sel)
        if next_el.count() == 0:
            return False

        href = next_el.first.get_attribute("href")
        if href:
            page.goto(urljoin(page.url, href), wait_until="domcontentloaded")
        else:
            next_el.first.click()
            page.wait_for_load_state("domcontentloaded")

        page.wait_for_timeout(300)
        return True

    def csv_columns(self) -> list[str] | None:
        return csv_columns()

    def extract_detail(self, page, url: str) -> dict[str, str]:
        def text(selector: str) -> str:
            return page.locator(selector).first.inner_text().strip()

        return build_row(
            site_name=self.display_name,
            detail_url=url,
            company_name=text(self._selectors["company_name"]),
            address=text(self._selectors["address"]),
            phone=text(self._selectors["phone"]),
        )
