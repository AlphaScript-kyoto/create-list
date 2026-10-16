@echo off
chcp 65001 > nul
cd /d "%~dp0"

rem 現場の output（実装済みリスト・収集CSV）を上書きしない更新用 zip を作る
call "%~dp0build_exe.bat" --without-output
