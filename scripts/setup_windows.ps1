param([switch]$SetupOnly)
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSScriptRoot
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'uv.ps1') sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed. Check your network, then retry start.cmd.' }
    $Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
    $env:PYTHONUTF8 = '1'
    & $Python -I -c 'import sys, ssl, sqlite3, webview, portalocker; assert sys.version_info[:2] == (3, 12)'
    if ($LASTEXITCODE -ne 0) { throw 'Python environment check failed.' }
    Write-Host 'Project Python environment ready.'
    # Exclusive creation preserves existing credentials, including simultaneous launches.
    & $Python -I (Join-Path $PSScriptRoot 'init_env.py')
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare project configuration.' }
    if (-not $SetupOnly) {
        & $Python -B (Join-Path $ProjectDir 'start.py') --ready
        if ($LASTEXITCODE -ne 0) { throw 'Haru could not start. See the message above and README.md.' }
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
