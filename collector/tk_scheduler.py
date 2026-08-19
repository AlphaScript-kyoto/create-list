"""Tkinter / CustomTkinter 向けの段階実行スケジューラ。"""

from __future__ import annotations

from typing import Callable

from collector.orchestrator import StepScheduler


class TkScheduler(StepScheduler):
    def __init__(self, app) -> None:
        self._app = app
        self._after_ids: list[str] = []

    def schedule(self, callback: Callable[[], None], delay_sec: float = 0) -> None:
        ms = max(1, int(delay_sec * 1000))
        after_id = self._app.after(ms, callback)
        self._after_ids.append(after_id)

    def cancel_all(self) -> None:
        for after_id in self._after_ids:
            try:
                self._app.after_cancel(after_id)
            except Exception:
                pass
        self._after_ids.clear()
