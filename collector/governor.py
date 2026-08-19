"""負荷制御 — 詳細をランダム件数処理したあと、ランダム秒数で休憩する。"""

from __future__ import annotations

import random
from typing import Callable


class Governor:
    """相手サーバー負荷を抑えるための休憩・ページ間待機。

    - 詳細ページを batch_min〜batch_max 件（毎回ランダム）処理したら休憩
    - 休憩秒数は rest_min〜rest_max を毎回ランダム（ペース倍率は休憩のみ）
    - 一覧・詳細の遷移ごとに page_delay_min〜page_delay_max 秒（倍率なし）
    """

    def __init__(
        self,
        batch_min: int = 1,
        batch_max: int = 5,
        rest_min_sec: float = 30,
        rest_max_sec: float = 300,
        page_delay_min_sec: float = 1.0,
        page_delay_max_sec: float = 3.0,
        pace_multiplier: float = 1.0,
    ) -> None:
        if batch_min < 1 or batch_max < batch_min:
            raise ValueError("batch_min / batch_max が不正です")
        if rest_min_sec < 0 or rest_max_sec < rest_min_sec:
            raise ValueError("rest_min_sec / rest_max_sec が不正です")
        if page_delay_min_sec < 0 or page_delay_max_sec < page_delay_min_sec:
            raise ValueError("page_delay_min_sec / page_delay_max_sec が不正です")

        self._batch_min = batch_min
        self._batch_max = batch_max
        # ペース倍率は休憩秒数にだけかける（件数の幅は変えない）
        self._rest_min = rest_min_sec * pace_multiplier
        self._rest_max = rest_max_sec * pace_multiplier
        self._page_delay_min = page_delay_min_sec
        self._page_delay_max = page_delay_max_sec
        self._processed_in_batch = 0
        self._batch_limit = self._roll_batch_size()

    def _roll_batch_size(self) -> int:
        return random.randint(self._batch_min, self._batch_max)

    def sample_page_delay(self) -> float:
        """一覧・詳細の遷移ごとに使う短いランダム待機（倍率なし）。"""
        return random.uniform(self._page_delay_min, self._page_delay_max)

    def sample_rest_seconds(self) -> float:
        """休憩秒数を毎回ランダムに決める（ペース倍率済み）。"""
        return random.uniform(self._rest_min, self._rest_max)

    def after_item(self, stop_check: Callable[[], bool] | None = None) -> tuple[bool, float | None]:
        """詳細 1 件処理後。バッチ上限なら休憩秒数を返す。

        Returns:
            (継続可否, 休憩秒数 or None)
        """
        self._processed_in_batch += 1
        if self._processed_in_batch < self._batch_limit:
            return True, None

        rest_sec = self.sample_rest_seconds()
        if stop_check and stop_check():
            return False, rest_sec

        self._processed_in_batch = 0
        self._batch_limit = self._roll_batch_size()
        return True, rest_sec
