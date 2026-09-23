param([switch]$SetupOnly)
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSScriptRoot
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'uv.ps1') sync --locked --group build
    if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed. Check your network, then retry start.cmd.' }
    $Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
    $env:PYTHONUTF8 = '1'
    & $Python -I -c 'import sys, ssl, sqlite3, webview, portalocker; assert sys.version_info[:2] == (3, 12)'
    if ($LASTEXITCODE -ne 0) { throw 'Python environment check failed.' }
    Write-Host 'Project Python environment ready.'
    & $Python -B (Join-Path $PSScriptRoot 'build_windows.py')
    if ($LASTEXITCODE -ne 0) { throw 'Haru app build failed. Close Haru, then retry start.cmd.' }
    $App = Join-Path $ProjectDir 'dist\Haru\Haru.exe'
    # A project-local shortcut keeps installation reversible and needs no admin rights.
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut((Join-Path $ProjectDir 'Haru.lnk'))
    $Shortcut.TargetPath = $App
    $Shortcut.WorkingDirectory = $ProjectDir
    $Shortcut.IconLocation = "$App,0"
    $Shortcut.Description = 'Haru - Japanese Learning'
    $Shortcut.Save()
    Write-Host 'Haru app ready. Next time, double-click Haru.lnk or dist\Haru\Haru.exe.'
    if (-not $SetupOnly) {
        Start-Process -FilePath $App -WorkingDirectory $ProjectDir
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
