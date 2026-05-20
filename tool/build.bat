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
python -m pip install --upgrade pyinstaller faster-whisper
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller or faster-whisper.
    exit /b 1
)

python -m pip install --upgrade nvidia-cublas-cu12 nvidia-cudnn-cu12
if errorlevel 1 (
    echo [ERROR] Failed to install CUDA runtime wheels.
    exit /b 1
)

for /f "delims=" %%i in ('python -c "import nvidia.cublas, os; print(os.path.dirname(nvidia.cublas.__file__))"') do set CUBLAS_DIR=%%i\bin
for /f "delims=" %%i in ('python -c "import nvidia.cudnn, os; print(os.path.dirname(nvidia.cudnn.__file__))"') do set CUDNN_DIR=%%i\bin
if not exist "%CUBLAS_DIR%" (
    echo [ERROR] cuBLAS wheel bin directory missing: %CUBLAS_DIR%
    exit /b 1
)
if not exist "%CUDNN_DIR%" (
    echo [ERROR] cuDNN wheel bin directory missing: %CUDNN_DIR%
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
    --collect-submodules tools ^
    --collect-submodules ctranslate2 ^
    --hidden-import faster_whisper ^
    --collect-data faster_whisper ^
    --copy-metadata faster-whisper ^
    --collect-submodules onnxruntime ^
    --collect-data onnxruntime ^
    a4071_tool.py
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

copy /Y ffmpeg.exe dist\ffmpeg.exe >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy ffmpeg.exe to dist\.
    exit /b 1
)

if not exist dist\cuda mkdir dist\cuda
copy /Y "%CUBLAS_DIR%\*.dll" dist\cuda\ >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy cuBLAS DLLs to dist\cuda\.
    exit /b 1
)
copy /Y "%CUDNN_DIR%\*.dll" dist\cuda\ >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy cuDNN DLLs to dist\cuda\.
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete: dist\A4071-Tool.exe + dist\ffmpeg.exe + dist\cuda\
echo  Distribute the entire dist\ folder. ffmpeg.exe and cuda\ must
echo  stay alongside A4071-Tool.exe.
echo ============================================================
endlocal
