# Project-local uv, Python and cache; no global PATH or profile changes.
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSScriptRoot
$UvVersion = '0.12.15'
$UvDir = Join-Path $ProjectDir '.tools\uv'
$UvBin = Join-Path $UvDir 'uv.exe'
try {
    $Architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $Architecture) { $Architecture = $env:PROCESSOR_ARCHITECTURE }
    if ($Architecture -ne 'AMD64') { throw 'Haru Windows currently requires x64 Windows 10/11.' }
    $CurrentVersion = if (Test-Path $UvBin) { & $UvBin --version } else { '' }
    if ($CurrentVersion -ne "uv $UvVersion") {
        Write-Host "Preparing project-local uv $UvVersion (internet required)..."
        New-Item -ItemType Directory -Force -Path $UvDir | Out-Null
        $Installer = Join-Path ([IO.Path]::GetTempPath()) ("haru-uv-" + [guid]::NewGuid() + '.ps1')
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            for ($Attempt = 0; $Attempt -lt 3; $Attempt++) {
                try {
                    Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Uri "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $Installer
                    break
                } catch {
                    if ($Attempt -eq 2) { throw }
                    Start-Sleep -Seconds (2 * ($Attempt + 1))
                }
            }
            $env:UV_UNMANAGED_INSTALL = $UvDir
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer
            if ($LASTEXITCODE -ne 0) { throw 'uv installation failed. Check your network and retry start.cmd.' }
        } finally {
            Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path $UvBin)) { throw 'uv.exe was not installed.' }
        if ((& $UvBin --version) -ne "uv $UvVersion") { throw 'Unexpected uv version.' }
    }
    $env:UV_CACHE_DIR = Join-Path $ProjectDir '.cache\uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectDir '.tools\python'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $ProjectDir '.venv'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:UV_HTTP_TIMEOUT = '60'
    $env:UV_HTTP_RETRIES = '2'
    Remove-Item Env:VIRTUAL_ENV, Env:PYTHONHOME, Env:PYTHONPATH -ErrorAction SilentlyContinue
    & $UvBin --directory $ProjectDir @args
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
