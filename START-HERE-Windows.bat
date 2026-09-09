@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Chennai Serviceability - Windows launcher
color 0F

echo.
echo  ============================================================
echo    CHENNAI SERVICEABILITY - Windows launcher
echo  ============================================================
echo.
echo  This checks what you have, installs what is missing inside
echo  this folder, and opens the app in your browser.
echo.
echo  Nothing is installed system-wide except Python and Node.js,
echo  which you install yourself if the check below says so.
echo.

REM ==========================================================
REM  1. Python
REM ==========================================================
set "PY="
py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3.12"
py -3 --version >nul 2>&1
if not defined PY if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto no_python

for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
echo  [OK] Python %PYVER%

echo %PYVER% | findstr /b "3.13 3.14 3.15" >nul
if not errorlevel 1 (
    echo.
    echo  [!] Python %PYVER% is newer than this project's packages support.
    echo      Some packages have no ready-made Windows files for it and pip
    echo      will try to compile them from source, which usually fails.
    echo.
    echo      Install Python 3.12 from https://www.python.org/downloads/windows/
    echo      then run this file again.
    echo.
    choice /c YN /m "  Try anyway"
    if errorlevel 2 goto finish
)

REM ==========================================================
REM  2. Node.js
REM ==========================================================
node --version >nul 2>&1
if errorlevel 1 goto no_node
for /f %%v in ('node --version 2^>^&1') do set "NODEVER=%%v"
echo  [OK] Node.js %NODEVER%

npm --version >nul 2>&1
if errorlevel 1 goto no_node

echo.
echo  ------------------------------------------------------------
echo   Setting up the backend. First run takes 2-5 minutes.
echo  ------------------------------------------------------------
echo.

cd backend

REM ==========================================================
REM  3. Virtual environment
REM ==========================================================
if exist ".venv\Scripts\python.exe" goto have_venv
echo  Creating a private Python environment in backend\.venv ...
%PY% -m venv .venv
if errorlevel 1 goto venv_failed
:have_venv

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto venv_failed

REM ==========================================================
REM  4. Packages
REM ==========================================================
python -c "import fastapi, uvicorn, bcrypt, aiosqlite" >nul 2>&1
if not errorlevel 1 goto have_packages

echo  Installing Python packages (about 200 MB, please wait) ...
echo.
python -m pip install --upgrade pip --quiet --disable-pip-version-check
pip install -r requirements-quickstart.txt --disable-pip-version-check
if errorlevel 1 goto pip_failed
echo.
echo  [OK] Packages installed
goto packages_done

:have_packages
echo  [OK] Python packages already installed
:packages_done

REM ==========================================================
REM  5. Demo database
REM ==========================================================
if exist "demo.db" (
    echo  [OK] Demo database already exists
) else (
    echo  Creating the demo database ...
    python scripts\demo_local.py >nul
    if errorlevel 1 goto seed_failed
    echo  [OK] 10 Chennai service locations, 1 warehouse, 7 routes
)

cd ..

REM ==========================================================
REM  6. Launch
REM ==========================================================
echo.
echo  ------------------------------------------------------------
echo   Starting. Two new windows will open - LEAVE THEM OPEN.
echo   Closing either one stops the app.
echo  ------------------------------------------------------------
echo.

start "Serviceability API  - leave open" cmd /k "%~dp0backend\run-backend.bat"
timeout /t 6 /nobreak >nul

start "Serviceability UI   - leave open" cmd /k "%~dp0frontend\run-frontend.bat"

echo  Waiting for the front end to build (first run is slower) ...
timeout /t 25 /nobreak >nul

start "" "http://localhost:5173"

echo.
echo  ============================================================
echo    Opening http://localhost:5173 in your browser
echo.
echo    Username   admin
echo    Password   demo-password-1234
echo  ============================================================
echo.
echo  If the browser shows an error, wait 20 seconds and refresh -
echo  the front end may still be building.
echo.
echo  To stop the app: close the two windows that just opened.
echo.
goto finish

REM ==========================================================
REM  Failures
REM ==========================================================
:no_python
echo  [X] Python was not found on this computer.
echo.
echo      1. Go to  https://www.python.org/downloads/windows/
echo      2. Download "Python 3.12.x - Windows installer (64-bit)"
echo      3. IMPORTANT: on the first installer screen, tick
echo         "Add python.exe to PATH" before clicking Install
echo      4. When it finishes, run this file again
echo.
goto finish

:no_node
echo  [X] Node.js was not found on this computer.
echo.
echo      1. Go to  https://nodejs.org/
echo      2. Download the LTS version and install it, accepting
echo         all the defaults
echo      3. When it finishes, run this file again
echo.
goto finish

:venv_failed
echo.
echo  [X] Could not create the Python environment in backend\.venv
echo.
echo      Most likely this folder is read-only, or it is inside a
echo      synced folder (OneDrive, Dropbox) that is locking files.
echo      Move the whole serviceability folder somewhere plain,
echo      such as C:\serviceability, and run this file again.
echo.
goto finish

:pip_failed
echo.
echo  [X] Installing the Python packages failed.
echo.
echo      The most common cause is a Python version newer than 3.12.
echo      Check with:  py --version
echo.
echo      If it says 3.13 or higher, install Python 3.12, then delete
echo      the backend\.venv folder and run this file again.
echo.
echo      The full error is in the scrolling text above.
echo.
goto finish

:seed_failed
echo.
echo  [X] Could not create the demo database.
echo      Run this by hand to see the error:
echo.
echo        cd backend
echo        .venv\Scripts\activate.bat
echo        python scripts\demo_local.py
echo.
goto finish

:finish
echo.
pause
endlocal
