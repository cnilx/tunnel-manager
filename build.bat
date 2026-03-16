@echo off
cd /d "%~dp0"
echo Building...
.venv\Scripts\pyinstaller --onefile --noconsole --name SSHTunnelManager source\tunnel_gui.py --clean
if exist release\SSHTunnelManager.exe del /f release\SSHTunnelManager.exe
move dist\SSHTunnelManager.exe release\
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist SSHTunnelManager.spec del /f SSHTunnelManager.spec
echo.
echo Build completed!
echo Release file: release\SSHTunnelManager.exe
echo.
pause
