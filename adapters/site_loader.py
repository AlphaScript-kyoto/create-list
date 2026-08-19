"""sites.json の読み込みとアダプタ生成。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from adapters.json_site import JsonSiteAdapter

logger = logging.getLogger("list_collector")


def load_sites_from_json(path: Path) -> list[JsonSiteAdapter]:
    """sites.json から有効なサイトアダプタ一覧を返す。"""
    if not path.exists():
        logger.info("sites.json が見つかりません: %s", path)
        return []

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("sites.json の JSON が不正です: %s", exc)
        return []

    raw_sites = data.get("sites", [])
    if not isinstance(raw_sites, list):
        logger.error("sites.json: 'sites' は配列である必要があります")
        return []

    adapters: list[JsonSiteAdapter] = []
    seen_ids: set[str] = set()

    for index, entry in enumerate(raw_sites, start=1):
        if not isinstance(entry, dict):
            logger.warning("sites.json: %d 番目のエントリをスキップ（オブジェクトではない）", index)
            continue

        if not entry.get("enabled", True):
            continue

        site_id = str(entry.get("site_id", "")).strip()
        display_name = str(entry.get("display_name", "")).strip()
        top_url = str(entry.get("top_url", "")).strip()

        if not site_id or not display_name or not top_url:
            logger.warning(
                "sites.json: %d 番目をスキップ（site_id / display_name / top_url は必須）",
                index,
            )
            continue

        if site_id in seen_ids:
            logger.warning("sites.json: site_id '%s' が重複しているためスキップ", site_id)
            continue

        if not top_url.startswith(("http://", "https://", "file://")):
            logger.warning("sites.json: site_id '%s' の top_url が不正なためスキップ", site_id)
            continue

        seen_ids.add(site_id)
        adapters.append(JsonSiteAdapter(entry))

    return adapters
