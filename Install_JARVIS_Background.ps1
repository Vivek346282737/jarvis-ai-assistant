# =====================================================================================
#  JARVIS - Background / Auto-start installer
#
#  What it does:
#   1. Creates a Windows scheduled task "JARVIS Assistant":
#        - starts JARVIS automatically 20 seconds after you log in to Windows (also after every restart)
#        - waits (up to 90 seconds) until the internet is reachable first, so even the first greeting uses the good online voice
#        - runs it HIDDEN in the background (no black window, everything is in JARVIS\jarvis.log)
#        - if JARVIS crashes, Windows restarts it after 1 minute
#   2. Writes two helper files in the JARVIS folder:
#        JARVIS_Start_Background.bat   -> double-click = start JARVIS in the background
#        JARVIS_Stop.bat               -> double-click = stop JARVIS completely
#
#  Usage (PowerShell, inside the JARVIS folder):
#     powershell -ExecutionPolicy Bypass -File .\Install_JARVIS_Background.ps1 -StartNow
#     powershell -ExecutionPolicy Bypass -File .\Install_JARVIS_Background.ps1 -Status
#     powershell -ExecutionPolicy Bypass -File .\Install_JARVIS_Background.ps1 -Remove
#     add  -Visible  to see JARVIS's own black window (minimized in the taskbar; click it to watch what JARVIS hears/says).
#     NOTE: closing that window stops JARVIS; Windows starts it again after 1 minute. To stop it for good say
#           "quit Jarvis completely" or double-click JARVIS_Stop.bat.
#
#  By voice:  "Jarvis off" / "go to sleep"  = JARVIS keeps running but ignores everything except the wake phrase
#             "Jarvis on" / "wake up Jarvis" = JARVIS is back
#             "quit Jarvis completely"       = JARVIS exits (start it again with the .bat file or at next login)
# =====================================================================================
param([switch]$Remove, [switch]$Status, [switch]$StartNow, [switch]$Visible)

$JarvisRoot = $PSScriptRoot
$Script     = Join-Path $JarvisRoot "live_cmd_console.py"
$PythonW    = Join-Path $JarvisRoot "venv\Scripts\pythonw.exe"
$PythonC    = Join-Path $JarvisRoot "venv\Scripts\python.exe"
$TaskName   = "JARVIS Assistant"

function Get-JarvisProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*live_cmd_console.py*' })
}

function Stop-Jarvis {
    # stop the scheduled task first, otherwise Windows would "restart the crashed task" after we kill it
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
    Start-Sleep -Milliseconds 800
    foreach ($p in (Get-JarvisProcesses)) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { }
    }
}

if ($Status) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "Auto-start task : INSTALLED (state: $($t.State))" -ForegroundColor Green
        Write-Host "Last run        : $($info.LastRunTime)   result code: $($info.LastTaskResult)"
    } else {
        Write-Host "Auto-start task : NOT installed" -ForegroundColor Yellow
    }
    $n = (Get-JarvisProcesses).Count
    if ($n -gt 0) { Write-Host "JARVIS running  : YES ($n process)" -ForegroundColor Green } else { Write-Host "JARVIS running  : NO" -ForegroundColor Yellow }
    return
}

if ($Remove) {
    Stop-Jarvis
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Auto-start hata diya aur JARVIS band kiya." -ForegroundColor Yellow
    return
}

# ---------------- checks ----------------
if (-not (Test-Path $PythonW)) {
    Write-Host "ERROR: pythonw.exe nahi mila: $PythonW" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Script)) {
    Write-Host "ERROR: live_cmd_console.py nahi mila: $Script  (pehle JARVIS_Launcher.ps1 ek baar chalao)" -ForegroundColor Red
    exit 1
}

