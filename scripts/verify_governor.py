"""Governor の乱数範囲・ペース倍率を検証する（実待機はしない）。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from collector.governor import Governor


def _assert_in(name: str, value: float, low: float, high: float) -> None:
    if not (low <= value <= high):
        raise SystemExit(f"{name} が範囲外: {value} (期待 {low}〜{high})")


def main() -> None:
    samples = 200

    gov = Governor(
        batch_min=1,
        batch_max=5,
        rest_min_sec=30,
        rest_max_sec=300,
        page_delay_min_sec=1.0,
        page_delay_max_sec=3.0,
        pace_multiplier=1.0,
        skip_rest_min_sec=5,
        skip_rest_max_sec=20,
    )

    batch_sizes: set[int] = set()
    rest_count = 0
    for _ in range(samples):
        delay = gov.sample_page_delay()
        _assert_in("page_delay", delay, 1.0, 3.0)

        continued, rest = gov.after_item()
        if not continued:
            raise SystemExit("after_item が停止を返した")
        if rest is None:
            continue
        rest_count += 1
        _assert_in("rest(normal)", rest, 30.0, 300.0)
        batch_sizes.add(gov._batch_limit)

    if rest_count < 10:
        raise SystemExit(f"休憩回数が少なすぎます: {rest_count}")

    skip_gov = Governor(
        batch_min=1,
        batch_max=5,
        skip_rest_min_sec=5,
        skip_rest_max_sec=20,
        pace_multiplier=1.0,
    )
    skip_rest_count = 0
    for _ in range(samples):
        continued, rest = skip_gov.after_light_skip()
        if not continued:
            raise SystemExit("after_light_skip が停止を返した")
        if rest is None:
            continue
        skip_rest_count += 1
        _assert_in("rest(light skip)", rest, 5.0, 20.0)

    if skip_rest_count < 10:
        raise SystemExit(f"短い休憩回数が少なすぎます: {skip_rest_count}")

    safe = Governor(pace_multiplier=1.5)
    fast = Governor(pace_multiplier=0.7)
    for _ in range(50):
        _assert_in("rest(safe)", safe.sample_rest_seconds(), 45.0, 450.0)
        _assert_in("rest(fast)", fast.sample_rest_seconds(), 21.0, 210.0)
        _assert_in("skip(safe)", safe.sample_skip_rest_seconds(), 7.5, 30.0)
        _assert_in("skip(fast)", fast.sample_skip_rest_seconds(), 3.5, 14.0)
        _assert_in("batch", float(safe._roll_batch_size()), 1, 5)
        _assert_in("batch", float(fast._roll_batch_size()), 1, 5)

    print(f"OK: 通常休憩 {rest_count} 回 / 短い休憩 {skip_rest_count} 回 / 次バッチ幅 {sorted(batch_sizes)}")
    print("OK: 安全 45〜450 / 標準 30〜300 / 速め 21〜210 / 短い 5〜20 / 件数 1〜5")


if __name__ == "__main__":
    main()
