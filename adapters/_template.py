"""新規サイトアダプタの雛形。コピーして site_xxx.py として実装してください。

CSV に 090/080/070 と実装済みリストの既出は出ません（CsvWriter が全サイト共通で除外します）。
"""

from __future__ import annotations

from adapters.base import SiteAdapter


class TemplateSiteAdapter(SiteAdapter):
    site_id = "site_template"
    display_name = "テンプレート（未実装）"
    top_url = "https://example.com/"

    def validate_list_page(self, page) -> bool:
        # 例: return "search-result" in page.url
        return False

    def extract_list_links(self, page) -> list[str]:
        links: list[str] = []
        for el in page.locator("a.detail-link").all():
            href = el.get_attribute("href")
            if href:
                links.append(href)
        return links

    def go_next_page(self, page) -> bool:
        next_btn = page.locator("a.next-page")
        if next_btn.count() == 0:
            return False
        next_btn.first.click()
        page.wait_for_load_state("domcontentloaded")
        return True

    def extract_detail(self, page, url: str) -> dict[str, str]:
        return {
            "会社名": page.locator("[data-field='company-name']").inner_text().strip(),
            "住所": page.locator("[data-field='address']").inner_text().strip(),
            "電話番号": page.locator("[data-field='phone']").inner_text().strip(),
            "詳細URL": url,
        }
