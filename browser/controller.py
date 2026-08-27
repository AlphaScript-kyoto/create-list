"""Playwright によるブラウザ制御（隔離プロファイル + シークレット相当）。"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

DisconnectCallback = Callable[[], None]


class BrowserController:
    def __init__(
        self,
        profile_dir: Path,
        headless: bool = False,
        channel: str = "chrome",
        incognito: bool = True,
        clear_storage_on_open: bool = True,
        profile_subdir: str = "chrome",
        on_disconnect: DisconnectCallback | None = None,
    ) -> None:
        self._profile_dir = profile_dir
        self._headless = headless
        self._channel = channel
        self._incognito = incognito
        self._clear_storage_on_open = clear_storage_on_open
        self._profile_subdir = profile_subdir
        self._on_disconnect = on_disconnect
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._closing = False
        self._dead = False

    def list_pages(self) -> list[Page]:
        if not self._context:
            return []
        try:
            return [p for p in self._context.pages if not p.is_closed()]
        except Exception:
            return []

    def tab_urls(self) -> list[str]:
        urls: list[str] = []
        for page in self.list_pages():
            try:
                urls.append(page.url)
            except Exception:
                urls.append("(不明)")
        return urls

    def pick_page(self, url_hint: str = "") -> Page | None:
        """収集に使うタブを選ぶ。url_hint を含むタブを優先し、ログアウト画面は避ける。"""
        if not self.is_alive():
            return None

        pages = self.list_pages()
        if not pages:
            self._page = None
            return None

        ranked: list[tuple[int, Page]] = []
        for page in pages:
            try:
                url = page.url
            except Exception:
                continue
            score = 0
            if url_hint and url_hint in url:
                score += 100
            if "session/destroy" in url:
                score -= 50
            if "/viewjob/" in url:
                score += 10
            ranked.append((score, page))

        if not ranked:
            self._page = pages[-1]
            return self._page

        ranked.sort(key=lambda item: item[0])
        self._page = ranked[-1][1]
        return self._page

    def _watch_page(self, page: Page) -> None:
        """ユーザーが操作したタブを、最後に遷移したページとして覚える。"""

        def on_nav(frame) -> None:
            try:
                if frame != page.main_frame:
                    return
                self._page = page
            except Exception:
                pass

        def on_close() -> None:
            try:
                if self._closing or self._dead:
                    return
                # イベント内では Playwright を stop しない（固まることがある）
                if not self.list_pages():
                    self._mark_dead(notify=True)
            except Exception:
                self._mark_dead(notify=True)

        page.on("framenavigated", on_nav)
        page.on("close", on_close)

    def open_site(self, url: str, force_new: bool = False) -> tuple[Page, str]:
        """サイトを開く。ウィンドウが閉じられていたら必ず新規起動する。"""
        if force_new or not self.is_alive():
            page = self.launch(url)
            return page, "Chrome を新規起動しました（隔離プロファイル）"

        page = self.pick_page() or self.page
        if page is None:
            page = self.launch(url)
            return page, "Chrome を新規起動しました（隔離プロファイル）"

        try:
            current = page.url
        except Exception:
            page = self.launch(url)
            return page, "Chrome を新規起動しました（隔離プロファイル）"

        if "session/destroy" in current:
            page = self._leave_destroy_page(page, url)
            return page, "ログアウト画面からトップへ戻しました"

        # 収集サイトを変えたときは、同じ Chrome でもそのサイトのトップへ移動する
        if self._same_site(current, url):
            return page, "既存ブラウザを継続します（検索結果はそのまま）"

        page.goto(url, wait_until="domcontentloaded")
        page = self._leave_destroy_page(page, url)
        return page, "収集サイトを切り替えました"

    @staticmethod
    def _same_site(current_url: str, target_url: str) -> bool:
        current_host = urlparse(current_url).netloc.lower().removeprefix("www.")
        target_host = urlparse(target_url).netloc.lower().removeprefix("www.")
        if not current_host or not target_host:
            return False
        return current_host == target_host

    @property
    def page(self) -> Page | None:
        if not self.is_alive():
            return None
        try:
            pages = self.list_pages()
            if not pages:
                self._page = None
                return None
            if self._page and not self._page.is_closed() and self._page in pages:
                return self._page
            self._page = pages[-1]
            return self._page
        except Exception:
            self._mark_dead(notify=False)
            return None

    def is_running(self) -> bool:
        return self.is_alive()

    def is_alive(self) -> bool:
        """実際に操作できる Chrome が残っているか。ユーザーがウィンドウを閉じたら False。"""
        if self._dead or not self._context or not self._playwright:
            return False
        try:
            pages = [p for p in self._context.pages if not p.is_closed()]
            if not pages:
                self._mark_dead(notify=False)
                return False
            _ = pages[0].url
            return True
        except Exception:
            self._mark_dead(notify=False)
            return False

    def launch(self, url: str) -> Page:
        logger = logging.getLogger("list_collector")
        self.close()
        self._dead = False

        profile_path = self._profile_dir / self._profile_subdir
        if self._incognito and profile_path.exists():
            shutil.rmtree(profile_path, ignore_errors=True)
        profile_path.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self._playwright = sync_playwright().start()
                launch_kwargs: dict[str, Any] = {
                    "user_data_dir": str(profile_path),
                    "headless": self._headless,
                    "viewport": {"width": 1280, "height": 900},
                    "locale": "ja-JP",
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                    ],
                }
                if self._channel:
                    launch_kwargs["channel"] = self._channel

                try:
                    self._context = self._playwright.chromium.launch_persistent_context(
                        **launch_kwargs
                    )
                except Exception as exc:
                    logger.warning(
                        "Chrome チャネル起動に失敗 (%s)。Chromium で起動します。", exc
                    )
                    launch_kwargs.pop("channel", None)
                    self._context = self._playwright.chromium.launch_persistent_context(
                        **launch_kwargs
                    )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger.warning("ブラウザ起動リトライ %s/3: %s", attempt + 1, exc)
                self.close()
                time.sleep(0.6 * (attempt + 1))

        if last_error is not None or self._context is None:
            self.close()
            raise RuntimeError(
                f"ブラウザを起動できませんでした: {last_error}"
            ) from last_error

        if self._clear_storage_on_open:
            self._clear_session_storage()

        self._context.on("page", self._on_new_page)
        self._context.on("close", self._on_context_close)
        for existing in self.list_pages():
            self._watch_page(existing)

        pages = self.list_pages()
        self._page = pages[0] if pages else self._context.new_page()
        if self._page not in pages:
            self._watch_page(self._page)
        self._page.goto(url, wait_until="domcontentloaded")
        self._page = self._leave_destroy_page(self._page, url)
        logger.info("Browser opened: %s (profile=%s)", self._page.url, profile_path)
        return self._page

    def _leave_destroy_page(self, page: Page, fallback_url: str) -> Page:
        logger = logging.getLogger("list_collector")
        for _ in range(3):
            try:
                page.wait_for_timeout(700)
                if "session/destroy" not in page.url:
                    return page
                logger.warning("session/destroy を検出。トップへ戻します。")
                page.goto(fallback_url, wait_until="domcontentloaded")
            except Exception:
                return page
        return page

    def _on_new_page(self, page: Page) -> None:
        self._page = page
        self._watch_page(page)

    def _on_context_close(self) -> None:
        if self._closing:
            return
        self._mark_dead(notify=True)

    def _mark_dead(self, notify: bool = False) -> None:
        """ウィンドウ閉鎖を記録する。Playwright の stop は close()/launch() 側で行う。"""
        already = self._dead
        self._dead = True
        self._page = None
        if notify and not already and self._on_disconnect is not None:
            try:
                self._on_disconnect()
            except Exception:
                pass

    def _clear_session_storage(self) -> None:
        if not self._context:
            return
        try:
            self._context.clear_cookies()
        except Exception:
            pass

    def close(self) -> None:
        self._closing = True
        try:
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
        finally:
            self._context = None
            self._playwright = None
            self._page = None
            self._dead = False
            self._closing = False
