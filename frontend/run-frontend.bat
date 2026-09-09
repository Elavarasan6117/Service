@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Serviceability UI - LEAVE THIS WINDOW OPEN

if exist "node_modules" goto have_modules
echo.
echo  Installing front-end packages. First run only, 1-2 minutes.
echo  Warnings about package versions are normal; only ERR! matters.
echo.
call npm install
if errorlevel 1 goto npm_failed
:have_modules

echo.
echo  ============================================================
echo    SERVICEABILITY UI   -   http://localhost:5173
echo  ============================================================
echo.
echo    Leave this window open while you use the app.
echo.
echo    Sign in:   admin  /  demo-password-1234
echo.
echo  ------------------------------------------------------------
echo.

call npm run dev
goto done

:npm_failed
echo.
echo  [X] npm install failed.
echo.
echo      If it says 'npm' is not recognized, Node.js is not
echo      installed - get the LTS build from https://nodejs.org/
echo      and then run START-HERE-Windows.bat again.
echo.

:done
echo.
echo  The front end has stopped.
pause
endlocal
