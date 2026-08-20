"""エキテン アダプタ（許可取得済みサイト向け）。"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlunparse

from adapters.base import SiteAdapter
from collector.csv_schema import build_row, csv_columns, extract_email

_SHOP_ANY = re.compile(r"/shop_(\d+)/")
_PHONE_PATTERN = re.compile(r"0\d{1,4}[-−‒–—]?\d{1,4}[-−‒–—]?\d{3,4}")
_PAGE_SUFFIX = re.compile(r"/p(\d+)/?$")


class EkitenAdapter(SiteAdapter):
    site_id = "ekiten"
    display_name = "エキテン"
    top_url = "https://www.ekiten.jp/"
    list_url_hint = "/a"

    def csv_columns(self) -> list[str] | None:
        return csv_columns()

    def validate_list_page(self, page) -> bool:
        path = urlparse(page.url).path
        if path.startswith("/shop_"):
            return False
        try:
            return page.locator('a[href*="/shop_"]').count() >= 3
        except Exception:
            return False

    def explain_list_page_failure(self, page) -> str | None:
        path = urlparse(page.url).path
        if path.startswith("/shop_"):
            return (
                "店舗の詳細ページが開いています。\n"
                "  → ブラウザで一覧（お店のカードが並んだ検索結果）へ戻ってから [収集開始] してください。"
            )
        return (
            "検索結果一覧ページではありません。\n"
            "  → トップからエリア・ジャンルで検索し、店舗カードが並んだ画面で [収集開始] してください。"
        )

    def extract_list_links(self, page) -> list[str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)

        hrefs: list[str] = []
        try:
            hrefs = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href*="/shop_"]'))
                    .map(a => a.href)
                    .filter(Boolean)"""
            )
        except Exception:
            hrefs = []

        links: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            clean = self._normalize_shop_url(urljoin(page.url, href))
            if clean and clean not in seen:
                seen.add(clean)
                links.append(clean)
        return links

    def go_next_page(self, page) -> bool:
        current = page.url
        parsed = urlparse(current)
        path = parsed.path.rstrip("/") or "/"
        match = _PAGE_SUFFIX.search(path)
        if match:
            nxt = int(match.group(1)) + 1
            new_path = _PAGE_SUFFIX.sub(f"/p{nxt}/", path + "/")
            if not new_path.endswith("/"):
                new_path += "/"
        else:
            new_path = path.rstrip("/") + "/p2/"

        next_url = urlunparse(
            (parsed.scheme, parsed.netloc, new_path, "", parsed.query, "")
        )
        if next_url.rstrip("/") == current.rstrip("/"):
            return False
        try:
            page.goto(next_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
        except Exception:
            return False
        if "404" in (page.title() or "") or not self.validate_list_page(page):
            try:
                page.go_back(wait_until="domcontentloaded")
            except Exception:
                pass
            return False
        if not self.extract_list_links(page):
            try:
                page.go_back(wait_until="domcontentloaded")
            except Exception:
                pass
            return False
        return True

    def extract_detail(self, page, url: str) -> dict[str, str]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(600)
        info = self._extract_overview(page)
        phone = info.get("phone", "")
        if not phone:
            phone = self._extract_phone_from_button(page)
        if not phone:
            phone = self._clean_phone(page.evaluate("() => document.body.innerText") or "")

        name = info.get("name", "")
        address = info.get("address", "")
        postal = info.get("postal", "")
        email = info.get("email", "")

        row = build_row(
            site_name=self.display_name,
            detail_url=url,
            company_name=name,
            postal=postal,
            address=address,
            phone=phone,
            email=email,
        )
        if not row.get("電話番号"):
            row["_skip_reason"] = "電話番号なし"
        return row

    def _extract_overview(self, page) -> dict[str, str]:
        try:
            data = page.evaluate(
                """() => {
                    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    const pick = (root, labels) => {
                        const nodes = root.querySelectorAll('h2,h3,h4,dt,th,p,div,span,dt,strong');
                        for (const el of nodes) {
                            const t = clean(el.textContent);
                            if (!labels.some(l => t === l || t.startsWith(l))) continue;
                            let nxt = el.nextElementSibling;
                            for (let i = 0; i < 4 && nxt; i++, nxt = nxt.nextElementSibling) {
                                const v = clean(nxt.textContent);
                                if (v && !labels.some(l => v === l)) return v;
                            }
                            const parent = el.parentElement;
                            if (parent) {
                                const v = clean(parent.textContent.replace(t, ''));
                                if (v) return v;
                            }
                        }
                        return '';
                    };

                    let root = document.body;
                    const headings = Array.from(document.querySelectorAll('h2,h3,section'));
                    for (const h of headings) {
                        if (clean(h.textContent).startsWith('概要')) {
                            root = h.parentElement || h;
                            break;
                        }
                    }

                    const h1 = document.querySelector('h1');
                    let name = h1 ? clean(h1.textContent) : '';
                    const overviewName = pick(root, ['店舗名']);
                    if (overviewName) {
                        const parts = overviewName.split(' ').filter(Boolean);
                        name = parts[parts.length - 1] || overviewName;
                    }

                    let address = pick(root, ['住所']);
                    let phone = pick(root, ['電話番号', 'TEL', 'Tel']);
                    let postal = '';
                    const access = pick(document.body, ['アクセス']) || '';
                    const postalMatch = (address + ' ' + access + ' ' + document.body.innerText)
                        .match(/〒\\s*(\\d{3})-?(\\d{4})/);
                    if (postalMatch) postal = postalMatch[1] + postalMatch[2];

                    const bullets = Array.from(root.querySelectorAll('li,p,div'))
                        .map(el => clean(el.textContent))
                        .filter(t => t.startsWith('●') || t.startsWith('・') || t.startsWith('▶'));
                    const overviewText = clean(root.innerText || '');
                    const extraLines = overviewText.split(/\\n/).map(clean).filter(Boolean);
                    const lines = bullets.concat(extraLines);

                    const phoneRe = /0\\d{1,4}[-−‒–—]?\\d{1,4}[-−‒–—]?\\d{3,4}/;
                    const addrRe = /(北海道|東京都|大阪府|京都府|.+?[都道府県])/;
                    for (const line of lines) {
                        const body = line.replace(/^[●・▶\\s]+/, '');
                        if (!phone && (body.includes('電話') || body.includes('TEL'))) {
                            const m = body.match(phoneRe);
                            if (m) phone = m[0];
                        }
                        if (!phone) {
                            const m = body.match(phoneRe);
                            if (m && /^0\\d/.test(m[0])) phone = phone || m[0];
                        }
                        if (!address && (body.includes('住所') || addrRe.test(body))) {
                            const stripped = body.replace(/^住所[：:\\s]*/, '');
                            if (addrRe.test(stripped) && stripped.length >= 8) address = stripped;
                        }
                        if ((!name || name.length < 2) && (body.startsWith('店舗名') || body.startsWith('店名'))) {
                            name = body.replace(/^(店舗名|店名)[：:\\s]*/, '');
                        }
                    }

                    const tel = document.querySelector('a[href^="tel:"]');
                    if (tel && !phone) phone = tel.getAttribute('href') || '';

                    const mail = (overviewText.match(/[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}/) || [''])[0];
                    return {name, address, phone, postal, email: mail};
                }"""
            )
            if isinstance(data, dict):
                return {
                    "name": str(data.get("name") or "").strip(),
                    "address": str(data.get("address") or "").strip(),
                    "phone": self._clean_phone(str(data.get("phone") or "")),
                    "postal": str(data.get("postal") or "").strip(),
                    "email": extract_email(str(data.get("email") or "")),
                }
        except Exception:
            pass
        return {"name": "", "address": "", "phone": "", "postal": "", "email": ""}

    def _extract_phone_from_button(self, page) -> str:
        tel_links = page.locator('a[href^="tel:"]')
        if tel_links.count() > 0:
            href = tel_links.first.get_attribute("href") or ""
            phone = self._clean_phone(href)
            if phone:
                return phone

        button = page.get_by_role("link", name=re.compile(r"電話する"))
        if button.count() == 0:
            button = page.get_by_role("button", name=re.compile(r"電話する"))
        if button.count() == 0:
            button = page.get_by_text("電話する", exact=True)
        if button.count() == 0:
            return ""

        try:
            href = button.first.get_attribute("href") or ""
        except Exception:
            href = ""
        phone = self._clean_phone(href)
        if phone:
            return phone

        try:
            button.first.click(timeout=4000)
            page.wait_for_timeout(800)
        except Exception:
            return ""

        tel_links = page.locator('a[href^="tel:"]')
        if tel_links.count() > 0:
            return self._clean_phone(tel_links.first.get_attribute("href") or "")
        return self._clean_phone(page.locator("body").inner_text()[:4000])

    @staticmethod
    def _normalize_shop_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.netloc and "ekiten.jp" not in parsed.netloc:
            return ""
        match = _SHOP_ANY.search(parsed.path)
        if not match:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}/shop_{match.group(1)}/"

    @staticmethod
    def _clean_phone(text: str) -> str:
        text = (text or "").replace("tel:", "").replace("TEL:", "")
        text = text.replace("−", "-").replace("–", "-").replace("—", "-")
        match = _PHONE_PATTERN.search(text)
        return match.group(0) if match else ""
