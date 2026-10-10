@echo off
rem  JARVIS one-click installer: double-click this file. It only starts Install_JARVIS.ps1 from this same folder.
title JARVIS installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_JARVIS.ps1" %*
echo.
if errorlevel 1 (
    echo Install poora nahi hua. Upar likha hai kya karna hai.
) else (
    echo Install poora ho gaya.
)
pause
