@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Study Tracker

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
    echo [ERROR] Python 3 was not found.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup, then run this again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 ( echo [ERROR] Could not create the virtual environment. & pause & exit /b 1 )
)
set "VPY=.venv\Scripts\python.exe"

echo Checking dependencies...

rem Some Python installs (notably the Microsoft Store one) create a venv
rem without a working pip. Detect that and bootstrap it before trying to
rem install anything, instead of just failing.
"%VPY%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo pip is missing from the virtual environment - bootstrapping it...
    "%VPY%" -m ensurepip --upgrade >nul 2>nul
    if errorlevel 1 (
        echo   ensurepip isn't available either - downloading get-pip.py instead...
        powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri https://bootstrap.pypa.io/get-pip.py -OutFile '%TEMP%\get-pip.py'"
        if errorlevel 1 (
            echo [ERROR] Could not download get-pip.py. Check your internet connection.
            echo If this keeps happening, your Python install may be the Microsoft Store
            echo version, which is known to have this problem. Installing Python from
            echo https://www.python.org/downloads/ instead usually fixes it for good.
            pause
            exit /b 1
        )
        "%VPY%" "%TEMP%\get-pip.py" --quiet
        if errorlevel 1 (
            echo [ERROR] Could not install pip into the virtual environment.
            echo Try deleting the ".venv" folder next to this script and running it again.
            echo If it still fails, reinstall Python from https://www.python.org/downloads/
            echo ^(rather than the Microsoft Store^) and tick "Add python.exe to PATH".
            pause
            exit /b 1
        )
    )
)

"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check your internet connection and try again.
    pause
    exit /b 1
)

if not exist "data" mkdir data
if not exist "data\coursepacks" mkdir data\coursepacks
echo Starting Study Tracker...
"%VPY%" main.py
if errorlevel 1 (
    echo.
    echo The app exited with an error. See data\app.log for details.
    pause
)
endlocal
