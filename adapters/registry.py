"""ドロップダウン用アダプタレジストリ。"""

from __future__ import annotations

from pathlib import Path

from adapters.baitoru import BaitoruAdapter
from adapters.base import SiteAdapter
from adapters.ekiten import EkitenAdapter
from adapters.rikunabi_next import RikunabiNextAdapter
from adapters.site_loader import load_sites_from_json
from adapters.tabelog import TabelogAdapter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SITES_FILE = _PROJECT_ROOT / "sites.json"

# Python で実装した組み込みアダプタ
_BUILTIN_ADAPTERS: list[type[SiteAdapter]] = [
    RikunabiNextAdapter,
    BaitoruAdapter,
    TabelogAdapter,
    EkitenAdapter,
]


def get_all_adapters(sites_file: Path | None = None) -> list[SiteAdapter]:
    """組み込み + sites.json のアダプタ一覧。"""
    adapters: list[SiteAdapter] = [cls() for cls in _BUILTIN_ADAPTERS]
    known_ids = {a.site_id for a in adapters}

    json_path = sites_file or _DEFAULT_SITES_FILE
    for json_adapter in load_sites_from_json(json_path):
        if json_adapter.site_id in known_ids:
            continue
        adapters.append(json_adapter)
        known_ids.add(json_adapter.site_id)

    return adapters


def get_adapter_by_id(site_id: str, sites_file: Path | None = None) -> SiteAdapter | None:
    for adapter in get_all_adapters(sites_file):
        if adapter.site_id == site_id:
            return adapter
    return None
