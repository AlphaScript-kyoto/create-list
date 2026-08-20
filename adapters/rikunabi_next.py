"""リクナビNEXT アダプタ（許可取得済みサイト向け）。"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from adapters.base import SiteAdapter
from collector.csv_schema import build_row, csv_columns, extract_email

_VIEWJOB_PATTERN = re.compile(r"/viewjob/[^/?#]+")


class RikunabiNextAdapter(SiteAdapter):
    site_id = "rikunabi_next"
    display_name = "リクナビNEXT"
    top_url = "https://next.rikunabi.com/"
    list_url_hint = "/job_search"

    def csv_columns(self) -> list[str] | None:
        return csv_columns()

    def validate_list_page(self, page) -> bool:
        return "/job_search" in page.url

    def explain_list_page_failure(self, page) -> str | None:
        url = page.url
        if "session/destroy" in url:
            return (
                "ブラウザがログアウト画面（session/destroy）になっています。\n"
                "  → [開く] でトップを開き直し、検索結果一覧（URL に job_search）まで進んでから [収集開始] してください。"
            )
        if "/viewjob/" in url:
            return (
                "求人の詳細ページが開いています。\n"
                "  → ブラウザで「戻る」を押して一覧に戻るか、検索結果一覧を開き直してください。"
            )
        if "/job_search" not in url:
            return (
                "検索結果一覧ページではありません（URL に job_search が必要です）。\n"
                "  → ブラウザでキーワード・エリア等を指定して検索し、求人一覧が並んだ画面まで進んでください。"
            )
        return "一覧ページ上に求人リンク（/viewjob/）が見つかりません。ページの読み込み完了後に再試行してください。"

    def extract_list_links(self, page) -> list[str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)

        hrefs: list[str] = []
        try:
            hrefs = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h && h.includes('/viewjob/'))"""
            )
        except Exception:
            hrefs = []

        if not hrefs:
            try:
                page.wait_for_selector("a[href*='viewjob']", timeout=5000)
            except Exception:
                pass
            for el in page.locator("a[href*='viewjob']").all():
                href = el.get_attribute("href")
                if href:
                    hrefs.append(urljoin(page.url, href))

        links: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            full = urljoin(page.url, href)
            parsed = urlparse(full)
            if not _VIEWJOB_PATTERN.search(parsed.path):
                continue
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/"
            if clean not in seen:
                seen.add(clean)
                links.append(clean)
        return links

    def go_next_page(self, page) -> bool:
        for label in ("次へ", "次の100件"):
            link = page.get_by_role("link", name=label)
            if link.count() > 0:
                link.first.click()
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1500)
                return True

        pagination = page.locator("nav").filter(has_text=re.compile(r"\d+"))
        if pagination.count() == 0:
            return False

        current = pagination.locator("[aria-current='page'], .current, .is-active")
        if current.count() == 0:
            return False

        next_link = current.first.locator("xpath=following-sibling::a[1]")
        if next_link.count() == 0:
            return False

        next_link.first.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)
        return True

    def extract_detail(self, page, url: str) -> dict[str, str]:
        page.wait_for_load_state("domcontentloaded")

        section = page.locator("#company-information")
        if section.count() > 0:
            section.scroll_into_view_if_needed()
            page.wait_for_timeout(400)

        phone = self._company_info_cell(page, "企業代表番号")
        if not phone:
            phone = self._company_info_cell(page, "お問い合わせ先")

        email = self._company_info_cell(page, "メールアドレス")
        if not email:
            email = self._company_info_cell(page, "メール")
        if not email:
            email = extract_email(self._company_info_cell(page, "お問い合わせ先"))

        contact = self._company_info_cell(page, "担当者")
        if (contact or "").strip().startswith("その他"):
            contact = ""
        elif not contact:
            contact = self._company_info_cell(page, "採用担当")
            if not contact:
                contact = self._company_info_cell(page, "代表者")

        return build_row(
            site_name=self.display_name,
            detail_url=url,
            company_name=self._company_info_cell(page, "社名"),
            address=self._company_info_cell(page, "本社所在地"),
            phone=phone,
            email=email,
            contact=contact,
        )

    def _company_info_cell(self, page, label: str) -> str:
        """#company-information 内の table からラベル行の値を取得。"""
        section = page.locator("#company-information")
        if section.count() > 0:
            rows = section.locator("tr")
            for i in range(rows.count()):
                row = rows.nth(i)
                try:
                    th = row.locator("th").first
                    if th.count() == 0:
                        continue
                    if label not in th.inner_text():
                        continue
                    td = row.locator("td").first
                    if td.count() > 0:
                        return td.inner_text().strip()
                except Exception:
                    continue

        return self._table_cell_fallback(page, label)

    @staticmethod
    def _table_cell_fallback(page, header: str) -> str:
        try:
            row = page.locator("tr").filter(has_text=header).first
            if row.count() == 0:
                return ""
            cells = row.locator("td")
            if cells.count() > 0:
                return cells.first.inner_text().strip()
        except Exception:
            pass
        return ""
