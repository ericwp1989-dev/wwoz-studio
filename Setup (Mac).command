#!/bin/bash
# Sets up WWOZ Studio on a Mac. Double-click this file to run it; macOS opens it in
# Terminal. It does on a Mac what Setup.cmd and setup.ps1 do on Windows:
#
#   1. Python 3.12 (with the Tk library its windows need) and ffmpeg, installed with
#      Homebrew if they are missing (Homebrew too, if you say yes)
#   2. the Python packages in requirements.txt, kept in WWOZ Studio's own copy of
#      Python in ~/Library/Application Support/WWOZ Studio, and the festival set finder
#      if you say yes
#   3. a "WWOZ Studio" app in the Applications folder in your home folder, and a link
#      to it on the desktop
#   4. if you say yes, a job that downloads your favorite shows every Sunday at 9 AM
#
# Running it again is safe: it skips whatever is already there. Run it again after
# moving this folder, so the app and the Sunday job point at the new place.
#
#   --dry-run             say what it would do, and change nothing
#   --with-set-detection  install the set finder without asking
#   --no-weekly           skip the Sunday download without asking

DRY=0 SETS=0 NOWEEKLY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --with-set-detection) SETS=1 ;;
        --no-weekly) NOWEEKLY=1 ;;
        *) echo "Unknown option: $arg (see the top of this file)"; exit 2 ;;
    esac
done

[ -n "$HOME" ] || { echo "No home folder is set, so Setup can't tell where to put things."; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support/WWOZ Studio"
VENV="$SUPPORT/python"
DATA="$HOME/Documents/WWOZ Studio"
APP="$HOME/Applications/WWOZ Studio.app"
LINK="$HOME/Desktop/WWOZ Studio"
LABEL="local.wwoz-studio.weekly"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"

yellow=$'\033[33m' red=$'\033[31m' cyan=$'\033[36m' plain=$'\033[0m'
step() { echo; echo "${yellow}== $1${plain}"; }
fail() { echo; echo "${red}Setup stopped: $1${plain}"; finish 1; }
finish() {
    # Terminal keeps the window open after a double-click, but say so plainly
    echo; [ "$DRY" = 1 ] || echo "You can close this window now."
    exit "$1"
}
# run "what it does" command args...: do it, or in a dry run only say it
run() {
    local what="$1"; shift
    if [ "$DRY" = 1 ]; then echo "   would: $what"; else echo "   $what"; "$@"; fi
}
# ask "question" y|n: yes or no, with the default when Return is pressed or in a dry run
ask() {
    local hint answer
    if [ "$2" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    if [ "$DRY" = 1 ]; then [ "$2" = y ]; return; fi
    read -r -p "   $1 $hint " answer
    [ -z "$answer" ] && answer="$2"
    case "$answer" in [Yy]*) return 0 ;; *) return 1 ;; esac
}
# a path inside XML, and inside single quotes in a shell script
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

[ "$(uname)" = Darwin ] || fail "this setup is for a Mac. On Windows, double-click Setup.cmd."

echo "${cyan}Setting up WWOZ Studio in $here${plain}"
[ "$DRY" = 1 ] && echo "(dry run: nothing will be changed)"

step "Homebrew (installs the programs below)"
BREW="$(command -v brew)"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -z "$BREW" ] && [ -x "$b" ] && BREW="$b"
done
if [ -n "$BREW" ]; then
    echo "   found: $BREW"
    PREFIX="$("$BREW" --prefix)"
else
    echo "   Homebrew is the usual way to install programs like these on a Mac (brew.sh)."
    echo "   Its installer asks for your Mac password, and may install Apple's developer"
    echo "   tools first; that part can take a while."
    ask "Install Homebrew now?" y || fail "WWOZ Studio needs Homebrew, or Python 3.12 and ffmpeg installed by hand."
    install_homebrew() {
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    }
    run "installing Homebrew (brew.sh)" install_homebrew \
        || fail "Homebrew's installer stopped. Run this setup again to try once more."
    if [ "$(uname -m)" = arm64 ]; then PREFIX=/opt/homebrew; else PREFIX=/usr/local; fi
    BREW="$PREFIX/bin/brew"
    [ "$DRY" = 1 ] || [ -x "$BREW" ] || fail "Homebrew was installed, but not where expected ($BREW)."
fi
export PATH="$PREFIX/bin:$PATH"

brew_install() {   # brew_install "name" formula-or-cask [--cask]
    local name="$1"; shift
    run "installing $name" "$BREW" install "$@" || fail "Homebrew couldn't install $name."
}

step "Python 3.12"
PY="$PREFIX/opt/python@3.12/bin/python3.12"
if [ -x "$PY" ]; then echo "   found: $("$PY" --version)"; else brew_install "Python 3.12" python@3.12; fi
if [ -x "$PY" ] && "$PY" -c "import tkinter" 2>/dev/null; then
    echo "   its Tk library (for the app's windows): found"
