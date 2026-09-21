@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1"
set "HARU_EXIT=%ERRORLEVEL%"
if not "%HARU_EXIT%"=="0" pause
exit /b %HARU_EXIT%
