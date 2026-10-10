# =====================================================================================
#  JARVIS Launcher (PowerShell)  -  v3 "Real-Life JARVIS"
#  - Launches the live_cmd_console.py that is ALREADY on disk, silently. It does NOT write/overwrite that
#    file any more (the old embedded copy of the Python source has been removed from this launcher).
#  - NEW: stops any OLD running JARVIS copies first (the old launcher stacked up hidden
#         copies, and every copy typed into the phone at the same time = garbage text).
#  - NEW: syntax-checks the script before launching.
#  - NEW: run with  -Console  to see a live console window (debugging):
#            .\JARVIS_Launcher.ps1 -Console
#  - NEW: run with  -StopLegacy  to ALSO stop the old JARVIS scripts (jarvis_service.pyw, jarvis.pyw,
#         live_notification_radar.py ...) that may listen to the same mic and type into the phone:
#            .\JARVIS_Launcher.ps1 -Console -StopLegacy
#  - Everything JARVIS hears/does is also written to  JARVIS\jarvis.log
# =====================================================================================
param([switch]$Console, [switch]$StopLegacy)

$JarvisRoot       = $PSScriptRoot          # the folder this launcher sits in (was one fixed folder on one PC)
$JarvisScriptPath = Join-Path $JarvisRoot "live_cmd_console.py"
$PythonExe        = Join-Path $JarvisRoot "venv\Scripts\pythonw.exe"
$ConsolePythonExe = Join-Path $JarvisRoot "venv\Scripts\python.exe"

# (The old embedded copy of the JARVIS Python source - $PythonCode, ~14,400 lines - was removed from here.
#  It was no longer used: this launcher starts the live_cmd_console.py that is already on disk.)

# --------------------------------------------------------------------------------
#  Make sure the JARVIS folder exists.
# --------------------------------------------------------------------------------
if (-not (Test-Path $JarvisRoot)) {
    Write-Host "JARVIS folder nahi mila, bana raha hoon: $JarvisRoot" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $JarvisRoot -Force | Out-Null
}

# --------------------------------------------------------------------------------
#  Stop every OLD JARVIS copy (this was the root cause of interleaved / repeated typing)
# --------------------------------------------------------------------------------
$old = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
       Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*live_cmd_console.py*' }
$killed = 0
foreach ($p in $old) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $killed++ } catch { }
}
if ($killed -gt 0) {
    Write-Host "Purane JARVIS ke $killed process band kiye (venv launcher + python = 2 process, 1 JARVIS)." -ForegroundColor Yellow
    Start-Sleep -Seconds 1
}

if ($StopLegacy) {
    $legacy = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
              Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'jarvis_service\.pyw|jarvis\.pyw|live_notification_radar|audit_jarvis|enroll_voice' }
    $lk = 0
    foreach ($p in $legacy) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $lk++ } catch { }
    }
    if ($lk -gt 0) {
        Write-Host "Purani JARVIS scripts ke $lk process band kiye." -ForegroundColor Yellow
        Start-Sleep -Seconds 1
    }
}

# CHANGED: the launcher NO LONGER overwrites live_cmd_console.py. It used to write an embedded copy of the source
# here; that copy was an OLD snapshot (no jarvis_agent hook, none of the later fixes) and would have silently undone them.
# The file already on disk is the real, edited JARVIS; it is launched as it is.
if (-not (Test-Path $JarvisScriptPath)) {
    Write-Host "ERROR: live_cmd_console.py nahi mila: $JarvisScriptPath" -ForegroundColor Red
    exit 1
}
Write-Host "Disk wali JARVIS script use ho rahi hai (overwrite nahi kiya): $JarvisScriptPath" -ForegroundColor Green

# --------------------------------------------------------------------------------
#  Sanity checks before launching, so failures are visible instead of silent.
# --------------------------------------------------------------------------------
if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: venv ka pythonw.exe nahi mila: $PythonExe" -ForegroundColor Red
    Write-Host "Pehle venv banao:  python -m venv `"$JarvisRoot\venv`"  aur dependencies install karo." -ForegroundColor Red
    exit 1
}

if (Test-Path $ConsolePythonExe) {
    & $ConsolePythonExe -m py_compile $JarvisScriptPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: JARVIS script me syntax error hai (upar dekho)." -ForegroundColor Red
        exit 1
    }
}

try {
    if ($Console -and (Test-Path $ConsolePythonExe)) {
        $env:JARVIS_DEBUG = "1"     # console mode: show every debug/error line live in the window
        Start-Process -FilePath $ConsolePythonExe -ArgumentList "`"$JarvisScriptPath`"" -WorkingDirectory $JarvisRoot
        Write-Host "JARVIS console mode me launch hua, Boss." -ForegroundColor Green
    } else {
        Start-Process -FilePath $PythonExe -ArgumentList "`"$JarvisScriptPath`"" -WorkingDirectory $JarvisRoot -WindowStyle Hidden
        Write-Host "JARVIS launched successfully, Boss." -ForegroundColor Green
    }
    Write-Host "Log file: $JarvisRoot\jarvis.log" -ForegroundColor DarkGray
} catch {
    Write-Host "ERROR: JARVIS launch nahi ho paya: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
