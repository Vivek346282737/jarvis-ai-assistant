# =====================================================================================
#  JARVIS one-click installer for a NEW owner.
#  Easiest: double-click  Install_JARVIS.bat   (it runs this file).
#  Or:      powershell -ExecutionPolicy Bypass -File .\Install_JARVIS.ps1
#
#  What it does, telling you at every step:
#    1. Python 3.11  - uses it if it is already there; otherwise downloads the official installer from
#                      python.org, checks that it is really signed by the Python Software Foundation and
#                      installs it silently for this Windows user only (no admin rights needed).
#    2. This folder's own Python environment (venv).
#    3. All libraries, in exactly the versions JARVIS was tested with.
#    4. A check that JARVIS itself loads.
#    5. First-time setup (your name, AI keys, phone, voice).
#  Optional:  -WithOllama   also installs Ollama (free local AI) and downloads its models (several GB).
#  Nothing is sent anywhere; everything stays in this folder.
#
#  Switches:  -SkipSetup  only install, run setup yourself later.
#  (-SimulateNoPython, -DownloadOnly, -PythonUrl, -NoWinget exist only to TEST the installer.)
# =====================================================================================
param(
    [switch]$SkipSetup,
    [switch]$WithOllama,
    [switch]$SimulateNoPython,
    [switch]$DownloadOnly,
    [switch]$NoWinget,
    [string]$PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe",
    [string]$OllamaUrl = "https://ollama.com/download/OllamaSetup.exe"
)

$Root = $PSScriptRoot
Set-Location $Root
$ProgressPreference = "SilentlyContinue"          # the progress bar makes downloads many times slower in Windows PowerShell
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

function Say($text, $color = "Cyan") { Write-Host $text -ForegroundColor $color }
function Step($n, $text) { Write-Host ""; Write-Host "[$n/5] $text" -ForegroundColor Cyan }
function Stop-Install($what, $manual) {
    Write-Host ""
    Write-Host "RUK GAYA: $what" -ForegroundColor Red
    Write-Host "Khud aise karo, phir is installer ko dobara chalao:" -ForegroundColor Yellow
    foreach ($line in $manual) { Write-Host "   $line" -ForegroundColor Yellow }
    exit 1
}

function Test-Python311($exe) {
    # True only for a real Python 3.11 (the Microsoft Store placeholder 'python.exe' prints nothing useful and is rejected).
    if (-not $exe) { return $false }
    try {
        $out = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "3.11")
    } catch { return $false }
}

function Find-Python311 {
    if ($SimulateNoPython) { return $null }
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    $candidates += "$env:ProgramFiles\Python311\python.exe"
    $candidates += "C:\Python311\python.exe"
    foreach ($c in $candidates) {
        if ((Test-Path $c) -and (Test-Python311 $c)) {
            # 'python' on the PATH may be some OTHER project's venv; a new venv must be built from the real installation behind it
            $base = & $c -c "import sys, os; print(os.path.join(sys.base_prefix, 'python.exe'))" 2>$null
            if ($base -and (Test-Path "$base".Trim()) -and (Test-Python311 "$base".Trim())) { return "$base".Trim() }
            return $c
        }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $p = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p -and (Test-Python311 "$p".Trim())) { return "$p".Trim() }
        } catch { }
    }
    return $null
}

function Get-SignedDownload($url, $dest, $publisher, $name) {
    # Downloads a file and accepts it ONLY when Windows confirms a valid signature of the expected publisher.
    Say "   $name download ho raha hai: $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 900
    } catch {
        Say "   Download nahi ho paya: $($_.Exception.Message)" "Yellow"
        return $false
    }
    $mb = [math]::Round((Get-Item $dest).Length / 1MB, 1)
    $sig = Get-AuthenticodeSignature $dest
    $who = ""
    if ($sig.SignerCertificate) { $who = $sig.SignerCertificate.Subject }
    if ($sig.Status -ne "Valid" -or $who -notlike "*$publisher*") {
        Say "   Download hui file pe '$publisher' ka sahi signature nahi mila (status: $($sig.Status)). Ise NAHI chalaya ja raha." "Yellow"
        Remove-Item $dest -Force -ErrorAction SilentlyContinue
        return $false
    }
    Say "   Download ho gaya ($mb MB). Signature sahi hai: $publisher" "Green"
    return $true
}

