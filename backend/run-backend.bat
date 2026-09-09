@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Serviceability API - LEAVE THIS WINDOW OPEN

if not exist ".venv\Scripts\activate.bat" goto no_venv
call ".venv\Scripts\activate.bat"

if not exist "demo.db" python scripts\demo_local.py >nul

REM The database URL needs forward slashes. A native Windows path is
REM C:\Users\me\demo.db, and backslashes in a URL are read as escape
REM characters, not path separators - the connection then fails with a
REM misleading error. %VAR:\=/% swaps them.
set "DBFILE=%CD%\demo.db"
set "DATABASE_URL_OVERRIDE=sqlite+aiosqlite:///%DBFILE:\=/%"

set "ROUTING_PROVIDER=fake"
set "GEOCODING_PROVIDER=nominatim"
set "ENVIRONMENT=development"
set "SECRET_KEY=local-demo-key-not-for-real-use-1234567890"
set "RATE_LIMIT_ENABLED=false"
set "LOG_FORMAT=console"

echo.
echo  ============================================================
echo    SERVICEABILITY API   -   http://localhost:8000
echo  ============================================================
echo.
echo    Leave this window open while you use the app.
echo    Closing it, or pressing Ctrl+C, stops the backend.
echo.
echo    Routing provider: fake  (distances are estimated, not real
echo    Chennai road distances - see docs\05-deployment-runbook.md)
echo.
echo  ------------------------------------------------------------
echo.

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto done

:no_venv
echo.
echo  [X] The Python environment has not been created yet.
echo      Close this window and run START-HERE-Windows.bat in the
echo      folder above instead.
echo.

:done
echo.
echo  The API has stopped.
pause
endlocal
