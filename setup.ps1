<#
Sets up WWOZ Studio on this computer. Double-click Setup.cmd to run it.

  1. Python 3.12 and ffmpeg, installed with winget if they are missing
  2. the Python packages in requirements.txt, and the festival set finder if you say yes
  3. a "WWOZ Studio" shortcut on the desktop
  4. if you say yes, a Windows task that downloads your favorite shows every Sunday at 9 AM

Running it again is safe: it skips whatever is already there. Run it again after moving
or replacing the program's folder, so the shortcut and the Sunday task find the new one.

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

# A Windows computer with an ARM processor (a Snapdragon laptop, say). The sound and
# Shazam packages come ready-made only for the usual (x64) Python, which such a computer
# runs as well, so that is the Python Setup installs there.
$arm = ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") -or ($env:PROCESSOR_ARCHITEW6432 -eq "ARM64")

function WingetInstall($id, $name, $extra = @()) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw ("winget, Windows' own app installer, isn't on this computer, so $name can't be " +
               "installed for you. Open the Microsoft Store, search for App Installer, click Get or " +
               "Update, then double-click Setup.cmd again.")
    }
    Run "installing $name (Windows may ask for permission: say yes)" {
        winget install -e --id $id --source winget --accept-source-agreements --accept-package-agreements @extra
        # 0x8A15002B and 0x8A150061: winget says it is already installed
        if ($LASTEXITCODE -and $LASTEXITCODE -ne -1978335189 -and $LASTEXITCODE -ne -1978335135) {
            throw ("winget couldn't install $name (code $LASTEXITCODE). If Windows asked for " +
                   "permission and the answer was no, double-click Setup.cmd again and say yes.")
        }
        Write-Host "   (if winget said to restart the shell, there's no need: Setup carries on)"
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
    if ($env:TEMP -and $here.StartsWith($env:TEMP, [StringComparison]::OrdinalIgnoreCase)) {
        throw ("Setup is running from a temporary folder, which happens when it's opened from inside " +
               "the ZIP file. Close this window, right-click the downloaded ZIP file, choose Extract " +
               "All, then double-click Setup.cmd in the extracted folder.")
    }
    if ($here -like "*\Downloads\*") {
        Write-Host ("   Note: this folder is in Downloads. WWOZ Studio runs from wherever it is, so if " +
                    "Downloads gets cleaned out, it goes too. Moving the folder to Documents and " +
                    "running Setup.cmd again from there is safer.") -ForegroundColor DarkYellow
    }
    $open = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*wwoz_gui.py*" }
    if ($open -and -not $DryRun) {
        throw "WWOZ Studio is open. Close it first (its files can't be updated while it runs), then run Setup again."
    }

    Step "Python 3.12"
    if (HavePython) {
        Write-Host "   found: $(& py -3.12 --version)"
        if ($arm -and ((& py -3.12 -c "import platform; print(platform.machine())") -eq "ARM64")) {
            throw ("This computer's Python 3.12 is the ARM version, and WWOZ Studio's sound and Shazam " +
                   "parts only come ready-made for the standard (x64) one. Remove Python 3.12 in " +
                   "Settings, Apps, then double-click Setup.cmd again: it installs the right one.")
        }
    } else {
        $extra = @()
        if ($arm) {
            Write-Host "   this computer has an ARM processor: installing the standard (x64) Python, which runs on it"
            $extra = @("--architecture", "x64")
        }
        WingetInstall "Python.Python.3.12" "Python 3.12" $extra
        RefreshPath
        if (-not $DryRun -and -not (HavePython)) {
            throw "Python was installed, but this window can't see it yet. Close this window and double-click Setup.cmd again."
        }
    }

    Step "ffmpeg (reads the audio for the player, and cuts it)"
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if (-not $ff) {
        $link = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\ffmpeg.exe"
        if (Test-Path $link) { $ff = $link }
    }
    if ($ff) { Write-Host "   found" } else { WingetInstall "Gyan.FFmpeg" "ffmpeg"; RefreshPath }

    Step "Python packages"
    Run "adding the Python packages WWOZ Studio uses (a minute or two)" {
        & py -3.12 -m pip install --user --upgrade --disable-pip-version-check --no-warn-script-location -r "$here\requirements.txt"
        if ($LASTEXITCODE) { throw "pip, Python's package installer, couldn't add the packages (code $LASTEXITCODE). Check the internet connection and run Setup again." }
    }
    $sets = $WithSetDetection -or (Ask "Also install the Jazz Fest set finder? It's about a 2 GB download." $false)
    if ($sets) {
        Run "adding the set finder's packages (about 2 GB; this takes a while)" {
            & py -3.12 -m pip install --user --upgrade --disable-pip-version-check --no-warn-script-location -r "$here\requirements-sets.txt"
            if ($LASTEXITCODE) { throw "pip couldn't add the set finder (code $LASTEXITCODE). Check the internet connection and run Setup again." }
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
    $existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if ($existing -and -not $NoWeekly) {
        # set up before: point it at this folder, which may not be the one it was made in
        $want = $true
        $doing = "already set up; pointing the Windows task '$task' at this folder"
    } elseif ($NoWeekly -or -not (Ask "Download new episodes of your favorite shows every Sunday at 9 AM?" $true)) {
        $want = $false
        Write-Host "   skipped"
    } else {
        $want = $true
        $doing = "Windows task '$task', Sundays at 9 AM"
    }
    if ($want) {
        $log = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "WWOZ Studio\weekly.log"
        Run "$doing (log: $log)" {
            New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
            $act = New-ScheduledTaskAction -Execute $pyw -Argument "`"$here\wwoz.py`" --log `"$log`" weekly" `
                                           -WorkingDirectory $here
            $when = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
            $opts = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                                                 -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)
            Register-ScheduledTask -TaskName $task -Action $act -Trigger $when -Settings $opts -Force `
                -Description "WWOZ Studio: downloads new episodes of your favorite shows" | Out-Null
        }
    }

    Step "Done"
    Write-Host "   Double-click WWOZ Studio on the desktop, then pick your favorite shows on its"
    Write-Host "   Weekly Favorites page. Recordings go in Music\WWOZ recordings (change it on the"
    Write-Host "   WWOZ Archive page) and your settings in Documents\WWOZ Studio. If you move this"
    Write-Host "   folder, double-click Setup.cmd again from its new place."
} catch {
    Write-Host ""
    Write-Host "Setup stopped: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
