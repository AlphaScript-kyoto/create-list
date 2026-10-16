# PyInstaller 設定: build_exe.bat から使う（Python の無い PC 向け配布版）
from PyInstaller.utils.hooks import collect_all

datas = [("config.json", "."), ("sites.json", ".")]
binaries = []
hiddenimports = []

# Playwright は Node 製のドライバ一式、customtkinter はテーマ JSON が必要
for package in ("playwright", "customtkinter"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ListCollector",
    console=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ListCollector",
    upx=False,
)
