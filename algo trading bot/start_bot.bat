@echo off
title MT5 Live Automated Bot
cd /d "%~dp0"
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

echo ===================================================
echo   Starting 9:30 NY ICT MT5 Automated Trading Bot
echo   [NOTE] QuickEdit mode disabled to prevent freezing
echo ===================================================
echo.
python mt5_live_bot.py
if errorlevel 1 (
    echo.
    echo [ERROR] Could not run python mt5_live_bot.py
    echo Please make sure Python is installed and added to PATH.
)
pause
