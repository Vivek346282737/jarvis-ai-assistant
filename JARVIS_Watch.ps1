# =====================================================================================
#  JARVIS - Live Watch
#  JARVIS jo sunta hai aur jo bolta hai, sab yahan live dikhega.
#
#  Chalane ka tarika (JARVIS folder ke andar se):
#     powershell -ExecutionPolicy Bypass -File .\JARVIS_Watch.ps1
#
#  Rukna: Ctrl+C daba (JARVIS band nahi hoga, sirf dekhna band hoga)
#  Sirf zaroori lines dekhni ho (shor hata ke):
#     powershell -ExecutionPolicy Bypass -File .\JARVIS_Watch.ps1 -Clean
# =====================================================================================
param([switch]$Clean)

$LogFile = Join-Path $PSScriptRoot "jarvis.log"

if (-not (Test-Path $LogFile)) {
    Write-Host "jarvis.log nahi mila yahan: $LogFile" -ForegroundColor Red
    Write-Host "Pehle JARVIS chalu karo (Install_JARVIS_Background.ps1 -StartNow)." -ForegroundColor Yellow
    return
}

Write-Host "Live dikha raha hoon... rukne ke liye Ctrl+C daba." -ForegroundColor Cyan
Write-Host "Kuch bhej na ho to niche wala command chala (baad me):" -ForegroundColor DarkGray
Write-Host "  Get-Content .\jarvis.log -Tail 150 -Encoding UTF8 | Set-Clipboard" -ForegroundColor DarkGray
Write-Host ""

if ($Clean) {
    Get-Content $LogFile -Tail 20 -Wait -Encoding UTF8 |
        Where-Object { $_ -match "HEARD|\[JARVIS\]|\[CALL|\[ADB\]|\[FREEAI\]|\[LEARN\]|\[AGENT|SECURITY|Traceback|EXCEPTION|Error" } |
        ForEach-Object {
            if ($_ -match "EXCEPTION|Traceback|SECURITY|Error") { Write-Host $_ -ForegroundColor Red }
            elseif ($_ -match "\[JARVIS\]") { Write-Host $_ -ForegroundColor Green }
            elseif ($_ -match "HEARD") { Write-Host $_ -ForegroundColor Yellow }
            else { Write-Host $_ }
        }
} else {
    Get-Content $LogFile -Tail 20 -Wait -Encoding UTF8 |
        ForEach-Object {
            if ($_ -match "EXCEPTION|Traceback|SECURITY|Error") { Write-Host $_ -ForegroundColor Red }
            elseif ($_ -match "\[JARVIS\]") { Write-Host $_ -ForegroundColor Green }
            elseif ($_ -match "HEARD") { Write-Host $_ -ForegroundColor Yellow }
            else { Write-Host $_ }
        }
}
