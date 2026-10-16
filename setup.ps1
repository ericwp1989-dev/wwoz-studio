<#
Sets up WWOZ Studio on this computer. Double-click Setup.cmd to run it.

  1. Python 3.12, VLC media player and ffmpeg, installed with winget if they are missing
  2. the Python packages in requirements.txt, and the festival set finder if you say yes
  3. a "WWOZ Studio" shortcut on the desktop
  4. if you say yes, a Windows task that downloads your favorite shows every Sunday at 9 AM

Running it again is safe: it skips whatever is already there.

  -DryRun            say what it would do, and change nothing
  -WithSetDetection  install the set finder without asking
  -NoWeekly          skip the Sunday download without asking
#>
param([switch]$DryRun, [switch]$WithSetDetection, [switch]$NoWeekly)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Yellow }

function Run($what, [scriptblock]$action) {
    if ($DryRun) { Write-Host "   would: $what" } else { Write-Host "   $what"; & $action }
}

function Ask($question, $default) {
    if ($DryRun) { return $default }
    if ($default) { $hint = "[Y/n]" } else { $hint = "[y/N]" }
    $answer = Read-Host "   $question $hint"
    if (-not $answer) { return $default }
    return $answer.Trim().ToLower().StartsWith("y")
}

function WingetInstall($id, $name) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget isn't on this computer, so $name can't be installed for you. Install $name by hand, then run Setup again."
    }
    Run "installing $name" {
        winget install -e --id $id --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE) { throw "winget couldn't install $name (code $LASTEXITCODE)" }
    }
}

function RefreshPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function HavePython {
    try { $v = & py -3.12 --version 2>$null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

try {
    Write-Host "Setting up WWOZ Studio in $here" -ForegroundColor Cyan
    if ($DryRun) { Write-Host "(dry run: nothing will be changed)" }

    Step "Python 3.12"
    if (HavePython) { Write-Host "   found: $(& py -3.12 --version)" }
    else { WingetInstall "Python.Python.3.12" "Python 3.12"; RefreshPath }

    Step "VLC media player (plays the recordings)"
    # where VLC's installer recorded itself (it may be on another drive), else the usual place
    $dirs = @((Get-ItemProperty "HKLM:\SOFTWARE\VideoLAN\VLC" -ErrorAction SilentlyContinue).InstallDir,
              "$env:ProgramFiles\VideoLAN\VLC") | Where-Object { $_ }
    $vlc = $dirs | ForEach-Object { Join-Path $_ "libvlc.dll" } | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($vlc) { Write-Host "   found: $(Split-Path $vlc)" } else { WingetInstall "VideoLAN.VLC" "VLC media player" }

    Step "ffmpeg (reads and cuts the audio)"
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if (-not $ff) {
        $link = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\ffmpeg.exe"
        if (Test-Path $link) { $ff = $link }
    }
    if ($ff) { Write-Host "   found" } else { WingetInstall "Gyan.FFmpeg" "ffmpeg"; RefreshPath }

    Step "Python packages"
    Run "pip install -r requirements.txt" {
        & py -3.12 -m pip install --user --upgrade --disable-pip-version-check -r "$here\requirements.txt"
        if ($LASTEXITCODE) { throw "pip couldn't install the packages (code $LASTEXITCODE)" }
    }
    $sets = $WithSetDetection -or (Ask "Also install the Jazz Fest set finder? It's about a 2 GB download." $false)
    if ($sets) {
        Run "pip install -r requirements-sets.txt" {
            & py -3.12 -m pip install --user --upgrade --disable-pip-version-check -r "$here\requirements-sets.txt"
            if ($LASTEXITCODE) { throw "pip couldn't install the set finder (code $LASTEXITCODE)" }
        }
    } else { Write-Host "   set finder skipped (run Setup again to add it later)" }

    Step "Desktop shortcut"
    if (HavePython) {
        $pyw = (& py -3.12 -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))").Trim()
    } else { $pyw = "pythonw.exe" }
    $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "WWOZ Studio.lnk"
    Run "shortcut: $lnk" {
        $shell = New-Object -ComObject WScript.Shell
        $s = $shell.CreateShortcut($lnk)
        $s.TargetPath = $pyw                      # pythonw: no black console window
        $s.Arguments = "`"$here\wwoz_gui.py`""
        $s.WorkingDirectory = $here
        $s.IconLocation = "$here\wwoz.ico"
        $s.Description = "WWOZ Studio"
        $s.Save()
    }

    Step "Sunday download of your favorite shows"
    $task = "WWOZ Weekly Archive Download"
    if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
        Write-Host "   already set up (the Windows task '$task')"
    } elseif ($NoWeekly -or -not (Ask "Download new episodes of your favorite shows every Sunday at 9 AM?" $true)) {
        Write-Host "   skipped"
    } else {
        $log = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "WWOZ Studio\weekly.log"
        Run "Windows task '$task', Sundays at 9 AM (log: $log)" {
            New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
            $act = New-ScheduledTaskAction -Execute $pyw -Argument "`"$here\wwoz.py`" --log `"$log`" weekly" `
                                           -WorkingDirectory $here
            $when = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
            $opts = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                                                 -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)
            Register-ScheduledTask -TaskName $task -Action $act -Trigger $when -Settings $opts `
                -Description "WWOZ Studio: downloads new episodes of your favorite shows" | Out-Null
        }
    }

    Step "Done"
    Write-Host "   Double-click WWOZ Studio on the desktop, then pick your favorite shows on its"
    Write-Host "   Weekly Favorites page. Recordings go in Music\WWOZ recordings (change it on the"
    Write-Host "   WWOZ Archive page) and your settings in Documents\WWOZ Studio."
} catch {
    Write-Host ""
    Write-Host "Setup stopped: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
