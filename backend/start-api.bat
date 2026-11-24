@echo off
title AutoBalloon API (port 9686)
cd /d "%~dp0"
echo.
echo  ============================================
echo   AutoBalloon API
echo   Keep this window OPEN while using the app
echo   http://127.0.0.1:9686
echo   Local-only unless SMORX_ALLOW_EXTERNAL_AI=1 / SMORX_ALLOW_CLOUD_STORAGE=1 in backend\.env
echo  ============================================
echo.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m uvicorn app.main:app --port 9686 --host 127.0.0.1
echo.
echo  API stopped. Press any key to close.
pause
