@echo off
rem starts JARVIS in the background (through the scheduled task, so Windows restarts it if it crashes)
schtasks /run /tn "JARVIS Assistant" >nul 2>&1
if errorlevel 1 start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0live_cmd_console.py"
echo JARVIS background me chalu ho gaya.
timeout /t 2 >nul