function Install-Python311 {
    $manual = @("1. https://www.python.org/downloads/release/python-3119/ kholo",
                "2. 'Windows installer (64-bit)' download karke chalao",
                "3. Pehli screen pe 'Add python.exe to PATH' TICK karo, phir 'Install Now'")
    $tmp = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
    # InstallAllUsers=0 + no 'py' launcher = nothing here needs administrator rights, so no Windows permission prompt can block it.
    $silent = "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0"
    if (Get-SignedDownload $PythonUrl $tmp "Python Software Foundation" "Python 3.11 ka official installer") {
        if ($DownloadOnly) {
            Say "   (TEST MODE) installer chalaya NAHI gaya. Asli install me ye chalta:  $tmp $silent" "Yellow"
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            return "DOWNLOAD-ONLY"
        }
        Say "   Python install ho raha hai (sirf is Windows user ke liye, admin nahi chahiye; 1-2 minute)..."
        try {
            $p = Start-Process -FilePath $tmp -ArgumentList $silent -Wait -PassThru
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            if ($p.ExitCode -ne 0) { Say "   Python installer ne error diya (code $($p.ExitCode))." "Yellow" }
        } catch {
            Say "   Python installer chal nahi paya: $($_.Exception.Message)" "Yellow"
        }
        $exe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
        if ((Test-Path $exe) -and (Test-Python311 $exe)) { return $exe }
    }
    if (-not $NoWinget -and -not $DownloadOnly -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Say "   Doosra tareeka: Windows ka apna 'winget' Python install kar raha hai..."
        try {
            & winget install -e --id Python.Python.3.11 --scope user --silent --accept-source-agreements --accept-package-agreements | Out-Null
        } catch { }
        $exe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
        if ((Test-Path $exe) -and (Test-Python311 $exe)) { return $exe }
    }
    Stop-Install "Python 3.11 apne aap install nahi ho paya (internet, antivirus ya permission ki wajah se)." $manual
}

function Install-Ollama {
    $exe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $exe) {
        Say "   Ollama pehle se install hai." "Green"
    } else {
        $tmp = Join-Path $env:TEMP "OllamaSetup.exe"
        if (-not (Get-SignedDownload $OllamaUrl $tmp "Ollama" "Ollama (local AI) ka installer - ye badi file hai, time lagega")) {
            Say "   Ollama install nahi hua. JARVIS cloud AI keys se chalega. Baad me khud: https://ollama.com/download" "Yellow"
            return $false
        }
        Say "   Ollama install ho raha hai..."
        try {
            $p = Start-Process -FilePath $tmp -ArgumentList "/VERYSILENT /NORESTART /SP-" -Wait -PassThru
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        } catch {
            Say "   Ollama installer chal nahi paya: $($_.Exception.Message). Baad me khud: https://ollama.com/download" "Yellow"
            return $false
        }
        if (-not (Test-Path $exe)) {
            Say "   Ollama install hua nahi dikh raha. Baad me khud: https://ollama.com/download" "Yellow"
            return $false
        }
        Say "   Ollama install ho gaya." "Green"
        Start-Sleep -Seconds 5
    }
    Say "   Local AI ke models download ho rahe hain (kai GB; internet ke hisaab se 10-40 minute)..."
    & "$Root\venv\Scripts\python.exe" "$Root\live_cmd_console.py" --setup-free-ai
    if ($LASTEXITCODE -ne 0) {
        Say "   Models poore download nahi hue. Baad me:  .\venv\Scripts\python.exe live_cmd_console.py --setup-free-ai" "Yellow"
        return $false
    }
    return $true
}

Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  JARVIS installer - sab kuch isi folder me rahega" -ForegroundColor Green
Write-Host "  Folder: $Root" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green

