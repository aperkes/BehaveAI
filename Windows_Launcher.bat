@echo off
REM Live interactive mode: opens Powershell and leaves window open for interactive prompts
set SCRIPT_DIR=%~dp0
powershell -ExecutionPolicy Bypass -NoProfile -File "%SCRIPT_DIR%Windows_Launcher_ps.ps1" %*
pause
