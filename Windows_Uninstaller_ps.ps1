<#
  BehaveAI - Windows_Uninstaller_ps.ps1 -- safe interactive uninstaller for the BehaveAI/yolo environment
  This version will NOT remove any scripts in the working directory (e.g. BehaveAI.py).
  It only removes:
    - the virtualenv directory (default: %USERPROFILE%\ultralytics-venv)
    - the marker file inside the venv (.ultralytics_ready)
    - the installer transcript/log (Windows_Uninstaller.log) and the uninstaller log
#>

# Fail fast
$ErrorActionPreference = 'Stop'

# Script folder and log
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$log = Join-Path $scriptDir "Windows_Uninstaller.log"
if (Test-Path $log) { Remove-Item $log -Force -ErrorAction SilentlyContinue }
Start-Transcript -Path $log -Force

try {
    Write-Host "=== Windows_Uninstaller_ps.ps1 (safe mode) ==="
    Write-Host "This uninstaller will remove the python virtual environment, marker files and logs "
    Write-Host "It will NOT remove any scripts in this folder - you can delete them yourself"
    Write-Host "It will also NOT remove system-installed Python."
    Write-Host ""

    # Defaults — change here if you used a different venv location
    $defaultVenv = Join-Path $env:USERPROFILE "ultralytics-venv"
    $venvPath = $defaultVenv

    if (-not (Test-Path $venvPath)) {
        Write-Host "Virtualenv not found at default location: $venvPath" -ForegroundColor Yellow
        $manual = Read-Host "If you created the venv elsewhere, enter its full path now (or press Enter to skip)"
        if ($manual) {
            if (Test-Path $manual) {
                $venvPath = $manual
            } else {
                Write-Host "Provided path does not exist. Aborting." -ForegroundColor Red
                Stop-Transcript
                exit 1
            }
        }
    }

    Write-Host ""
    Write-Host "Planned actions:"
    if (Test-Path $venvPath) {
        Write-Host " - Virtualenv directory: $venvPath"
    } else {
        Write-Host " - Virtualenv directory (not found): $venvPath"
    }

    $marker = Join-Path $venvPath ".ultralytics_ready"
    if (Test-Path $marker) { Write-Host " - Marker file: $marker" }

    $installerLog = Join-Path $scriptDir "Windows_Uninstaller.log"
    if (Test-Path $installerLog) { Write-Host " - Installer transcript/log: $installerLog" }

    Write-Host ""
    $proceed = Read-Host "Do you want to continue and remove the items listed above? (Y/N)"
    if ($proceed.ToUpper() -ne 'Y') {
        Write-Host "Aborting uninstall (user cancelled)."
        Stop-Transcript
        exit 0
    }

    # Check for running Python processes from this venv (best-effort)
    if (Test-Path $venvPath) {
        Write-Host ""
        Write-Host "Checking for running Python processes that belong to the virtualenv..."
        try {
            $venvPy = Join-Path $venvPath "Scripts\python.exe"
            if (Test-Path $venvPy) {
                $procs = Get-CimInstance Win32_Process | Where-Object {
                    $_.ExecutablePath -and ($_.ExecutablePath -like "*\python.exe") -and ($_.ExecutablePath -ieq $venvPy)
                }
                if ($procs) {
                    Write-Host "Found $($procs.Count) running python process(es) from the venv."
                    $kill = Read-Host "Kill these processes before removing the venv? (Y/N)"
                    if ($kill.ToUpper() -eq 'Y') {
                        foreach ($p in $procs) {
                            try {
                                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                                Write-Host "Killed process ID $($p.ProcessId)"
                            } catch {
                                Write-Warning "Failed to kill process ID $($p.ProcessId): $_"
                            }
                        }
                        Start-Sleep -Seconds 1
                    } else {
                        Write-Host "Please close those processes and re-run this uninstaller when ready."
                        Stop-Transcript
                        exit 1
                    }
                } else {
                    Write-Host "No running venv python processes found."
                }
            } else {
                Write-Host "Venv python executable not found; skipping process check."
            }
        } catch {
            Write-Warning "Error checking running processes: $_"
        }
    }

    # Remove venv directory (interactive)
    if (Test-Path $venvPath) {
        Write-Host ""
        $delVenv = Read-Host "Remove virtualenv directory '$venvPath'? (Y/N)"
        if ($delVenv.ToUpper() -eq 'Y') {
            Write-Host "Removing virtualenv directory..."
            Remove-Item -LiteralPath $venvPath -Recurse -Force -ErrorAction Stop
            Write-Host "Virtualenv removed."
        } else {
            Write-Host "Skipped removing virtualenv."
        }
    } else {
        Write-Host "Virtualenv directory not present; nothing to remove."
    }

    # Remove marker if still present
    if (Test-Path $marker) {
        try {
            Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
            Write-Host "Marker file removed: $marker"
        } catch {
            Write-Warning "Failed to remove marker file: $_"
        }
    }

    # Remove installer log (if present)
    if (Test-Path $installerLog) {
        $delLog = Read-Host "Remove installer log file '$installerLog'? (Y/N)"
        if ($delLog.ToUpper() -eq 'Y') {
            Remove-Item -LiteralPath $installerLog -Force -ErrorAction SilentlyContinue
            Write-Host "Installer log removed."
        } else {
            Write-Host "Left installer log in place."
        }
    }

    Write-Host ""
    Write-Host "Uninstall summary:"
    if (-not (Test-Path $venvPath)) { Write-Host " - Virtualenv: removed or not present" } else { Write-Host " - Virtualenv: still present" }
    if (-not (Test-Path $installerLog)) { Write-Host " - Installer log: removed or not present" } else { Write-Host " - Installer log: still present" }
    if (-not (Test-Path $marker)) { Write-Host " - Marker file: removed or not present" } else { Write-Host " - Marker file: still present" }

    Write-Host ""
    Write-Host "NOTE: This uninstaller intentionally did NOT remove any scripts in this folder (e.g. BehaveAI.py)."
    Write-Host "If you want to remove those script files manually, delete them using File Explorer or from a shell."

    Write-Host ""
    Write-Host "Uninstall completed. Uninstaller log: $log"
    Stop-Transcript
    exit 0
}
catch {
    Write-Error "Uninstall failed: $_"
    Write-Host "See log: $log"
    Stop-Transcript
    exit 1
}
