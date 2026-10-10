@echo off
rem stops JARVIS completely
powershell -NoProfile -Command "try { Stop-ScheduledTask -TaskName 'JARVIS Assistant' -ErrorAction SilentlyContinue } catch {}; Start-Sleep -Milliseconds 800; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*live_cmd_console.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo JARVIS band ho gaya.
timeout /t 2 >nul