# ---------------------------------------------------------------- 1. Python
Step 1 "Python 3.11 dhoondh raha hoon..."
$Python = Find-Python311
if ($Python) {
    Say "   Python 3.11 pehle se hai: $Python" "Green"
} else {
    Say "   Python 3.11 is PC pe nahi mila. Ise khud install kar raha hoon - aapko kuch nahi karna."
    $Python = Install-Python311
    if ($Python -eq "DOWNLOAD-ONLY") {
        $SimulateNoPython = $false
        $Python = Find-Python311
        if (-not $Python) { Stop-Install "(TEST MODE) aage badhne ke liye asli Python chahiye." @("Python 3.11 install karo") }
        Say "   (TEST MODE) aage ke steps is PC ke asli Python se: $Python" "Yellow"
    } else {
        Say "   Python 3.11 install ho gaya: $Python" "Green"
    }
}

# ---------------------------------------------------------------- 2. venv
Step 2 "Is folder ka apna Python environment (venv) bana raha hoon..."
if (Test-Path "$Root\venv\Scripts\python.exe") {
    Say "   venv pehle se hai." "Green"
} else {
    & $Python -m venv "$Root\venv"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$Root\venv\Scripts\python.exe")) {
        Stop-Install "venv nahi bana." @("Is folder ko kisi aisi jagah rakho jahan likhne ki ijazat ho (jaise C:\JARVIS), phir dobara chalao")
    }
    Say "   venv ban gaya." "Green"
}

# ---------------------------------------------------------------- 3. libraries
Step 3 "Libraries install ho rahi hain (pehli baar 5-15 minute; internet chahiye)..."
& "$Root\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip 2>$null | Out-Null
# requirements.txt is the COMPLETE list of the libraries (and their exact versions) of the installation this package was made
# from. --no-deps makes an exact copy of that known-working set; letting pip re-solve the dependencies failed on a clean PC
# ("ResolutionImpossible": two libraries disagree about a third one's version although they run fine together).
# A git checkout has both files: requirements.txt (what the code imports) and requirements.lock.txt (the complete tested set).
$ReqFile = if (Test-Path "$Root\requirements.lock.txt") { "$Root\requirements.lock.txt" } else { "$Root\requirements.txt" }
& "$Root\venv\Scripts\python.exe" -m pip install --no-deps --quiet --disable-pip-version-check -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    Stop-Install "kuch libraries install nahi hui (upar ka error dekho). Zyadatar ye internet ki wajah se hota hai." @(
        "Internet check karo, phir is installer ko dobara chalao - jo ho chuka hai wo dobara nahi hoga")
}
$count = (& "$Root\venv\Scripts\python.exe" -m pip list --disable-pip-version-check 2>$null | Measure-Object -Line).Lines - 2
Say "   $count libraries install ho gayi." "Green"

# ---------------------------------------------------------------- 4. check
Step 4 "JARVIS ko jaanch raha hoon..."
& "$Root\venv\Scripts\python.exe" -m py_compile "$Root\live_cmd_console.py"
if ($LASTEXITCODE -ne 0) { Stop-Install "JARVIS script check fail hua." @("Package dobara download/unzip karo; file adhoori ho sakti hai") }
Say "   JARVIS theek hai." "Green"

if ($WithOllama) {
    Write-Host ""
    Write-Host "[optional] Ollama (free local AI)..." -ForegroundColor Cyan
    [void](Install-Ollama)
}

# ---------------------------------------------------------------- 5. setup
if ($SkipSetup) {
    Step 5 "Setup chhoda gaya (-SkipSetup)."
    Say "Install ho gaya. Setup baad me chalao:  .\venv\Scripts\python.exe jarvis_setup.py" "Green"
} else {
    Step 5 "First-time setup: aapka naam, AI keys, phone, awaaz..."
    & "$Root\venv\Scripts\python.exe" "$Root\jarvis_setup.py"
    Say "Install ho gaya. JARVIS chalane ke liye:  .\JARVIS_Launcher.ps1 -Console" "Green"
}
if (-not $WithOllama) {
    Write-Host ""
    Write-Host "Optional - bina internet/cloud key ke chalne wala free local AI (Ollama, kai GB download) chahiye to:" -ForegroundColor DarkGray
    Write-Host "   powershell -ExecutionPolicy Bypass -File .\Install_JARVIS.ps1 -WithOllama -SkipSetup" -ForegroundColor DarkGray
}
exit 0
