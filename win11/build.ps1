<#
.SYNOPSIS
  Builds FastImpact Preach for Windows 11: the program folder (PyInstaller) and the installer (Inno Setup).

.EXAMPLE
  .\win11\build.ps1                 # full build: tests, program, speech model, installer
  .\win11\build.ps1 -NoModel        # smaller installer, the speech model downloads on first use
  .\win11\build.ps1 -SkipTests

  Output:
    win11\build\dist\FastImpactPreach\FastImpactPreach.exe   (portable program folder)
    win11\installer\FastImpactPreach-Setup-<version>.exe     (installer)
#>
param([switch]$NoModel, [switch]$SkipTests, [switch]$NoInstaller)
$ErrorActionPreference = "Stop"
$Win = $PSScriptRoot
$Root = Split-Path $Win -Parent
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Build = Join-Path $Win "build"
$Dist = Join-Path $Build "dist\FastImpactPreach"
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

if (-not (Test-Path $Py)) { throw "Python environment not found: $Py (run run.bat once to create it)." }
$Version = (& $Py -c "import sys; sys.path.insert(0, r'$Root'); import fip; print(fip.__version__)").Trim()
Write-Host "FastImpact Preach $Version"

Step "Build tools"
& $Py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { & $Py -m pip install --disable-pip-version-check "pyinstaller>=6.10" ; if ($LASTEXITCODE) { throw "pip failed" } }

if (-not $SkipTests) {
  Step "Tests"
  Push-Location $Root
  & $Py -m pytest -q tests
  $code = $LASTEXITCODE
  Pop-Location
  if ($code -ne 0) { throw "Tests failed — the build is stopped." }
}

Step "Icon, version resource$(if (-not $NoModel) { ', speech model' })"
$args_ = @((Join-Path $Win "src\make_assets.py"), $Build, $Version)
if (-not $NoModel) { $args_ += "--model" }
& $Py @args_
$HasModel = (-not $NoModel) -and ($LASTEXITCODE -eq 0)
if (-not $NoModel -and -not $HasModel) { Write-Warning "Speech model not bundled: it will download on first use." }

Step "Program folder (PyInstaller)"
if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }
& $Py -m PyInstaller --noconfirm --log-level WARN --distpath (Join-Path $Build "dist") --workpath (Join-Path $Build "work") (Join-Path $Win "fastimpact.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Step "FFmpeg"
$ff = if ($env:FFMPEG_BIN) { $env:FFMPEG_BIN } else { (Get-Command ffmpeg -ErrorAction Stop).Source }
$ffDir = Split-Path $ff -Parent
New-Item -ItemType Directory -Force (Join-Path $Dist "ffmpeg") | Out-Null
foreach ($b in "ffmpeg.exe", "ffprobe.exe") { Copy-Item (Join-Path $ffDir $b) (Join-Path $Dist "ffmpeg\$b") -Force }
(& (Join-Path $Dist "ffmpeg\ffmpeg.exe") -hide_banner -version | Select-Object -First 1) | Out-File -Encoding utf8 (Join-Path $Win "licenses\FFMPEG-BUILD.txt")

Step "Smoke test (the packaged app starts and answers)"
$tmp = Join-Path $env:TEMP ("fip-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory $tmp | Out-Null
$saved = $env:LOCALAPPDATA
try {
  $env:LOCALAPPDATA = $tmp; $env:FIP_SMOKE_TEST = "1"
  $p = Start-Process -FilePath (Join-Path $Dist "FastImpactPreach.exe") -PassThru -Wait
  if ($p.ExitCode -ne 0) {
    Get-Content (Join-Path $tmp "FastImpactPreach\logs\fastimpact.log") -ErrorAction SilentlyContinue | Select-Object -Last 30
    throw "Smoke test failed (exit code $($p.ExitCode))"
  }
  Write-Host "  OK — the packaged app started and answered."
} finally {
  $env:LOCALAPPDATA = $saved; Remove-Item Env:FIP_SMOKE_TEST -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

if (-not $NoInstaller) {
  Step "Installer (Inno Setup)"
  $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $iscc) { throw "Inno Setup 6 not found (https://jrsoftware.org/isdl.php)." }
  $defs = @("/DAppVersion=$Version")
  if (-not $HasModel) { $defs += "/DNoModel" }
  & $iscc /Q @defs (Join-Path $Win "installer.iss")
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
  $setup = Get-Item (Join-Path $Win "installer\FastImpactPreach-Setup-$Version.exe")
  Write-Host ("  {0}  ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
}

$size = (Get-ChildItem -Recurse $Dist | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("`nDone. Program folder: {0} ({1:N0} MB)" -f $Dist, $size) -ForegroundColor Green
