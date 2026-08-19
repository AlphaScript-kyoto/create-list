"""食べログ アダプタ（許可取得済みサイト向け）。"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from adapters.base import SiteAdapter
from collector.csv_schema import build_row, csv_columns

# https://tabelog.com/{pref}/A2601/A260201/26012136/
_SHOP_PATH = re.compile(r"^/[a-z]+/A\d+/A\d+/(\d+)/?$")
_PHONE_PATTERN = re.compile(r"0\d{1,4}[-−‒–—]?\d{1,4}[-−‒–—]?\d{3,4}")


class TabelogAdapter(SiteAdapter):
    site_id = "tabelog"
    display_name = "食べログ"
    top_url = "https://tabelog.com/"
    list_url_hint = "/rstLst"

    def __init__(self) -> None:
        self._open_dates: dict[str, str] = {}

    def csv_columns(self) -> list[str] | None:
        return csv_columns("オープン日")

    def validate_list_page(self, page) -> bool:
        path = urlparse(page.url).path
        if "/rstLst" not in path:
            return False
        if _SHOP_PATH.match(path):
            return False
        try:
            return page.locator("a.list-rst__rst-name-target, a.cpy-rst-name").count() >= 1
        except Exception:
            return True

    def explain_list_page_failure(self, page) -> str | None:
        path = urlparse(page.url).path
        if _SHOP_PATH.match(path):
            return (
                "店舗の詳細ページが開いています。\n"
                "  → ブラウザで一覧（URL に rstLst を含む検索結果）へ戻ってから [収集開始] してください。"
            )
        return (
            "検索結果一覧ページではありません（URL に rstLst が必要です）。\n"
            "  → トップからエリア・キーワードで検索し、お店のカードが並んだ画面で [収集開始] してください。"
        )

    def extract_list_links(self, page) -> list[str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)

        items: list[dict] = []
        try:
            items = page.evaluate(
                """() => Array.from(document.querySelectorAll('.list-rst__wrap')).map(card => {
                    const a = card.querySelector('a.list-rst__rst-name-target, a.cpy-rst-name');
                    const dateEl = card.querySelector('.list-rst__newopen');
                    return {
                        href: a ? a.href : '',
                        openDate: dateEl ? dateEl.textContent.trim() : ''
                    };
                }).filter(x => x.href)"""
            )
        except Exception:
            items = []

        if not items:
            try:
                page.wait_for_selector("a.list-rst__rst-name-target", timeout=5000)
                items = [
                    {"href": urljoin(page.url, el.get_attribute("href") or ""), "openDate": ""}
                    for el in page.locator("a.list-rst__rst-name-target").all()
                ]
            except Exception:
                items = []

        links: list[str] = []
        seen: set[str] = set()
        for item in items:
            href = str(item.get("href") or "")
            clean = self._normalize_shop_url(urljoin(page.url, href))
            if not clean or clean in seen:
                continue
            seen.add(clean)
            links.append(clean)
            open_date = self._clean_open_date(str(item.get("openDate") or ""))
            if open_date:
                self._open_dates[clean] = open_date
        return links

    def go_next_page(self, page) -> bool:
        next_link = page.locator('a[rel="next"]')
        if next_link.count() == 0:
            next_link = page.get_by_role("link", name=re.compile(r"次の\s*20件|次へ"))
        if next_link.count() == 0:
            return False
        try:
            next_link.first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1200)
            return True
        except Exception:
            return False

    def extract_detail(self, page, url: str) -> dict[str, str]:
        page.wait_for_load_state("domcontentloaded")
        info = self._extract_shop_info(page)
        name = info.get("name", "")
        phone = info.get("phone", "")
        open_date = info.get("open_date", "") or self._open_dates.get(url, "")
        row = build_row(
            site_name=self.display_name,
            detail_url=url,
            company_name=name,
            postal=info.get("postal", ""),
            address=info.get("address", ""),
            phone=phone,
            extra={"オープン日": open_date},
        )
        if not phone:
            row["_skip_reason"] = "電話番号なし"
        return row

    def _extract_shop_info(self, page) -> dict[str, str]:
        try:
            data = page.evaluate(
                """() => {
                    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    let name = '';
                    let address = '';
                    let postal = '';
                    let phone = '';
                    let openDate = '';

                    const scripts = Array.from(
                        document.querySelectorAll('script[type="application/ld+json"]')
                    );
                    for (const script of scripts) {
                        let parsed;
                        try { parsed = JSON.parse(script.textContent || ''); }
                        catch { continue; }
                        const items = Array.isArray(parsed) ? parsed : [parsed];
                        for (const item of items) {
                            const type = item && item['@type'];
                            const types = Array.isArray(type) ? type : [type];
                            if (!types.some(t => t === 'Restaurant' || t === 'LocalBusiness' || t === 'FoodEstablishment')) {
                                continue;
                            }
                            name = name || clean(item.name);
                            phone = phone || clean(item.telephone);
                            const addr = item.address || {};
                            postal = postal || clean(addr.postalCode);
                            const parts = [
                                addr.addressRegion,
                                addr.addressLocality,
                                addr.streetAddress,
                            ].filter(Boolean).map(clean);
                            if (parts.length) address = address || parts.join('');
                        }
                    }

                    if (!name) {
                        const nameEl = document.querySelector('.display-name, .rdheader-rstname');
                        name = nameEl ? clean(nameEl.textContent) : '';
                    }
                    if (!address) {
                        const addrEl = document.querySelector('.rstinfo-table__address');
                        address = addrEl ? clean(addrEl.textContent) : '';
                    }
                    if (!phone) {
                        const rows = document.querySelectorAll('tr');
                        for (const row of rows) {
                            const th = row.querySelector('th');
                            const td = row.querySelector('td');
                            if (!th || !td) continue;
                            const label = clean(th.textContent);
                            if (label.includes('お問い合わせ') || label.includes('電話')) {
                                phone = clean(td.textContent);
                                break;
                            }
                        }
                    }
                    const openEl = document.querySelector('.rstinfo-opened-date');
                    if (openEl) openDate = clean(openEl.textContent);
                    if (!openDate) {
                        const rows = document.querySelectorAll('tr');
                        for (const row of rows) {
                            const th = row.querySelector('th');
                            const td = row.querySelector('td');
                            if (!th || !td) continue;
                            if (clean(th.textContent).includes('オープン日')) {
                                openDate = clean(td.textContent);
                                break;
                            }
                        }
                    }
                    return {name, address, postal, phone, openDate};
                }"""
            )
            if isinstance(data, dict):
                return {
                    "name": self._clean_name(str(data.get("name") or "")),
                    "address": self._clean_address(str(data.get("address") or "")),
                    "postal": str(data.get("postal") or "").strip(),
                    "phone": self._clean_phone(str(data.get("phone") or "")),
                    "open_date": self._clean_open_date(str(data.get("openDate") or "")),
                }
        except Exception:
            pass
        return {"name": "", "address": "", "postal": "", "phone": "", "open_date": ""}

    @staticmethod
    def _normalize_shop_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.netloc and "tabelog.com" not in parsed.netloc:
            return ""
        match = _SHOP_PATH.match(parsed.path)
        if not match:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/"

    @staticmethod
    def _clean_name(text: str) -> str:
        return re.sub(r"\s+", " ", text.replace("掲載保留", "")).strip()

    @staticmethod
    def _clean_address(text: str) -> str:
        text = text.replace("大きな地図を見る", "").replace("周辺のお店を探す", "")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _clean_phone(text: str) -> str:
        text = text.replace("−", "-").replace("–", "-").replace("—", "-")
        match = _PHONE_PATTERN.search(text)
        return match.group(0) if match else ""

    @staticmethod
    def _clean_open_date(text: str) -> str:
        text = re.sub(r"\s+", "", text or "")
        text = text.replace("オープン", "")
        return text