else
    brew_install "the Tk library for Python 3.12" python-tk@3.12
fi

step "ffmpeg (reads the audio for the player, and cuts it)"
if command -v ffmpeg >/dev/null; then echo "   found: $(command -v ffmpeg)"; else brew_install ffmpeg ffmpeg; fi

step "Python packages"
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import sys" 2>/dev/null; then
    echo "   WWOZ Studio's Python: found ($VENV)"
else
    # a Python of its own: Homebrew's Python refuses packages installed straight into it
    run "WWOZ Studio's own Python, in $VENV" "$PY" -m venv --clear "$VENV" \
        || fail "couldn't make WWOZ Studio's Python in $VENV."
fi
pip_install() { "$VENV/bin/python" -m pip install --upgrade --disable-pip-version-check -r "$1"; }
run "pip install -r requirements.txt" pip_install "$here/requirements.txt" \
    || fail "pip couldn't install the packages."
if [ "$SETS" = 1 ] || ask "Also install the Jazz Fest set finder? It's about a 2 GB download." n; then
    run "pip install -r requirements-sets.txt" pip_install "$here/requirements-sets.txt" \
        || fail "pip couldn't install the set finder."
else
    echo "   set finder skipped (run Setup again to add it later)"
fi

step "The WWOZ Studio app"
make_app() {
    local exe="$APP/Contents/MacOS/wwoz-studio"
    rm -rf "$APP"
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" || return 1
    cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>WWOZ Studio</string>
    <key>CFBundleDisplayName</key><string>WWOZ Studio</string>
    <key>CFBundleIdentifier</key><string>local.wwoz-studio</string>
    <key>CFBundleExecutable</key><string>wwoz-studio</string>
    <key>CFBundleIconFile</key><string>wwoz</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF
    {
        echo '#!/bin/sh'
        echo '# Opens WWOZ Studio. Written by "Setup (Mac).command"; run that again if the program moves.'
        echo "here=$(sq "$here")"
        echo "python=$(sq "$VENV/bin/python")"
        echo "PATH=$(sq "$PREFIX/bin"):\$PATH; export PATH"
        cat <<'EOF'
if [ ! -f "$here/wwoz_gui.py" ] || [ ! -x "$python" ]; then
    osascript -e 'display alert "WWOZ Studio needs to be set up again" message "Its program folder has moved or its Python is missing. Double-click Setup (Mac).command in the folder you keep WWOZ Studio in."'
    exit 1
fi
cd "$here" || exit 1
exec "$python" "$here/wwoz_gui.py"
EOF
    } > "$exe" || return 1
    chmod +x "$exe"
    # the app's icon, from the same picture as the Windows one (no icon if sips can't)
    sips -s format icns "$here/wwoz.png" --out "$APP/Contents/Resources/wwoz.icns" >/dev/null 2>&1 || true
    touch "$APP"
}
run "app: $APP" make_app || fail "couldn't make the app in $APP."
if [ -L "$LINK" ] || [ ! -e "$LINK" ]; then
    run "desktop link: $LINK" ln -sfn "$APP" "$LINK" || echo "   (no desktop link; the app is in $APP)"
else
    echo "   something else is already called \"WWOZ Studio\" on the desktop, so no link there"
fi

step "Sunday download of your favorite shows"
make_agent() {
    mkdir -p "$DATA" "$(dirname "$AGENT")" || return 1
    cat > "$AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(xml "$VENV/bin/python")</string>
        <string>$(xml "$here/wwoz.py")</string>
        <string>--log</string>
        <string>$(xml "$DATA/weekly.log")</string>
        <string>weekly</string>
    </array>
    <key>WorkingDirectory</key><string>$(xml "$here")</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$(xml "$PREFIX/bin"):/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>0</integer>
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
</dict>
</plist>
EOF
    # (re)load it: a job that was already there may point at the folder it used to be in
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$AGENT" 2>/dev/null
    launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null || launchctl load -w "$AGENT"
}
if [ -f "$AGENT" ]; then
    run "already set up; pointing the job $LABEL at this folder" make_agent \
        || fail "couldn't update the Sunday job."
elif [ "$NOWEEKLY" = 1 ] || ! ask "Download new episodes of your favorite shows every Sunday at 9 AM?" y; then
    echo "   skipped"
else
    run "a job that runs Sundays at 9 AM ($AGENT, log: $DATA/weekly.log)" make_agent \
        || fail "couldn't set up the Sunday job."
fi

step "Done"
echo "   Double-click WWOZ Studio on the desktop (it is also in the Applications folder in"
echo "   your home folder), then pick your favorite shows on its Weekly Favorites page."
echo "   Recordings go in Music/WWOZ recordings (change it on the WWOZ Archive page) and"
echo "   your settings in Documents/WWOZ Studio."
finish 0
