"""Google マップ アダプタ（少量収集の許可取得済み・1 検索あたり最大 120 件）。"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from adapters.base import SiteAdapter
from collector.csv_schema import build_row, csv_columns

# 1 回の検索で一覧スクロールから出てくる件数の上限（マニュアル準拠）
MAX_LIST_RESULTS = 120

_PHONE_PATTERN = re.compile(r"0\d{1,4}[-−‒–—]?\d{1,4}[-−‒–—]?\d{3,4}")
_PLUS81 = re.compile(r"\+?81(\d{9,11})")
_END_OF_LIST = (
    "リストの最後に到達しました",
    "リストの最後です",
    "You've reached the end of the list",
    "You've reached the end",
)


class GoogleMapsAdapter(SiteAdapter):
    site_id = "google_maps"
    display_name = "Google マップ"
    top_url = "https://www.google.com/maps"
    list_url_hint = "/maps"

    def csv_columns(self) -> list[str] | None:
        return csv_columns("業種", "評価", "HP", "営業時間")

    def validate_list_page(self, page) -> bool:
        if not self._is_maps_url(page.url):
            return False
        if self._looks_like_challenge(page):
            return False
        return self._feed_card_count(page) >= 1

    def explain_list_page_failure(self, page) -> str | None:
        if self._looks_like_challenge(page):
            return (
                "確認画面（同意・bot 判定など）が出ています。\n"
                "  → ブラウザで内容を確認し、左側にお店の一覧が出てから [収集開始] してください。"
            )
        if "/maps/place/" in (page.url or ""):
            return (
                "店舗の詳細パネルだけが開いているようです。\n"
                "  → 検索結果に戻り、左側に店のカードが並んだ状態で [収集開始] してください。"
            )
        return (
            "Google マップの検索結果一覧ではありません。\n"
            "  → [開く] のあと、キーワードで検索し、左側に店のカードが並んだ画面で [収集開始] してください。\n"
            "  → 1 回の検索で集められるのはおよそ 120 件までです。"
        )

    def extract_list_links(self, page) -> list[str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(700)
        links = self._collect_place_links(page)
        return links[:MAX_LIST_RESULTS]

    def go_next_page(self, page) -> bool:
        """次の HTML ページではなく、左一覧を少しスクロールしてカードを増やす。"""
        before = len(self._collect_place_links(page))
        if before >= MAX_LIST_RESULTS:
            return False
        if self._is_end_of_list(page):
            return False
        self._scroll_feed(page)
        page.wait_for_timeout(1400)
        after = len(self._collect_place_links(page))
        return after > before

    def extract_detail(self, page, url: str) -> dict[str, str]:
        page.wait_for_load_state("domcontentloaded")
        if self._looks_like_challenge(page):
            row = build_row(site_name=self.display_name, detail_url=url, company_name="")
            row["_skip_reason"] = "確認画面のため取得できません"
            return row

        try:
            page.wait_for_selector(
                'h1, button[data-item-id="address"], [data-item-id^="phone"]',
                timeout=10000,
            )
        except Exception:
            pass
        page.wait_for_timeout(600)

        info = self._extract_panel(page)
        name = info.get("name", "")
        phone = info.get("phone", "")
        row = build_row(
            site_name=self.display_name,
            detail_url=url,
            company_name=name,
            address=info.get("address", ""),
            phone=phone,
            extra={
                "業種": info.get("category", ""),
                "評価": info.get("rating", ""),
                "HP": info.get("website", ""),
                "営業時間": info.get("hours", ""),
            },
        )
        if not name:
            row["_skip_reason"] = "店名が取れない"
        elif not phone:
            row["_skip_reason"] = "電話番号なし"
        return row

    def _collect_place_links(self, page) -> list[str]:
        hrefs: list[str] = []
        try:
            hrefs = page.evaluate(
                """() => {
                    const feed = document.querySelector('div[role="feed"]');
                    const root = feed || document;
                    const seen = new Set();
                    const out = [];
                    const anchors = root.querySelectorAll('a[href*="/maps/place/"]');
                    for (const a of anchors) {
                        const href = a.href || '';
                        if (!href || seen.has(href)) continue;
                        seen.add(href);
                        out.push(href);
                    }
                    return out;
                }"""
            )
        except Exception:
            hrefs = []

        links: list[str] = []
        seen: set[str] = set()
        for href in hrefs or []:
            clean = self._normalize_place_url(str(href))
            if clean and clean not in seen:
                seen.add(clean)
                links.append(clean)
        return links

    def _scroll_feed(self, page) -> None:
        try:
            page.evaluate(
                """() => {
                    const feed = document.querySelector('div[role="feed"]');
                    if (feed) {
                        const links = feed.querySelectorAll('a[href*="/maps/place/"]');
                        if (links.length) {
                            links[links.length - 1].scrollIntoView({block: 'end'});
                        }
                        feed.scrollTop = feed.scrollHeight;
                        return true;
                    }
                    window.scrollBy(0, 800);
                    return false;
                }"""
            )
        except Exception:
            pass

    def _is_end_of_list(self, page) -> bool:
        try:
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            return False
        return any(marker in text for marker in _END_OF_LIST)

    def _feed_card_count(self, page) -> int:
        try:
            return int(
                page.evaluate(
                    """() => {
                        const feed = document.querySelector('div[role="feed"]');
                        if (!feed) return 0;
                        return feed.querySelectorAll('a[href*="/maps/place/"]').length;
                    }"""
                )
                or 0
            )
        except Exception:
            return 0

    def _looks_like_challenge(self, page) -> bool:
        try:
            if page.locator('iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"]').count() > 0:
                return True
            text = (
                page.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 1500)"
                )
                or ""
            )
        except Exception:
            return False
        markers = (
            "Before you continue",
            "unusual traffic",
            "自動アクセス",
            "ロボットではありません",
            "I'm not a robot",
        )
        return any(m in text for m in markers)

    def _extract_panel(self, page) -> dict[str, str]:
        try:
            data = page.evaluate(
                """() => {
                    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    const byId = (id) => document.querySelector('[data-item-id="' + id + '"]');
                    const byPrefix = (prefix) => document.querySelector('[data-item-id^="' + prefix + '"]');

                    const h1 = document.querySelector('h1');
                    let name = h1 ? clean(h1.textContent) : '';

                    let address = '';
                    const addrBtn = byId('address');
                    if (addrBtn) {
                        const label = addrBtn.getAttribute('aria-label') || '';
                        address = clean(label.replace(/^住所[:：]\\s*/, '').replace(/住所をコピー.*/, ''));
                        if (!address) address = clean(addrBtn.textContent);
                    }

                    let phone = '';
                    const phoneBtn = byPrefix('phone');
                    if (phoneBtn) {
                        const label = phoneBtn.getAttribute('aria-label') || '';
                        phone = clean(
                            label.replace(/^電話番号[:：]\\s*/, '').replace(/電話番号をコピー.*/, '')
                        );
                        const id = phoneBtn.getAttribute('data-item-id') || '';
                        if (!/\\d/.test(phone) && id.includes('tel:')) {
                            phone = id.split('tel:')[1] || '';
                        }
                    }

                    let website = '';
                    const auth = byId('authority');
                    if (auth && auth.href) website = auth.href;

                    let hours = '';
                    const oh = byId('oh');
                    if (oh) {
                        hours = clean(oh.getAttribute('aria-label') || oh.textContent || '');
                        hours = hours.replace(/^営業時間[:：]\\s*/, '');
                    }
                    if (!hours) {
                        const hourEl = document.querySelector(
                            'div[aria-label*="営業時間"], div[aria-label*="Hours"], table[aria-label*="営業"]'
                        );
                        if (hourEl) hours = clean(hourEl.getAttribute('aria-label') || hourEl.textContent || '');
                    }

                    let rating = '';
                    const ratingImg = document.querySelector(
                        '[role="img"][aria-label*="星"], [role="img"][aria-label*="star"]'
                    );
                    if (ratingImg) rating = clean(ratingImg.getAttribute('aria-label') || '');

                    let category = '';
                    const catBtn = document.querySelector('button[jsaction*="pane.rating.category"]');
                    if (catBtn) category = clean(catBtn.textContent);
                    if (!category) {
                        const cat = document.querySelector('button.DkEaL');
                        if (cat) category = clean(cat.textContent);
                    }

                    return {name, address, phone, website, hours, rating, category};
                }"""
            )
            if isinstance(data, dict):
                return {
                    "name": self._clean_name(str(data.get("name") or "")),
                    "address": self._clean_address(str(data.get("address") or "")),
                    "phone": self._clean_phone(str(data.get("phone") or "")),
                    "website": self._clean_website(str(data.get("website") or "")),
                    "hours": self._clean_hours(str(data.get("hours") or "")),
                    "rating": self._clean_rating(str(data.get("rating") or "")),
                    "category": self._clean_category(str(data.get("category") or "")),
                }
        except Exception:
            pass
        return {
            "name": "",
            "address": "",
            "phone": "",
            "website": "",
            "hours": "",
            "rating": "",
            "category": "",
        }

    @staticmethod
    def _is_maps_url(url: str) -> bool:
        parsed = urlparse(url or "")
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if not (host.startswith("google.") or host.startswith("maps.google.")):
            return False
        return "/maps" in parsed.path or host.startswith("maps.google.")

    @staticmethod
    def _normalize_place_url(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host and "google." not in host and "maps.google." not in host:
            return ""
        if "/maps/place/" not in parsed.path:
            return ""
        return urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path, "", "", ""))

    @staticmethod
    def _clean_name(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if text in {"Google マップ", "Google Maps", "結果"}:
            return ""
        return text

    @staticmethod
    def _clean_address(text: str) -> str:
        text = text.replace("住所をコピー", "").replace("Copy address", "")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _clean_phone(text: str) -> str:
        text = (text or "").replace("tel:", "").replace("TEL:", "")
        text = text.replace("−", "-").replace("–", "-").replace("—", "-")
        plus = _PLUS81.search(text.replace(" ", ""))
        if plus:
            text = "0" + plus.group(1)
        match = _PHONE_PATTERN.search(text)
        return match.group(0) if match else ""

    @staticmethod
    def _clean_website(url: str) -> str:
        url = (url or "").strip()
        if not url.startswith("http"):
            return ""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if "google." in host and "/maps" in parsed.path:
            return ""
        return url

    @staticmethod
    def _clean_hours(text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        for prefix in ("営業時間:", "営業時間：", "Hours:", "Hours："):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
        return text[:300]

    @staticmethod
    def _clean_rating(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()[:80]

    @staticmethod
    def _clean_category(text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if text in {"ウェブサイト", "ルート", "保存"}:
            return ""
        return text[:80]
