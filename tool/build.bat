@echo off
setlocal

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/windows/
    echo Make sure to check "Add python.exe to PATH" during install.
    exit /b 1
)

if not exist ffmpeg.exe (
    echo [ERROR] ffmpeg.exe not found next to build.bat.
    echo Download a static Windows build from:
    echo   https://www.gyan.dev/ffmpeg/builds/
    echo Extract bin\ffmpeg.exe and place it next to build.bat, then rerun.
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist A4071-Tool.spec del /q A4071-Tool.spec

python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name A4071-Tool ^
    --add-binary "ffmpeg.exe;." ^
    --collect-submodules tools ^
    a4071_tool.py
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete: dist\A4071-Tool.exe
echo  ffmpeg.exe is embedded. Distribute the single .exe file.
echo ============================================================
endlocal
