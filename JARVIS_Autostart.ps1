param([switch]$Visible)
$Root    = $PSScriptRoot
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
