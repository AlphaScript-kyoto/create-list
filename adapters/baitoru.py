"""バイトル アダプタ（許可取得済みサイト向け）。"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlunparse

from adapters.base import SiteAdapter
from collector.csv_schema import build_row, csv_columns, extract_email

_JOBDETAIL_PATTERN = re.compile(r"/jobdetail/\d+")
_PHONE_PATTERN = re.compile(r"0\d{1,3}[-−‒–—]?\d{1,4}[-−‒–—]?\d{3,4}")
_PAGE_SUFFIX = re.compile(r"/page(\d+)/?$", re.I)


def next_jlist_url(current: str) -> str | None:
    """一覧 URL の次ページ（/page2, /page3 …）。一覧でなければ None。"""
    parsed = urlparse(current)
    path = parsed.path or "/"
    if "/jlist/" not in path and "/joblist/" not in path:
        return None
    stripped = path.rstrip("/")
    match = _PAGE_SUFFIX.search(stripped)
    if match:
        nxt = int(match.group(1)) + 1
        new_path = _PAGE_SUFFIX.sub(f"/page{nxt}", stripped) + "/"
    else:
        new_path = stripped + "/page2/"
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", parsed.query, ""))


class BaitoruAdapter(SiteAdapter):
    site_id = "baitoru"
    display_name = "バイトル"
    top_url = "https://www.baitoru.com/"
    list_url_hint = "/jlist/"

    def csv_columns(self) -> list[str] | None:
        return csv_columns()

    def validate_list_page(self, page) -> bool:
        path = urlparse(page.url).path
        if "/jobdetail/" in path:
            return False
        if "/jlist/" in path or "/joblist/" in path:
            return True
        try:
            return page.locator("a[href*='/jobdetail/']").count() >= 3
        except Exception:
            return False

    def explain_list_page_failure(self, page) -> str | None:
        path = urlparse(page.url).path
        if "/jobdetail/" in path:
            return (
                "求人の詳細ページが開いています。\n"
                "  → ブラウザで一覧（URL に jlist を含む検索結果）へ戻ってから [収集開始] してください。"
            )
        return (
            "検索結果一覧ページではありません。\n"
            "  → トップから地域・キーワードで検索し、求人カードが並んだ画面で [収集開始] してください。"
        )

    def extract_list_links(self, page) -> list[str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)

        hrefs: list[str] = []
        try:
            hrefs = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h && h.includes('/jobdetail/'))"""
            )
        except Exception:
            hrefs = []

        links: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            full = urljoin(page.url, href)
            parsed = urlparse(full)
            match = _JOBDETAIL_PATTERN.search(parsed.path)
            if not match:
                continue
            clean = f"{parsed.scheme}://{parsed.netloc}{match.group(0)}"
            if clean not in seen:
                seen.add(clean)
                links.append(clean)
        return links

    def go_next_page(self, page) -> bool:
        previous = page.url
        next_url = next_jlist_url(previous)
        if next_url and next_url.rstrip("/") != previous.rstrip("/"):
            try:
                page.goto(next_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1200)
                if self._is_advanced_list_page(page, previous):
                    return True
            except Exception:
                pass
            try:
                if page.url.rstrip("/") != previous.rstrip("/"):
                    page.goto(previous, wait_until="domcontentloaded")
                    page.wait_for_timeout(800)
            except Exception:
                pass

        if self._click_next_control(page) and self._is_advanced_list_page(page, previous):
            return True
        return False

    def _click_next_control(self, page) -> bool:
        locators = (
            page.locator('a[rel="next"]'),
            page.locator("a[href*='/page']").filter(has_text=re.compile(r"次へ")),
            page.get_by_role("link", name=re.compile(r"次へ")),
            page.get_by_role("button", name=re.compile(r"次へ")),
            page.locator("a").filter(has_text=re.compile(r"^\s*次へ")),
        )
        for loc in locators:
            try:
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=4000)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1200)
                return True
            except Exception:
                continue
        return False

    def _is_advanced_list_page(self, page, previous_url: str) -> bool:
        if not self.validate_list_page(page):
            return False
        if page.url.rstrip("/") == previous_url.rstrip("/"):
            return False
        return bool(self.extract_list_links(page))

    def extract_detail(self, page, url: str) -> dict[str, str]:
        page.wait_for_load_state("domcontentloaded")

        section = page.locator("#section-business-info")
        try:
            if section.count() > 0:
                section.scroll_into_view_if_needed()
                page.wait_for_timeout(400)
        except Exception:
            pass

        info = self._extract_business_info(page)
        row = build_row(
            site_name=self.display_name,
            detail_url=url,
            company_name=info.get("name", ""),
            address=info.get("address", ""),
            email=info.get("email", ""),
            contact=info.get("contact", ""),
        )
        row["従業員数"] = info.get("employees", "")
        filter_obj = getattr(self, "_company_filter", None)
        skip_reason = filter_obj.skip_reason(row) if filter_obj else None
        if skip_reason:
            row["電話番号"] = ""
            row["_skip_reason"] = skip_reason
            return row

        row["電話番号"] = self._extract_phone_from_dialog(page)
        return row

    def _extract_business_info(self, page) -> dict[str, str]:
        try:
            data = page.evaluate(
                """() => {
                    const section = document.querySelector('#section-business-info');
                    if (!section) return {name: '', address: '', employees: '', email: '', contact: ''};
                    const nameEl = section.querySelector('[class*="companyName"]');
                    let name = nameEl ? nameEl.textContent.trim() : '';
                    if (!name) {
                        const p = section.querySelector('p');
                        name = p ? p.textContent.trim() : '';
                    }
                    let address = '';
                    let employees = '';
                    let email = '';
                    let contact = '';
                    const nodes = section.querySelectorAll('dt, th, h3, h4, p, span, div');
                    for (const el of nodes) {
                        const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                        const next = el.nextElementSibling;
                        if (!next) continue;
                        const value = (next.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (t === '所在地' && !address) address = value;
                        if (t === '従業員数' && !employees) employees = value;
                        if ((t === 'メール' || t === 'メールアドレス' || t === 'E-mail') && !email) email = value;
                        if ((t === '担当者' || t === '担当者名') && !contact) contact = value;
                    }
                    return {name, address, employees, email, contact};
                }"""
            )
            if isinstance(data, dict):
                return {
                    "name": str(data.get("name") or "").strip(),
                    "address": str(data.get("address") or "").strip(),
                    "employees": str(data.get("employees") or "").strip(),
                    "email": extract_email(str(data.get("email") or "")),
                    "contact": str(data.get("contact") or "").strip(),
                }
        except Exception:
            pass
        return {"name": "", "address": "", "employees": "", "email": "", "contact": ""}

    def _extract_phone_from_dialog(self, page) -> str:
        button = page.locator("#section-business-info").get_by_text("電話番号を表示する")
        if button.count() == 0:
            button = page.get_by_role("button", name=re.compile("電話番号を表示"))
        if button.count() == 0:
            button = page.get_by_text("電話番号を表示する")
        if button.count() == 0:
            return ""

        try:
            button.first.click()
        except Exception:
            return ""

        dialog = page.locator("dialog").filter(has_text="専用電話番号")
        try:
            dialog.first.wait_for(state="visible", timeout=8000)
        except Exception:
            dialog = page.get_by_role("dialog")
            try:
                dialog.first.wait_for(state="visible", timeout=4000)
            except Exception:
                return ""

        phone = self._read_phone(dialog.first)
        self._close_dialog(page, dialog.first)
        return phone

    def _read_phone(self, dialog) -> str:
        try:
            labeled = dialog.get_by_text("専用電話番号")
            if labeled.count() > 0:
                parent = labeled.first.locator("xpath=ancestor::*[self::div or self::section or self::dl][1]")
                text = parent.inner_text() if parent.count() else dialog.inner_text()
            else:
                text = dialog.inner_text()
        except Exception:
            try:
                text = dialog.inner_text()
            except Exception:
                return ""

        match = _PHONE_PATTERN.search(text.replace("−", "-").replace("–", "-"))
        return match.group(0) if match else ""

    @staticmethod
    def _close_dialog(page, dialog) -> None:
        try:
            closer = dialog.get_by_role("button", name="閉じる")
            if closer.count() == 0:
                closer = page.get_by_role("button", name="閉じる")
            if closer.count() > 0:
                closer.first.click()
                page.wait_for_timeout(300)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
