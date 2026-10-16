"""ホットペッパービューティー アダプタ。"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from urllib.parse import urljoin, urlparse

from adapters.base import SiteAdapter
from collector.csv_schema import build_row, csv_columns

_SALON_ID = re.compile(r"^/slnH\d+", re.I)
_PHONE_PATTERN = re.compile(r"0\d{1,4}[-−‒–—]?\d{1,4}[-−‒–—]?\d{3,4}")
_PREFERRED_TYPES = {
    "HairSalon",
    "BeautySalon",
    "NailSalon",
    "DaySpa",
    "HealthAndBeautyBusiness",
}
_SKIP_TYPES = {
    "BreadcrumbList",
    "SaleEvent",
    "ListItem",
    "Offer",
    "Place",
    "Thing",
    "AggregateRating",
    "GeoCoordinates",
    "PostalAddress",
}


class HotpepperBeautyAdapter(SiteAdapter):
    site_id = "hotpepper_beauty"
    display_name = "ホットペッパービューティー"
    top_url = "https://beauty.hotpepper.jp/"
    list_url_hint = "/PN"

    def csv_columns(self) -> list[str] | None:
        return csv_columns()

    def validate_list_page(self, page) -> bool:
        path = urlparse(page.url).path
        if _SALON_ID.match(path):
            return False
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
            return page.locator("h3.slnName a[href*='/slnH']").count() >= 1
        except Exception:
            return False

    def explain_list_page_failure(self, page) -> str | None:
        path = urlparse(page.url).path
        if _SALON_ID.match(path):
            return (
                "サロンの詳細ページが開いています。\n"
                "  → お店のカードが並んだ検索結果へ戻ってから [収集開始] してください。"
            )
        if path.rstrip("/") in ("", "/"):
            return (
                "ホットペッパービューティーのトップページです。\n"
                "  → エリアやジャンルで検索し、サロン名が並んだ一覧で [収集開始] してください。"
            )
        return (
            "検索結果一覧ページではありません。\n"
            "  → 例: https://beauty.hotpepper.jp/pre26/ （都道府県のサロン一覧）\n"
            "  → 黄色い帯の Chrome で検索し、サロンのカードが並んだ画面で [収集開始] してください。"
        )

    def extract_list_links(self, page) -> list[str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)
        hrefs: list[str] = []
        try:
            hrefs = page.evaluate(
                """() => Array.from(document.querySelectorAll('h3.slnName a[href*="/slnH"]'))
                    .map(a => a.href)
                    .filter(Boolean)"""
            )
        except Exception:
            hrefs = []

        links: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            clean = self._normalize_salon_url(urljoin(page.url, str(href)))
            if clean and clean not in seen:
                seen.add(clean)
                links.append(clean)
        return links

    def go_next_page(self, page) -> bool:
        next_link = page.locator("a.iS.arrowPagingR")
        if next_link.count() == 0:
            next_link = page.locator("a:has(span.arrowPagingR)")
        if next_link.count() == 0:
            return False
        try:
            href = next_link.first.get_attribute("href") or ""
            target = urljoin(page.url, href) if href else ""
            if target and target.rstrip("/") == page.url.rstrip("/"):
                return False
            if target:
                page.goto(target, wait_until="domcontentloaded")
            else:
                next_link.first.click()
                page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)
            return self.validate_list_page(page)
        except Exception:
            return False

    def extract_detail_from_html(self, html_text: str, url: str) -> dict[str, str] | None:
        info = self._parse_salon_html(html_text)
        if not info.get("name"):
            return None
        phone = info.get("phone", "")
        if not phone:
            phone = self._fetch_tel_phone(self._tel_url(url))
        return self._build(url, info.get("name", ""), info.get("address", ""), phone)

    def extract_detail(self, page, url: str) -> dict[str, str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)
        try:
            html_text = page.content()
        except Exception:
            html_text = ""
        info = self._parse_salon_html(html_text)
        phone = info.get("phone", "")
        if not phone:
            tel_url = self._tel_url(url)
            if tel_url:
                try:
                    page.goto(tel_url, wait_until="domcontentloaded", timeout=30000)
                    phone = self._phone_from_tel_html(page.content())
                except Exception:
                    phone = ""
        return self._build(url, info.get("name", ""), info.get("address", ""), phone)

    def _build(self, url: str, name: str, address: str, phone: str) -> dict[str, str]:
        row = build_row(
            site_name=self.display_name,
            detail_url=self._normalize_salon_url(url) or url,
            company_name=name,
            address=address,
            phone=phone,
        )
        if not row.get("電話番号"):
            row["_skip_reason"] = "電話番号なし"
        return row

    @staticmethod
    def _normalize_salon_url(url: str) -> str:
        parsed = urlparse(url)
        if "beauty.hotpepper.jp" not in (parsed.netloc or "").lower():
            return ""
        match = re.match(r"^/(slnH\d+)", parsed.path or "", re.I)
        if not match:
            return ""
        return f"https://beauty.hotpepper.jp/{match.group(1)}/"

    @classmethod
    def _tel_url(cls, url: str) -> str:
        salon = cls._normalize_salon_url(url)
        if not salon:
            return ""
        return salon + "tel/"

    @classmethod
    def _parse_salon_html(cls, html_text: str) -> dict[str, str]:
        name, address, phone = cls._from_json_ld(html_text)
        table_name, table_address, table_phone = cls._from_salon_table(html_text)
        return {
            "name": name or table_name or cls._h1_name(html_text),
            "address": address or table_address,
            "phone": phone or table_phone,
        }

    @classmethod
    def _from_json_ld(cls, html_text: str) -> tuple[str, str, str]:
        name = ""
        address = ""
        phone = ""
        for raw in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html_text or "",
            flags=re.I | re.S,
        ):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                types = item.get("@type")
                type_set = set(types if isinstance(types, list) else [types])
                if type_set & _SKIP_TYPES and not (type_set & _PREFERRED_TYPES):
                    continue
                if not (type_set & _PREFERRED_TYPES) and not item.get("telephone"):
                    continue
                item_name = cls._clean_text(str(item.get("name") or ""))
                item_phone = cls._clean_phone(str(item.get("telephone") or ""))
                item_address = cls._address_text(item.get("address"))
                if type_set & _PREFERRED_TYPES:
                    name = name or item_name
                    phone = phone or item_phone
                    address = address or item_address
                else:
                    name = name or item_name
                    phone = phone or item_phone
                    address = address or item_address
        return name, address, phone

    @classmethod
    def _from_salon_table(cls, html_text: str) -> tuple[str, str, str]:
        table = re.search(
            r'<table[^>]*class="[^"]*slnDataTbl[^"]*"[^>]*>(.*?)</table>',
            html_text or "",
            flags=re.I | re.S,
        )
        if not table:
            return "", "", ""
        address = ""
        phone = ""
        for label, cell in re.findall(
            r"<th[^>]*>\s*([^<]+?)\s*</th>\s*<td[^>]*>(.*?)</td>",
            table.group(1),
            flags=re.I | re.S,
        ):
            label_text = cls._clean_text(label)
            cell_text = cls._clean_text(cell)
            if label_text == "住所" and cell_text:
                address = cell_text
            if label_text == "電話番号":
                phone = cls._clean_phone(cell_text)
        return "", address, phone

    @staticmethod
    def _h1_name(html_text: str) -> str:
        match = re.search(r"<h1[^>]*>(.*?)</h1>", html_text or "", flags=re.I | re.S)
        if not match:
            return ""
        return HotpepperBeautyAdapter._clean_text(match.group(1))

    @staticmethod
    def _address_text(value) -> str:
        if isinstance(value, str):
            return HotpepperBeautyAdapter._clean_text(value)
        if isinstance(value, dict):
            parts = [
                value.get("postalCode") or "",
                value.get("addressRegion") or "",
                value.get("addressLocality") or "",
                value.get("streetAddress") or "",
                value.get("name") or "",
            ]
            text = HotpepperBeautyAdapter._clean_text(" ".join(str(part) for part in parts if part))
            return text
        return ""

    @staticmethod
    def _clean_text(value: str) -> str:
        text = html.unescape(value or "")
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("\xa0", " ").replace("\u3000", " ")
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _clean_phone(cls, value: str) -> str:
        text = cls._clean_text(value)
        if not text or "番号を表示" in text:
            return ""
        match = _PHONE_PATTERN.search(text)
        return match.group(0) if match else ""

    @classmethod
    def _phone_from_tel_html(cls, html_text: str) -> str:
        match = re.search(
            r"電話番号</th>\s*<td[^>]*>(.*?)</td>",
            html_text or "",
            flags=re.I | re.S,
        )
        if match:
            phone = cls._clean_phone(match.group(1))
            if phone:
                return phone
        return cls._clean_phone(html_text or "")

    @classmethod
    def _fetch_tel_phone(cls, tel_url: str) -> str:
        if not tel_url:
            return ""
        request = urllib.request.Request(
            tel_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "ja-JP,ja;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception:
            return ""
        return cls._phone_from_tel_html(body)
