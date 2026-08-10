@echo off
setlocal enabledelayedexpansion
title Hive
cd /d "%~dp0"

echo.
echo   +==============================+
echo   :         H I V E              :
echo   +==============================+
echo.

:: ── Step 1: Find Python ──────────────────────────────────────────
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where python3 >nul 2>nul && set "PY=python3"
)
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)

if not defined PY (
    echo   [ERROR] Python 3 is not installed or not in PATH.
    echo   Download it from https://python.org
    echo.
    pause
    exit /b 1
)

:: Verify it's Python 3
for /f "tokens=*" %%v in ('!PY! -c "import sys; print(sys.version_info.major)" 2^>nul') do set "PYVER=%%v"
if not "!PYVER!"=="3" (
    echo   [ERROR] Python 3 required but found Python !PYVER!
    echo   Download Python 3 from https://python.org
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('!PY! --version 2^>^&1') do set "PYVERSTR=%%v"
echo   [OK] Found !PYVERSTR!

:: ── Step 2: Create venv if missing ───────────────────────────────
if not exist "venv\Scripts\python.exe" (
    echo   [1/3] Creating virtual environment...
    !PY! -m venv venv
    if !errorlevel! neq 0 (
        echo   [ERROR] Failed to create virtual environment.
        echo   Try: !PY! -m pip install virtualenv
        pause
        exit /b 1
    )
    echo         Done.
) else (
    echo   [1/3] Virtual environment found.
)

set "VPYTHON=.\venv\Scripts\python.exe"

:: ── Step 3: Install dependencies ─────────────────────────────────
echo   [2/3] Installing dependencies...
%VPYTHON% -m pip install -q --upgrade pip 2>nul
%VPYTHON% -m pip install -q -r requirements.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu 2>nul
if !errorlevel! neq 0 (
    echo   [WARN] Some packages may have failed. Continuing anyway...
) else (
    echo         Done.
)

:: ── Step 3.5: GPU-Aware llama-cpp-python Install ─────────────────

:: Check if llama_cpp is already importable
%VPYTHON% -c "import llama_cpp" >nul 2>&1
if !errorlevel! equ 0 (
    :: llama_cpp exists — check if marker file exists
    if exist ".gpu_backend" (
        :: Both exist — use cached backend, skip re-detection
        for /f "tokens=1,2 delims==" %%a in ('findstr "type=" ".gpu_backend" 2^>nul') do (
            echo   [OK] GPU backend: %%b ^(cached^)
        )
        goto :start_app
    )
    :: llama_cpp installed but no marker — could be a manual install
    :: Run GPU verification and write marker
    echo   [*] Verifying GPU support in existing install...
    %VPYTHON% backend\gpu_detect.py --check >nul 2>&1
    if !errorlevel! equ 0 (
        echo   [OK] Existing install has GPU support
        :: Write marker for custom install
        echo [backend]> ".gpu_backend"
        echo type=custom>> ".gpu_backend"
        echo gpu_name=user_installed>> ".gpu_backend"
        goto :start_app
    ) else (
        echo   [!] Existing install lacks GPU support. Re-detecting...
    )
)

:: Need to detect GPU and install correct backend
echo   [*] Detecting GPU hardware...

:: Run gpu_detect.py and capture the recommended backend
set "GPU_BACKEND=cpu"
set "GPU_CUDA_VER="

:: B6: Only parse single-word JSON values (recommended_backend, cuda_version).
:: GPU name has spaces which break batch tokenization, and is only cosmetic.
for /f "tokens=1,2 delims=:, " %%a in ('%VPYTHON% backend\gpu_detect.py --json 2^>nul') do (
    if "%%~a"==""recommended_backend"" set "GPU_BACKEND=%%~b"
    if "%%~a"==""cuda_version"" set "GPU_CUDA_VER=%%~b"
)

:: Display what was detected
if "!GPU_BACKEND!"=="cuda" (
    echo   [OK] Found: NVIDIA GPU ^(!GPU_CUDA_VER!^)
) else if "!GPU_BACKEND!"=="metal" (
    echo   [OK] Found: Apple Silicon ^(Metal^)
) else if "!GPU_BACKEND!"=="sycl" (
    echo   [OK] Found: Intel GPU ^(SYCL/oneAPI^)
) else if "!GPU_BACKEND!"=="rocm" (
    echo   [OK] Found: AMD GPU ^(ROCm^)
) else if "!GPU_BACKEND!"=="vulkan" (
    echo   [OK] Found: Vulkan-compatible GPU
) else (
    echo   [--] No GPU detected. Using CPU mode.
)

:: ── Install based on detected backend ────────────────────────────

if "!GPU_BACKEND!"=="cuda" (
    echo   [*] Installing llama-cpp-python with CUDA !GPU_CUDA_VER! support...
    %VPYTHON% -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/!GPU_CUDA_VER! --upgrade --force-reinstall --no-cache-dir --quiet 2>nul
    set "PIP_RC=!errorlevel!"
    if "!PIP_RC!"=="0" (
        echo   [OK] CUDA backend installed successfully
        goto :write_marker
    )
    echo   [!] CUDA install failed. Trying CPU fallback...
    goto :install_cpu
)

if "!GPU_BACKEND!"=="metal" (
    echo   [*] Installing llama-cpp-python with Metal support...
    %VPYTHON% -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/metal --upgrade --force-reinstall --no-cache-dir --quiet 2>nul
    set "PIP_RC=!errorlevel!"
    if "!PIP_RC!"=="0" (
        echo   [OK] Metal backend installed successfully
        goto :write_marker
    )
    echo   [!] Metal install failed. Trying CPU fallback...
    goto :install_cpu
)

if "!GPU_BACKEND!"=="sycl" (
    echo   [*] Installing llama-cpp-python with SYCL support ^(source build^)...
    echo   [*] This may take several minutes...

    :: oneAPI doesn't add itself to PATH by default. Find and add it.
    for /d %%d in ("C:\Program Files (x86)\Intel\oneAPI\compiler\*") do (
        if exist "%%d\bin\icx.exe" (
            echo   [*] Found oneAPI compiler at %%d\bin
            set "PATH=%%d\bin;!PATH!"
            set "LIB=%%d\lib;!LIB!"
            set "INCLUDE=%%d\include;!INCLUDE!"
            set "CMPLR_ROOT=%%d"
        )
    )
    for /d %%d in ("C:\Program Files\Intel\oneAPI\compiler\*") do (
        if exist "%%d\bin\icx.exe" (
            set "PATH=%%d\bin;!PATH!"
            set "LIB=%%d\lib;!LIB!"
            set "INCLUDE=%%d\include;!INCLUDE!"
            set "CMPLR_ROOT=%%d"
        )
    )

    :: NOTE: Do NOT add MinGW for SYCL builds.
    :: icx/icpx require MSVC's linker — MinGW is incompatible.
    set "CMAKE_ARGS=-DGGML_SYCL=on -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx"
    %VPYTHON% -m pip install llama-cpp-python --upgrade --force-reinstall --no-cache-dir --quiet 2>nul
    set "PIP_RC=!errorlevel!"
    set "CMAKE_ARGS="

    if "!PIP_RC!"=="0" (
        echo   [OK] SYCL backend installed successfully
        goto :write_marker
    )
    echo   [!] SYCL build failed. Trying Vulkan fallback...
    goto :try_vulkan
)

if "!GPU_BACKEND!"=="rocm" (
    echo   [*] Installing llama-cpp-python with ROCm support ^(source build^)...
    echo   [*] This may take several minutes...
    set "CMAKE_ARGS=-DGGML_HIPBLAS=on"
    %VPYTHON% -m pip install llama-cpp-python --upgrade --force-reinstall --no-cache-dir --quiet 2>nul
    set "PIP_RC=!errorlevel!"
    set "CMAKE_ARGS="
    if "!PIP_RC!"=="0" (
        echo   [OK] ROCm backend installed successfully
        goto :write_marker
    )
    echo   [!] ROCm build failed. Trying Vulkan fallback...
    goto :try_vulkan
)

if "!GPU_BACKEND!"=="vulkan" (
    goto :try_vulkan
)

:: Default: CPU
goto :install_cpu

:try_vulkan
:: Check if build tools are available, install if needed
where cmake >nul 2>&1
if !errorlevel! neq 0 (
    echo.
    echo   [*] Build tools needed for GPU acceleration.
    echo   [*] Installing CMake, C++ compiler, and Vulkan SDK...
    echo   [*] This is a one-time setup and may take 5-10 minutes.
    echo.
    %VPYTHON% backend\gpu_detect.py --install-tools
    if !errorlevel! neq 0 (
        echo   [!] Build tools installation had issues.
        echo   [!] Attempting Vulkan build anyway...
    )
    :: Refresh PATH to pick up newly installed tools
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%b"
    for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
    set "PATH=!SYS_PATH!;!USER_PATH!;!PATH!"
)
echo.
echo   [*] Building llama-cpp-python with Vulkan support...
echo   [*] This may take 5-15 minutes. Please be patient...
echo.
%VPYTHON% backend\gpu_detect.py --build-vulkan
if !errorlevel! equ 0 (
    set "GPU_BACKEND=vulkan"
    echo   [OK] Vulkan GPU acceleration is ready!
    goto :write_marker
)
echo   [!] Vulkan build failed. Falling back to CPU...

:install_cpu
set "GPU_BACKEND=cpu"
echo   [*] Installing llama-cpp-python ^(CPU mode^)...
%VPYTHON% -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --upgrade --no-cache-dir --quiet 2>nul
if !errorlevel! neq 0 (
    echo   [ERROR] Failed to install llama-cpp-python.
    echo   Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)
echo   [OK] CPU backend installed
if "!GPU_BACKEND!"=="cpu" (
    echo   [--] No GPU acceleration available.
) else (
    echo   [!] GPU detected but could not install GPU backend.
    echo   [!] Running on CPU. Delete .gpu_backend and restart to retry.
)

:write_marker
:: Write marker file for subsequent fast starts
echo [backend]> ".gpu_backend"
echo type=!GPU_BACKEND!>> ".gpu_backend"

:: ── Step 4: Ensure models folder ─────────────────────────────────

:start_app
if not exist "models" mkdir models

:: ── Step 5: Start server ─────────────────────────────────────────
echo   [3/3] Starting Hive server...
echo.
echo   ----------------------------------------
echo     App:     http://127.0.0.1:8080
echo     Stop:    Press CTRL+C in this window
echo     GPU:     Delete .gpu_backend to re-detect
echo   ----------------------------------------
echo.

:: Open browser after 2s delay (background)
start /b %VPYTHON% -c "import time,webbrowser; time.sleep(2); webbrowser.open('http://127.0.0.1:8080')"

:: Launch uvicorn
%VPYTHON% -m uvicorn backend.main:app --host 127.0.0.1 --port 8080

:: If we get here, server stopped
echo.
echo   Hive stopped.
pause