# ---------------- helper .bat files ----------------
$startBat = @"
@echo off
rem starts JARVIS in the background (through the scheduled task, so Windows restarts it if it crashes)
schtasks /run /tn "$TaskName" >nul 2>&1
if errorlevel 1 start "" "$PythonW" "$Script"
echo JARVIS background me chalu ho gaya.
timeout /t 2 >nul
"@
$stopBat = @"
@echo off
rem stops JARVIS completely
powershell -NoProfile -Command "try { Stop-ScheduledTask -TaskName '$TaskName' -ErrorAction SilentlyContinue } catch {}; Start-Sleep -Milliseconds 800; Get-CimInstance Win32_Process | Where-Object { `$_.CommandLine -like '*live_cmd_console.py*' } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }"
echo JARVIS band ho gaya.
timeout /t 2 >nul
"@
# ---------------- auto-start wrapper: waits for the internet, then starts JARVIS and waits for it (so crashes are seen and restarted) ----------------
$wrapperText = @'
param([switch]$Visible)
$Root    = '__ROOT__'
$Script  = Join-Path $Root 'live_cmd_console.py'
$PythonW = Join-Path $Root 'venv\Scripts\pythonw.exe'
$PythonC = Join-Path $Root 'venv\Scripts\python.exe'
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try { [void][System.Net.Dns]::GetHostAddresses('speech.platform.bing.com'); break } catch { Start-Sleep -Seconds 3 }
}
if ($Visible) {
    $env:JARVIS_DEBUG = '1'
    $p = Start-Process -FilePath $PythonC -ArgumentList ('"' + $Script + '"') -WorkingDirectory $Root -WindowStyle Minimized -PassThru -Wait
} else {
    $p = Start-Process -FilePath $PythonW -ArgumentList ('"' + $Script + '"') -WorkingDirectory $Root -WindowStyle Hidden -PassThru -Wait
}
exit $p.ExitCode
'@
$wrapperText = $wrapperText.Replace('__ROOT__', $JarvisRoot)
$Wrapper = Join-Path $JarvisRoot "JARVIS_Autostart.ps1"
Set-Content -Path $Wrapper -Value $wrapperText -Encoding UTF8
Write-Host "Auto-start wrapper bana diya: JARVIS_Autostart.ps1 (internet ka intezaar karke JARVIS chalata hai)" -ForegroundColor Green

Set-Content -Path (Join-Path $JarvisRoot "JARVIS_Start_Background.bat") -Value $startBat -Encoding ASCII
Set-Content -Path (Join-Path $JarvisRoot "JARVIS_Stop.bat") -Value $stopBat -Encoding ASCII
Write-Host "Helper files bana diye: JARVIS_Start_Background.bat, JARVIS_Stop.bat" -ForegroundColor Green

# ---------------- scheduled task ----------------
$user      = "$env:USERDOMAIN\$env:USERNAME"
if ($Visible -and -not (Test-Path $PythonC)) { Write-Host "ERROR: python.exe nahi mila: $PythonC" -ForegroundColor Red; exit 1 }
# a hidden PowerShell runs the wrapper: it waits for the internet, starts JARVIS (hidden, or minimized with -Visible) and waits for it,
# so Windows still sees a crash and restarts it
$wrapArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Wrapper`"" + $(if ($Visible) { " -Visible" } else { "" })
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $wrapArgs -WorkingDirectory $JarvisRoot
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT20S"
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description "JARVIS voice assistant: starts at login, runs hidden, restarts if it crashes" -Force -ErrorAction Stop | Out-Null
    if ($Visible) { Write-Host "Auto-start lag gaya: JARVIS har login pe khud chalega, uski window taskbar me minimize dikhegi." -ForegroundColor Green }
    else { Write-Host "Auto-start lag gaya: JARVIS ab har login pe khud chalega (background me, bina window ke)." -ForegroundColor Green }
} catch {
    Write-Host "ERROR: task nahi ban paya: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "PowerShell ko 'Run as administrator' se kholke ye command dobara chalao." -ForegroundColor Red
    exit 1
}

if ($StartNow) {
    Stop-Jarvis
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 4
    $n = (Get-JarvisProcesses).Count
    if ($n -gt 0) { Write-Host "JARVIS abhi background me chal raha hai ($n process). Log: $JarvisRoot\jarvis.log" -ForegroundColor Green }
    else { Write-Host "JARVIS start nahi dikha. jarvis.log dekho." -ForegroundColor Yellow }
} else {
    Write-Host "Abhi start karne ke liye: -StartNow ke saath chalao, ya JARVIS_Start_Background.bat double-click karo." -ForegroundColor DarkGray
}
