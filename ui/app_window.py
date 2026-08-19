"""メイン GUI ウィンドウ。"""

from __future__ import annotations

import math
import time
from pathlib import Path
from tkinter import messagebox
from typing import Any

import customtkinter as ctk

from adapters.base import SiteAdapter
from adapters.registry import get_all_adapters
from app_utils import timestamp_log
from browser.controller import BrowserController
from collector.company_filter import CompanyFilter
from collector.governor import Governor
from collector.orchestrator import Orchestrator
from collector.tk_scheduler import TkScheduler
from data.japan_areas import (
    CITY_NONE,
    PREF_NONE,
    city_choices,
    is_placeholder,
    prefecture_choices,
)


class AppWindow(ctk.CTk):
    PACE_OPTIONS = {
        "安全": "safe",
        "標準": "normal",
        "速め": "fast",
    }

    def __init__(self, config: dict[str, Any], project_root: Path) -> None:
        super().__init__()
        self._config = config
        self._project_root = project_root
        self._output_dir = project_root / config.get("output_dir", "output")
        self._profile_dir = self._output_dir / ".browser_profile"

        self._adapters = get_all_adapters(project_root / config.get("sites_file", "sites.json"))
        self._adapter_map = {a.display_name: a for a in self._adapters}
        browser_cfg = config.get("browser", {})
        self._browser = BrowserController(
            profile_dir=self._profile_dir,
            headless=browser_cfg.get("headless", False),
            channel=browser_cfg.get("channel", "chrome"),
            incognito=browser_cfg.get("incognito", True),
            clear_storage_on_open=browser_cfg.get("clear_storage_on_open", True),
            profile_subdir=browser_cfg.get("profile_subdir", "chrome"),
        )
        self._orchestrator: Orchestrator | None = None
        self._is_collecting = False
        self._rest_after_id: str | None = None
        self._rest_end_time: float | None = None

        self.title("半手動リスト収集ツール")
        self.geometry("680x840")
        self.minsize(640, 740)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        json_count = sum(1 for a in self._adapters if a.__class__.__name__ == "JsonSiteAdapter")
        if json_count:
            self._append_log(f"sites.json から {json_count} 件のサイトを読み込みました。")

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}

        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=12, pady=12)

        ctk.CTkLabel(top, text="収集サイト:").grid(row=0, column=0, sticky="w", **pad)
        names = list(self._adapter_map.keys())
        self._site_var = ctk.StringVar(value=names[0] if names else "")
        self._site_combo = ctk.CTkComboBox(top, values=names, variable=self._site_var, width=280)
        self._site_combo.grid(row=0, column=1, sticky="w", **pad)

        ctk.CTkLabel(top, text="取得件数:").grid(row=1, column=0, sticky="w", **pad)
        self._limit_entry = ctk.CTkEntry(top, width=120)
        self._limit_entry.insert(0, "0")
        self._limit_entry.grid(row=1, column=1, sticky="w", **pad)
        ctk.CTkLabel(top, text="（0 = 上限なし）", text_color="gray").grid(row=1, column=2, sticky="w")

        ctk.CTkLabel(top, text="ペース:").grid(row=2, column=0, sticky="w", **pad)
        pace_frame = ctk.CTkFrame(top, fg_color="transparent")
        pace_frame.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        self._pace_var = ctk.StringVar(value="標準")
        for label in self.PACE_OPTIONS:
            ctk.CTkRadioButton(pace_frame, text=label, variable=self._pace_var, value=label).pack(
                side="left", padx=8
            )

        self._area_enabled_var = ctk.BooleanVar(value=False)
        self._area_check = ctk.CTkCheckBox(
            top,
            text="募集地で住所を絞り込む（任意）",
            variable=self._area_enabled_var,
            command=self._on_area_toggle,
        )
        self._area_check.grid(row=3, column=0, columnspan=3, sticky="w", **pad)

        area_frame = ctk.CTkFrame(top, fg_color="transparent")
        area_frame.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        ctk.CTkLabel(area_frame, text="県:").pack(side="left", padx=(0, 6))
        self._pref_combo = ctk.CTkComboBox(
            area_frame,
            values=prefecture_choices(),
            width=140,
            command=self._on_pref_change,
        )
        self._pref_combo.set(PREF_NONE)
        self._pref_combo.pack(side="left")
        ctk.CTkLabel(area_frame, text="市:").pack(side="left", padx=(12, 6))
        self._city_combo = ctk.CTkComboBox(area_frame, values=city_choices(""), width=180)
        self._city_combo.set(CITY_NONE)
        self._city_combo.pack(side="left")
        ctk.CTkLabel(
            top,
            text="有効時は、本社所在地・所在地・住所に選択した県・市が含まれるものだけをCSVに保存します。町村は手入力もできます。",
            text_color="gray",
            wraplength=520,
            justify="left",
            anchor="w",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 8))
        self._on_area_toggle()

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=4)
        self._open_btn = ctk.CTkButton(btn_frame, text="開く", width=100, command=self._on_open)
        self._open_btn.pack(side="left", padx=6)
        self._collect_btn = ctk.CTkButton(btn_frame, text="収集開始", width=100, command=self._on_collect)
        self._collect_btn.pack(side="left", padx=6)
        self._stop_btn = ctk.CTkButton(
            btn_frame, text="停止", width=100, command=self._on_stop, state="disabled"
        )
        self._stop_btn.pack(side="left", padx=6)

        self._progress_label = ctk.CTkLabel(self, text="待機中")
        self._progress_label.pack(anchor="w", padx=16, pady=(8, 0))

        self._rest_label = ctk.CTkLabel(self, text="", text_color="#f59e0b")
        self._rest_label.pack(anchor="w", padx=16, pady=(2, 0))

        self._browser_status_label = ctk.CTkLabel(self, text="ブラウザ: 未接続", text_color="gray")
        self._browser_status_label.pack(anchor="w", padx=16, pady=(2, 0))

        ctk.CTkLabel(self, text="ログ").pack(anchor="w", padx=16, pady=(8, 0))
        self._log_box = ctk.CTkTextbox(self, height=180)
        self._log_box.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        self._log_box.configure(state="disabled")

        notice_frame = ctk.CTkFrame(self)
        notice_frame.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(
            notice_frame,
            text="注意事項",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#b45309",
        ).pack(anchor="w", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            notice_frame,
            text=(
                "短時間に大量のアクセスを行うと、アクセス先のサイト側でアクセス制限やブロックされる恐れがあります。\n"
                "なお、本機能のアクセスには、ご利用者様ご自身のIPアドレスが使用されます。"
                "そのため、過度なアクセスを行った場合、該当サイトへ通常通りアクセスできなくなる可能性があります。\n"
                "本機能のご利用により発生したアクセス制限、ブロック、その他のトラブルにつきましては、"
                "ツール制作者側では責任を負いかねますので、予めご了承ください。\n"
                "ご利用の際は、ご利用者様ご自身の責任において、アクセス先サイトに過度な負荷をかけないよう、"
                "節度を持ってご利用くださいますようお願いいたします。"
            ),
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            justify="left",
            wraplength=620,
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 12))

        self._append_log("半手動リスト収集ツールを起動しました。")

    def _append_log(self, message: str) -> None:
        line = timestamp_log(message) + "\n"

        def update() -> None:
            self._log_box.configure(state="normal")
            self._log_box.insert("end", line)
            self._log_box.see("end")
            self._log_box.configure(state="disabled")

        self.after(0, update)

    def _start_rest_countdown(self, total_sec: float) -> None:
        self._stop_rest_countdown()
        self._rest_end_time = time.monotonic() + max(0.0, total_sec)
        self._tick_rest_countdown()

    def _tick_rest_countdown(self) -> None:
        if self._rest_end_time is None:
            return

        remaining = self._rest_end_time - time.monotonic()
        if remaining <= 0:
            self._stop_rest_countdown()
            return

        secs = max(1, math.ceil(remaining))
        self._rest_label.configure(text=f"休憩中: あと {secs} 秒")
        self._rest_after_id = self.after(200, self._tick_rest_countdown)

    def _stop_rest_countdown(self) -> None:
        if self._rest_after_id is not None:
            try:
                self.after_cancel(self._rest_after_id)
            except Exception:
                pass
            self._rest_after_id = None
        self._rest_end_time = None
        self._rest_label.configure(text="")

    def _set_browser_status(self, text: str, color: str = "gray") -> None:
        self._browser_status_label.configure(text=text, text_color=color)

    def _update_progress(self, phase: str, current: int, total: int | None) -> None:
        def update() -> None:
            if phase == "idle":
                text = "待機中"
            elif phase == "scan":
                text = f"一覧スキャン: {current} 件検出"
            elif total is None:
                text = f"詳細取得: {current} / —"
            else:
                text = f"詳細取得: {current} / {total}"
            self._progress_label.configure(text=text)

        self.after(0, update)

    def _set_collecting(self, collecting: bool) -> None:
        def update() -> None:
            self._is_collecting = collecting
            state_open = "disabled" if collecting else "normal"
            state_collect = "disabled" if collecting else "normal"
            state_stop = "normal" if collecting else "disabled"
            self._open_btn.configure(state=state_open)
            self._collect_btn.configure(state=state_collect)
            self._stop_btn.configure(state=state_stop)
            self._area_check.configure(state=state_open)
            if collecting:
                self._pref_combo.configure(state="disabled")
                self._city_combo.configure(state="disabled")
            else:
                self._on_area_toggle()

        self.after(0, update)

    def _on_area_toggle(self) -> None:
        enabled = bool(self._area_enabled_var.get()) and not self._is_collecting
        state = "normal" if enabled else "disabled"
        self._pref_combo.configure(state=state)
        self._city_combo.configure(state=state)

    def _on_pref_change(self, value: str = "") -> None:
        prefecture = value or self._pref_combo.get()
        choices = city_choices(prefecture)
        self._city_combo.configure(values=choices)
        self._city_combo.set(CITY_NONE)

    def _get_recruitment_area(self) -> dict[str, str] | None:
        """有効時は募集地フィルタ条件。無効時は None。入力不足なら空 dict。"""
        if not self._area_enabled_var.get():
            return None
        prefecture = self._pref_combo.get().strip()
        city = self._city_combo.get().strip()
        if is_placeholder(prefecture) or is_placeholder(city):
            return {}
        return {"募集県": prefecture, "募集市": city}

    def _selected_adapter(self) -> SiteAdapter | None:
        return self._adapter_map.get(self._site_var.get())

    def _parse_limit(self) -> int:
        try:
            return max(0, int(self._limit_entry.get().strip() or "0"))
        except ValueError:
            return 0

    def _pace_multiplier(self) -> float:
        pace_key = self.PACE_OPTIONS.get(self._pace_var.get(), "normal")
        return float(self._config.get("pace_multipliers", {}).get(pace_key, 1.0))

    def _make_governor(self) -> Governor:
        gov = self._config.get("governor", {})
        return Governor(
            batch_min=int(gov.get("batch_min", 1)),
            batch_max=int(gov.get("batch_max", 5)),
            rest_min_sec=float(gov.get("rest_min_sec", 30)),
            rest_max_sec=float(gov.get("rest_max_sec", 300)),
            page_delay_min_sec=float(gov.get("page_delay_min_sec", 1.0)),
            page_delay_max_sec=float(gov.get("page_delay_max_sec", 3.0)),
            pace_multiplier=self._pace_multiplier(),
        )

    def _on_open(self) -> None:
        adapter = self._selected_adapter()
        if not adapter:
            messagebox.showerror("エラー", "収集サイトが選択されていません。")
            return
        try:
            page, mode = self._browser.open_site(adapter.top_url)
            page = self._browser.pick_page(getattr(adapter, "list_url_hint", "")) or page
            display_url = page.url if len(page.url) <= 60 else page.url[:60] + "…"
            self._set_browser_status(f"ブラウザ: 接続中 — {display_url}", "#22c55e")
            self._append_log(f"{mode}: {adapter.display_name}")
            if adapter.supports_collection():
                self._append_log("同じ Chrome（黄色い帯が出ているウィンドウ）で地域・キーワードを検索し、一覧が出たら [収集開始] を押してください。")
                if "session/destroy" in page.url:
                    self._append_log(
                        "⚠ ログアウト画面のままです。このウィンドウのアドレスバーが job_search になるまで検索してください。"
                    )
            else:
                self._append_log("このサイトは [開く] による接続確認のみ可能です。")
        except Exception as exc:
            self._set_browser_status("ブラウザ: エラー", "#ef4444")
            messagebox.showerror("ブラウザ起動エラー", str(exc))
            self._append_log(f"ブラウザ起動に失敗しました: {exc}")

    def _on_collect(self) -> None:
        if self._is_collecting:
            return

        adapter = self._selected_adapter()
        if not adapter:
            messagebox.showerror("エラー", "収集サイトが選択されていません。")
            return

        if not adapter.supports_collection():
            messagebox.showinfo(
                "接続テスト用サイト",
                f"「{adapter.display_name}」は [開く] による接続確認のみ可能です。\n"
                "収集できるサイトはドロップダウンから選んでください。",
            )
            return

        page = self._browser.pick_page(getattr(adapter, "list_url_hint", ""))
        if page is None:
            messagebox.showwarning("確認", "先に [開く] でブラウザを起動してください。")
            return

        recruitment_area = self._get_recruitment_area()
        if recruitment_area is not None and not recruitment_area:
            messagebox.showwarning(
                "確認",
                "募集地フィルタを有効にしています。県と市を選んでから収集を開始してください。\n"
                "リストにない町村は、市の欄に直接入力できます。",
            )
            return

        limit = self._parse_limit()
        self._is_collecting = True
        self._set_collecting(True)
        self._append_log("収集を開始します…")
        tab_urls = self._browser.tab_urls()
        if tab_urls:
            self._append_log("開いているタブ: " + " | ".join(tab_urls))
        try:
            page.bring_to_front()
        except Exception:
            pass
        self._append_log(f"収集対象タブ: {page.url}")
        self._run_collect(adapter, limit, recruitment_area)

    def _run_collect(
        self,
        adapter: SiteAdapter,
        limit: int,
        recruitment_area: dict[str, str] | None = None,
    ) -> None:
        page = self._browser.pick_page(getattr(adapter, "list_url_hint", "")) or self._browser.page
        if page is None:
            self._append_log("ブラウザが閉じられています。再度 [開く] してください。")
            self._is_collecting = False
            self._set_collecting(False)
            return

        scheduler = TkScheduler(self)

        def on_finish(csv_path: Path | None, count: int) -> None:
            stopped = orchestrator.is_stopped()
            if csv_path and count > 0:
                self._append_log(f"収集が完了しました（{count} 件）。")
            elif not stopped:
                self._append_log("収集を終了しました（データなしまたはエラー）。")
            self._orchestrator = None
            self._is_collecting = False
            self._set_collecting(False)
            self._stop_rest_countdown()
            self._update_progress("idle", 0, None)

        orchestrator = Orchestrator(
            adapter=adapter,
            page=page,
            output_dir=self._output_dir,
            governor=self._make_governor(),
            max_items=limit,
            log_callback=self._append_log,
            progress_callback=self._update_progress,
            finish_callback=on_finish,
            rest_start_callback=self._start_rest_countdown,
            rest_end_callback=self._stop_rest_countdown,
            flush_every=int(self._config.get("csv_flush_every", 5)),
            scheduler=scheduler,
            company_filter=CompanyFilter.from_config(self._config),
            recruitment_area=recruitment_area,
        )
        self._orchestrator = orchestrator
        try:
            orchestrator.start()
        except Exception as exc:
            self._append_log(f"収集中にエラーが発生しました: {exc}")
            messagebox.showerror("収集エラー", str(exc))
            self._orchestrator = None
            self._is_collecting = False
            self._set_collecting(False)

    def _on_stop(self) -> None:
        if self._orchestrator:
            self._orchestrator.request_stop()
            self._stop_rest_countdown()
            self._append_log("停止を要求しました…")

    def _on_close(self) -> None:
        self._stop_rest_countdown()
        if self._orchestrator:
            self._orchestrator.request_stop()
        self._browser.close()
        self.destroy()
