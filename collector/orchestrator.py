"""収集フロー全体の制御（段階実行で UI をブロックしない）。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Callable

from adapters.base import SiteAdapter
from collector.csv_schema import (
    COMMON_COLUMNS,
    clean_contact_name,
    company_key,
    csv_skip_reason,
    normalize_text,
)
from collector.csv_writer import CsvWriter
from collector.governor import Governor
from collector.jp_phone import format_jp_phone
from collector.known_list import KnownList

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[str, int, int | None], None]
FinishCallback = Callable[[Path | None, int], None]
RestStartCallback = Callable[[float], None]
RestEndCallback = Callable[[], None]


class StepScheduler(ABC):
    @abstractmethod
    def schedule(self, callback: Callable[[], None], delay_sec: float = 0) -> None:
        """delay_sec 後に callback を実行する。"""

    @abstractmethod
    def cancel_all(self) -> None:
        """未実行の予約をキャンセルする。"""


class ImmediateScheduler(StepScheduler):
    """CLI 検証用 — 同期的に即実行。"""

    def schedule(self, callback: Callable[[], None], delay_sec: float = 0) -> None:
        import time

        if delay_sec > 0:
            time.sleep(delay_sec)
        callback()

    def cancel_all(self) -> None:
        pass


class Orchestrator:
    def __init__(
        self,
        adapter: SiteAdapter,
        page,
        output_dir: Path,
        governor: Governor,
        max_items: int = 0,
        log_callback: LogCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        finish_callback: FinishCallback | None = None,
        rest_start_callback: RestStartCallback | None = None,
        rest_end_callback: RestEndCallback | None = None,
        flush_every: int = 5,
        scheduler: StepScheduler | None = None,
        company_filter=None,
        recruitment_areas: list[dict[str, str]] | None = None,
        known_list_path: Path | None = None,
        csv_rows_per_file: int = 0,
    ) -> None:
        self._adapter = adapter
        self._page = page
        self._output_dir = output_dir
        self._governor = governor
        self._max_items = max(0, max_items)
        self._log = log_callback or (lambda _msg: None)
        self._progress = progress_callback or (lambda _phase, _cur, _total: None)
        self._finish_callback = finish_callback
        self._rest_start = rest_start_callback or (lambda _sec: None)
        self._rest_end = rest_end_callback or (lambda: None)
        self._flush_every = flush_every
        self._csv_rows_per_file = max(0, int(csv_rows_per_file))
        self._scheduler = scheduler or ImmediateScheduler()
        self._company_filter = company_filter
        self._recruitment_areas = list(recruitment_areas or [])
        self._known_list_path = Path(known_list_path) if known_list_path else None
        self._known_list: KnownList | None = None
        if company_filter is not None:
            self._adapter.set_company_filter(company_filter)

        self._stopped = False
        self._collected = 0
        self._urls: list[str] = []
        self._seen: set[str] = set()
        self._scan_page_num = 1
        self._detail_index = 0
        self._writer: CsvWriter | None = None
        self._csv_path: Path | None = None
        self._url_cache_path: Path | None = None
        self._seen_companies: set[tuple[str, str]] = set()
        self._skipped_duplicates = 0
        self._skipped_filtered = 0
        self._skipped_known = 0
        self._appended_known = 0

    @property
    def collected_count(self) -> int:
        return self._collected

    @property
    def cached_url_count(self) -> int:
        return len(self._urls)

    def request_stop(self) -> None:
        self._stopped = True
        self._rest_end()
        self._scheduler.cancel_all()

    def is_stopped(self) -> bool:
        return self._stopped

    def start(self) -> None:
        """非同期収集を開始（GUI 向け）。"""
        self._scheduler.schedule(self._phase_prepare, 0)

    def _phase_prepare(self) -> None:
        if self._stopped:
            self._finish(None)
            return
        if self._known_list_path:
            self._log(
                "実装済みリストを読み込みます。"
                "初回は索引づくりで少し時間がかかることがあります…"
            )
            self._scheduler.schedule(self._phase_load_known, 0.15)
            return
        self._phase_validate()

    def _phase_load_known(self) -> None:
        if self._stopped:
            self._finish(None)
            return
        try:
            assert self._known_list_path is not None
            self._known_list = KnownList.load(self._known_list_path, log=self._log)
            self._log(
                f"実装済みリスト: 電話 {self._known_list.phone_count:,} 件と照合します"
            )
        except Exception as exc:
            self._known_list = None
            self._log(f"実装済みリストを読めませんでした（照合なしで続行）: {exc}")
        self._phase_validate()

    def run(self) -> Path | None:
        """同期収集（CLI 検証向け）。"""
        result: list[Path | None] = [None]

        def on_finish(path: Path | None, _count: int) -> None:
            result[0] = path

        self._finish_callback = on_finish
        self.start()

        # ImmediateScheduler なら start() 内ですべて完了している
        return result[0]

    def _phase_validate(self) -> None:
        logger = logging.getLogger("list_collector")
        if self._stopped:
            self._finish(None)
            return

        if not self._adapter.validate_list_page(self._page):
            hint = self._adapter.explain_list_page_failure(self._page)
            msg = (
                "現在のページは収集可能な一覧ページではありません。"
                f"（現在の URL: {self._page.url}）"
            )
            self._log(msg)
            if hint:
                self._log(hint)
            logger.warning(msg)
            self._finish(None)
            return

        self._urls = []
        self._seen = set()
        self._scan_page_num = 1
        self._log("【フェーズ1】検索結果一覧をスキャンして件数をカウントします…")
        self._progress("scan", 0, None)
        self._phase_scan_list_page()

    def _phase_scan_list_page(self) -> None:
        if self._stopped:
            self._log("収集を停止しました（一覧スキャン中）。")
            self._finish(None)
            return

        links = self._adapter.extract_list_links(self._page)
        new_count = 0
        for url in links:
            if url not in self._seen:
                self._seen.add(url)
                self._urls.append(url)
                new_count += 1

        skipped_list = int(getattr(self._adapter, "last_list_skip_count", 0) or 0)
        skip_note = f" / 一覧除外 {skipped_list} 件" if skipped_list else ""
        self._log(
            f"  一覧 {self._scan_page_num} ページ目: +{new_count} 件"
            f"（このページ {len(links)} 件 / 累計 {len(self._urls)} 件{skip_note}）"
        )
        self._progress("scan", len(self._urls), None)

        if self._max_items > 0 and len(self._urls) >= self._url_scan_limit():
            self._urls = self._urls[: self._url_scan_limit()]
            self._complete_scan_phase()
            return

        if new_count == 0 and self._scan_page_num > 1:
            self._log("  新しい求人リンクが増えなかったため、一覧スキャンを終了します。")
            self._complete_scan_phase()
            return

        if self._adapter.go_next_page(self._page):
            self._scan_page_num += 1
            delay = self._governor.sample_page_delay()
            self._scheduler.schedule(self._phase_scan_list_page, delay)
            return

        if self._scan_page_num == 1:
            self._log("  次の一覧ページへ進めなかったため、このページだけでスキャンを終了します。")
        self._complete_scan_phase()

    def _complete_scan_phase(self) -> None:
        if not self._urls:
            self._log("収集対象のリンクが見つかりませんでした。")
            self._finish(None)
            return

        self._url_cache_path = self._save_url_cache()
        self._log(f"【フェーズ1 完了】合計 {len(self._urls)} 件の URL をキャッシュしました。")
        self._log(f"  キャッシュファイル: {self._url_cache_path}")
        self._log(f"【フェーズ2】{len(self._urls)} 件の詳細情報を取得します…")
        if self._max_items > 0:
            self._log(
                f"  取得件数はユニークな会社 {self._max_items} 件です。"
                "社名と本社所在地（住所）が同じ会社は 1 件にまとめます。"
            )
        if self._company_filter and self._company_filter.enabled:
            emp = self._company_filter.max_employees
            emp_txt = f"{emp}人" if emp is not None else "なし"
            self._log(
                f"  中小フィルタ: 従業員数上限 {emp_txt}、"
                f"会社名キーワード {len(self._company_filter.exclude_name_keywords)} 件"
            )
        if self._recruitment_areas:
            labels = [
                f"{area.get('募集県', '')} {area.get('募集市', '')}".strip()
                for area in self._recruitment_areas
            ]
            self._log(f"  住所フィルタ: {' / '.join(labels)} のいずれかに一致するものだけ保存します")
        self._log("  CSV ルール: 090/080/070 は出さない／実装済みは出さない（全サイト共通）")
        if self._csv_rows_per_file > 0:
            self._log(f"  CSV 区切り: {self._csv_rows_per_file} 件ごとにファイルを分けます")
        self._detail_index = 0
        self._collected = 0
        self._seen_companies = set()
        self._skipped_duplicates = 0
        self._skipped_filtered = 0
        self._skipped_known = 0
        self._appended_known = 0
        columns = self._adapter.csv_columns()
        extra_columns = self._adapter.extra_columns()
        if columns is None:
            columns = COMMON_COLUMNS + extra_columns
        self._writer = CsvWriter(
            self._output_dir,
            self._adapter.site_id,
            extra_columns=extra_columns,
            columns=columns,
            flush_every=self._flush_every,
            known_list=self._known_list,
            rows_per_file=self._csv_rows_per_file,
        )
        self._progress("detail", 0, len(self._urls))
        delay = self._governor.sample_page_delay()
        self._scheduler.schedule(self._phase_detail_next, delay)

    def _phase_detail_next(self) -> None:
        if self._stopped:
            self._log("収集を停止しました。")
            self._finish(self._csv_path)
            return

        if self._detail_index >= len(self._urls):
            self._finish(self._csv_path)
            return

        url = self._urls[self._detail_index]
        index = self._detail_index + 1
        skip_governor = False

        try:
            self._page.goto(url, wait_until="domcontentloaded")
            row = self._adapter.extract_detail(self._page, url)
            self._normalize_csv_fields(row)
            assert self._writer is not None
            name = (
                row.get("企業名")
                or row.get("社名")
                or row.get("会社名")
                or row.get("店名")
                or ""
            )
            skip_reason = row.pop("_skip_reason", "") or ""
            skip_governor = bool(row.pop("_skip_governor", ""))
            if not skip_reason and self._company_filter:
                skip_reason = self._company_filter.skip_reason(row) or ""
            if not skip_reason and self._recruitment_areas:
                if not self._match_recruitment_area(row):
                    skip_reason = "住所が指定した県・市と一致しない"
            if not skip_reason:
                skip_reason = csv_skip_reason(row, self._known_list)
            if skip_reason:
                if skip_reason == "実装済みリストに既出":
                    self._skipped_known += 1
                else:
                    self._skipped_filtered += 1
                self._log(f"[{index}/{len(self._urls)}] {name} をスキップ（{skip_reason}）")
                self._progress("detail", self._collected, self._progress_total())
            else:
                skip_governor = False
                dup_key = company_key(row)
                if dup_key and dup_key in self._seen_companies:
                    self._skipped_duplicates += 1
                    self._log(
                        f"[{index}/{len(self._urls)}] {name} は既出のためスキップ"
                        "（社名と住所が一致）"
                    )
                    self._progress("detail", self._collected, self._progress_total())
                else:
                    blocked = self._writer.append(row)
                    if blocked:
                        if blocked == "実装済みリストに既出":
                            self._skipped_known += 1
                        else:
                            self._skipped_filtered += 1
                        self._log(f"[{index}/{len(self._urls)}] {name} をスキップ（{blocked}）")
                        self._progress("detail", self._collected, self._progress_total())
                    else:
                        if dup_key:
                            self._seen_companies.add(dup_key)
                        self._collected += 1
                        if self._writer.last_closed_path:
                            self._log(f"CSV を区切って保存しました: {self._writer.last_closed_path}")
                        self._csv_path = self._writer.file_path
                        if self._known_list:
                            try:
                                self._known_list.append_new(row)
                                self._appended_known += 1
                            except Exception as exc:
                                self._log(f"実装済みリストへの追記に失敗: {exc}")
                        self._log(f"[{index}/{len(self._urls)}] {name}")
                        self._progress("detail", self._collected, self._progress_total())
        except Exception as exc:
            self._log(f"[{index}/{len(self._urls)}] エラー: {exc}")
            skip_governor = False

        self._detail_index = index

        reached_unique_limit = self._max_items > 0 and self._collected >= self._max_items
        if self._stopped or self._detail_index >= len(self._urls) or reached_unique_limit:
            if self._stopped:
                self._log("収集を停止しました。")
            elif reached_unique_limit:
                self._log(f"ユニークな会社が {self._collected} 件に達したので終了します。")
            self._finish(self._csv_path)
            return

        delay = self._governor.sample_page_delay()

        # 一覧除外の保険スキップなどは、相手サーバーへの負荷が小さいので休憩カウントに入れない
        if skip_governor:
            self._scheduler.schedule(self._phase_detail_next, delay)
            return

        continued, rest_sec = self._governor.after_item(lambda: self._stopped)
        if not continued:
            self._log("収集を停止しました（休憩中）。")
            self._finish(self._csv_path)
            return

        if rest_sec is not None:
            rest_int = max(1, round(rest_sec))
            self._log(f"[休憩] {rest_int}秒お休みします")
            self._rest_start(rest_sec)
            self._scheduler.schedule(self._resume_after_rest, rest_sec)
            return

        self._scheduler.schedule(self._phase_detail_next, delay)

    @staticmethod
    def _normalize_csv_fields(row: dict[str, str]) -> None:
        """CSV に書く直前に、電話のハイフンと「その他」担当者を整える。"""
        for key in ("電話番号", "企業代表番号", "専用電話番号"):
            if row.get(key):
                row[key] = format_jp_phone(row[key])
        if row.get("担当者名"):
            row["担当者名"] = clean_contact_name(row["担当者名"])

    def _resume_after_rest(self) -> None:
        if self._stopped:
            self._rest_end()
            self._finish(self._csv_path)
            return
        self._rest_end()
        self._log("[再開] 収集を再開します")
        self._phase_detail_next()

    def _url_scan_limit(self) -> int:
        """重複を見越して、取得件数より多めに URL を集める。"""
        if self._max_items <= 0:
            return 0
        return self._max_items * 3

    def _progress_total(self) -> int | None:
        if self._max_items > 0:
            return self._max_items
        return len(self._urls)

    def _match_recruitment_area(self, row: dict[str, str]) -> bool:
        """住所が、選んだ県・市（市未指定なら県だけ）のどれかと一致するか判定する。"""
        address = normalize_text(
            row.get("住所") or row.get("本社所在地") or row.get("所在地") or ""
        )
        if not address or not self._recruitment_areas:
            return False
        for area in self._recruitment_areas:
            pref = normalize_text(area.get("募集県", ""))
            city = normalize_text(area.get("募集市", ""))
            if not pref or pref not in address:
                continue
            if not city or city in address:
                return True
        return False

    def _save_url_cache(self) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._output_dir / f"{self._adapter.site_id}_urls_{timestamp}.txt"
        path.write_text("\n".join(self._urls), encoding="utf-8")
        return path

    def _finish(self, csv_path: Path | None) -> None:
        logger = logging.getLogger("list_collector")
        self._rest_end()
        csv_files: list[Path] = []
        if self._writer:
            self._writer.close()
            csv_files = list(self._writer.closed_paths)
            if self._writer.file_path:
                csv_path = self._writer.file_path
            self._writer = None

        if csv_path and self._collected > 0:
            if csv_files:
                for path in csv_files:
                    self._log(f"CSV を保存しました: {path}")
            else:
                self._log(f"CSV を保存しました: {csv_path}")
            if self._skipped_duplicates:
                self._log(
                    f"重複スキップ: {self._skipped_duplicates} 件"
                    "（社名と住所が同じ会社は 1 件のみ保存）"
                )
            if self._skipped_known:
                self._log(f"実装済みリストと重複: {self._skipped_known} 件")
            if self._appended_known:
                try:
                    assert self._known_list is not None
                    self._known_list.refresh_index()
                except Exception as exc:
                    self._log(f"実装済みリストの索引更新に失敗（次回読み込み時に作り直します）: {exc}")
                self._log(
                    f"実装済みリストに今回の新規 {self._appended_known} 件を追記しました"
                    f"（{self._known_list_path}）"
                )
            if self._skipped_filtered:
                self._log(f"フィルタでスキップ: {self._skipped_filtered} 件")
            logger.info("Saved CSV: %s", csv_path)
        elif csv_path is None and not self._stopped:
            pass

        if self._finish_callback:
            self._finish_callback(csv_path, self._collected)
