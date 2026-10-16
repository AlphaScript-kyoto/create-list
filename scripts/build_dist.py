"""Python の無い PC 向けに exe 版（dist/ListCollector）と配布用 zip を作る。

使い方:
  py -3 scripts/build_dist.py
  py -3 scripts/build_dist.py --without-known-list
  py -3 scripts/build_dist.py --without-output
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist" / "ListCollector"
KNOWN_DIR_NAME = "現状のmplist"
KNOWN_CSV_NAME = "実装済みリスト.csv"


def run_pyinstaller() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "ListCollector.spec"],
        cwd=PROJECT_ROOT,
        check=True,
    )


def copy_settings() -> None:
    for name in ("config.json", "sites.json"):
        shutil.copy2(PROJECT_ROOT / name, DIST_DIR / name)


def remove_output(dist_dir: Path) -> None:
    """配布 zip に output を入れない。現場の収集結果・実装済みリストを残すため。"""
    output_dir = dist_dir / "output"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    print("output フォルダは zip に含めません（現場の output はそのまま残してください）。")


def copy_known_list(include: bool) -> None:
    target_dir = DIST_DIR / "output" / KNOWN_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    if not include:
        print("実装済みリストは同梱しません（配布先で output/現状のmplist に置いてください）。")
        return

    source_dir = PROJECT_ROOT / "output" / KNOWN_DIR_NAME
    source_csv = source_dir / KNOWN_CSV_NAME
    if not source_csv.is_file():
        print(f"実装済みリストが見つからないため同梱しません: {source_csv}")
        return

    # 索引ファイルも一緒にコピーすると、配布先の初回読み込みが速い
    for path in source_dir.glob(KNOWN_CSV_NAME + "*"):
        if path.suffix == ".tmp":
            continue
        shutil.copy2(path, target_dir / path.name)
        print(f"同梱: {path.name}")


def make_zip(dist_dir: Path = DIST_DIR) -> Path:
    archive = shutil.make_archive(str(dist_dir), "zip", root_dir=dist_dir.parent, base_dir=dist_dir.name)
    return Path(archive)


def zip_contains_output(archive: Path) -> bool:
    prefix = f"{DIST_DIR.name}/output/"
    with zipfile.ZipFile(archive) as bundle:
        return any(name.replace("\\", "/").startswith(prefix) for name in bundle.namelist())


def main() -> None:
    args = set(sys.argv[1:])
    without_output = "--without-output" in args
    include_known = "--without-known-list" not in args and not without_output
    run_pyinstaller()
    copy_settings()
    if without_output:
        remove_output(DIST_DIR)
    else:
        copy_known_list(include_known)
    archive = make_zip(DIST_DIR)
    if without_output and zip_contains_output(archive):
        raise SystemExit("output フォルダが zip に含まれています。配布を中止しました。")
    print()
    print(f"exe フォルダ: {DIST_DIR}")
    print(f"配布用 zip : {archive}")
    if without_output:
        print("この zip に output はありません。現場では、いまのフォルダの上に展開してください。")


if __name__ == "__main__":
    main()
