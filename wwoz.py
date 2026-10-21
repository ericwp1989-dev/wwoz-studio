#!/usr/bin/env python3
"""WWOZ toolkit — download archive shows, fetch tracklists, identify songs, split recordings.

Commands:
  list                      List everything in WWOZ's 2-week archive
  schedule                  The weekly program schedule from wwoz.org, with show descriptions
  download                  Download archive shows by date / time / show-name match
  tracklist FILE            Build a tracklist for a recording from WWOZ's Spinitron playlists
  identify FILE             Identify songs by audio fingerprinting (Shazam), sampling the file
  shazam [FILE ...] | --all Fingerprint downloads and fold the finds into their tracklists
  split FILE                Split a recording into per-song files using its .tracklist.csv
  autocut FILE              Find and name the performances in a festival-day recording
  programs FILE             Mark WWOZ program changes outside festival hours
  festival                  Mark and cut a broadcast festival's downloads into one file per set
  festivals                 The festival broadcasts WWOZ has announced, and when they are recorded
  festival-record           Record the festival on the air now (the computer's schedule runs it)
  record                    Record the live stream
  weekly                    Download all new episodes of your favorite shows (favorites.json),
                            and every new broadcast of a festival WWOZ carries live

Run  py -3.12 wwoz.py <command> --help  for options.
"""

import argparse
import asyncio
import atexit
import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:          # in Python's personal package folder, which it left out (see there)
    import wwoz_player
    wwoz_player.see_personal_packages()
    import requests

TZ = ZoneInfo("America/Chicago")  # WWOZ local time; recordings assumed made on a Central-time PC
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # no console flash from GUI
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
ARCHIVE_URL = os.environ.get("WWOZ_ARCHIVE_URL") or "https://www.wwoz.org/listen/archive/"   # (tests: a stand-in)
WWOZ = "https://www.wwoz.org"
SPINITRON = "https://spinitron.com"
SCRIPT_DIR = Path(__file__).resolve().parent


def _known_folder(guid, fallback):
    """A Windows folder such as Documents or Music where it really is (it may have been
    moved, to OneDrive or another drive), else ~/<fallback>."""
    if os.name == "nt":
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
            u = uuid.UUID(guid)
            g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                path = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                return Path(path)
        except Exception:
            pass
    return Path.home() / fallback


# Each person's own files live apart from the program, so it can be shared and updated
# freely: their settings and favorite shows, the weekly schedule the app downloads,
# where they stopped in each show, and the error log. Override with WWOZ_DATA.
DATA_DIR = Path(os.environ.get("WWOZ_DATA") or
                _known_folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents") / "WWOZ Studio")
CONFIG_PATH = DATA_DIR / "favorites.json"           # settings and favorite shows
SCHEDULE_PATH = DATA_DIR / "wwoz_schedule.json"     # cached weekly schedule + show descriptions
STATE_FILES = ("favorites.json", "wwoz_schedule.json", "player_state.json", "wwoz_studio_errors.log")


def _move_old_state():
    """Earlier versions kept these files beside the code; move them to DATA_DIR once
    (not when WWOZ_DATA points somewhere else, as a test run does)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if os.environ.get("WWOZ_DATA"):
        return
    for name in STATE_FILES:
        old, new = SCRIPT_DIR / name, DATA_DIR / name
        if old.exists() and not new.exists():
            try:
                shutil.move(str(old), str(new))
            except OSError:
                pass


def _default_recordings_dir():
    return _known_folder("4BD8D571-6D19-48D3-BE97-422220080E43", "Music") / "WWOZ recordings"


def _recordings_dir():
    """The recordings folder: WWOZ_RECORDINGS, else the settings' 'recordings_dir', else
    a 'WWOZ recordings' folder in Music, made on first run."""
    if os.environ.get("WWOZ_RECORDINGS"):
        return Path(os.environ["WWOZ_RECORDINGS"])
    try:
        chosen = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")).get("recordings_dir")
        if chosen:
            return Path(chosen)
    except (OSError, ValueError, AttributeError):
        pass
    return _default_recordings_dir()


_move_old_state()
RECORDINGS_DIR = _recordings_dir()
RECORDINGS_PROBLEM = ""         # why the chosen folder isn't in use this time, for the app to say
SETTINGS_PROBLEM = ""           # why the settings file was started over, for the app to say
try:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
except OSError as e:
    # the chosen folder is on a drive that isn't there (a USB drive left unplugged, a
    # network drive that dropped): use the usual folder for now instead of not starting
    _fallback = _default_recordings_dir()
    RECORDINGS_PROBLEM = (f"Your recordings folder, {RECORDINGS_DIR}, can't be reached right now "
                          f"({e.strerror or e}). If it's on a drive that isn't plugged in, plug it in "
                          f"and open WWOZ Studio again. Until then, recordings go in {_fallback}.")
    RECORDINGS_DIR = _fallback
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "_help": [
        "Favorites are easiest to pick in WWOZ Studio's Weekly Favorites tab, which lists the",
        "whole weekly schedule. Each entry: 'match' is part of the show title as shown by",
        "`wwoz.py list` (case, accents and punctuation ignored); entries picked from the",
        "schedule also carry 'day' and 'start' (HH:MM Central) and match the archive by time",
        "slot too, because the archive titles some shows differently from the schedule.",
        "'enabled': true downloads new episodes weekly. 'recordings_dir' is the folder for",
        "your recordings (blank: 'WWOZ recordings' in Music). 'download_dir' is relative to",
        "the recordings folder unless you give an absolute path. 'send_error_reports': false",
        "stops the error reports described in wwoz_report.py. 'auto_festivals': false stops",
        "the automatic downloads of the festivals WWOZ broadcasts live; 'auto_festivals_from'",
        "is the first day whose festival broadcasts are downloaded (the day this computer",
        "first ran the feature, or the day it was turned back on), so the festivals already in",
        "the archive then are never downloaded on their own.",
    ],
    "recordings_dir": "",
    "download_dir": "archive_downloads",
    "send_error_reports": True,
    "auto_festivals": True,
    "favorites": [],
}


def write_text_atomic(path, text):
    """Write a file whole or not at all: whoever reads it at the same moment (the Sunday
    task, the app) sees the old copy or the new one, never half of one, and a crash
    mid-save leaves the old copy in place."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.saving")
    tmp.write_text(text, encoding="utf-8")
    for n in range(30):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:          # Windows: another program is reading it this instant
            if n == 29:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def save_config(cfg):
    write_text_atomic(CONFIG_PATH, json.dumps(cfg, indent=2, ensure_ascii=False))


def set_recordings_dir(folder):
    """Save a new recordings folder in the settings (used from the next start)."""
    cfg = load_config()
    cfg["recordings_dir"] = str(folder)
    save_config(cfg)


def out(msg=""):
    print(msg, flush=True)


def load_config():
    if not CONFIG_PATH.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        save_config(json.loads(json.dumps(DEFAULT_CONFIG)))
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))   # -sig: Notepad's old BOM
        if not isinstance(cfg, dict):
            raise ValueError("it isn't a settings file")
        return _first_festival_day(cfg)
    except ValueError as e:
        # broken by a hand edit: keep the old file, start fresh, and say so, rather than
        # stop the app and the Sunday task from starting at all
        bad = CONFIG_PATH.with_name(f"favorites (unreadable, {datetime.now(TZ):%Y-%m-%d %H%M}).json")
        shutil.copy2(CONFIG_PATH, bad)
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(cfg)
        global SETTINGS_PROBLEM
        SETTINGS_PROBLEM = (f"Your settings file couldn't be read ({e}), so WWOZ Studio started a "
                            f"new one: pick your favorite shows again on the Weekly Favorites page. "
                            f"The old file is saved as {bad}.")
        out(SETTINGS_PROBLEM)
        return _first_festival_day(cfg)


def _first_festival_day(cfg):
    """The first time this program runs with automatic festival downloads, note the day
    in the settings: festivals are downloaded from that day on, never the ones already
    sitting in the archive (a new install, or a copy updated to this version)."""
    if not cfg.get("auto_festivals_from"):
        cfg.setdefault("auto_festivals", True)
        cfg["auto_festivals_from"] = f"{datetime.now(TZ):%Y-%m-%d}"
        try:
            save_config(cfg)
        except OSError:
            pass                # noted again next time; meanwhile today is the first day
    return cfg


def download_dir_from_config(cfg):
    """Where archive downloads go. Relative paths are inside the recordings folder so
    the Player and Shows tabs see them; before 2026-09-11 they were resolved against
    the code folder, which broke when the code moved out of the recordings tree."""
    raw = cfg.get("download_dir", "archive_downloads")
    if raw == "../archive_downloads":          # legacy value from the pre-move layout
        raw = "archive_downloads"
    d = Path(raw)
    if not d.is_absolute():
        d = (RECORDINGS_DIR / d).resolve()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:                        # a download folder on a drive that isn't there
        d = RECORDINGS_DIR / "archive_downloads"
        d.mkdir(parents=True, exist_ok=True)
    return d


class ToolError(RuntimeError):
    """A problem a caller can catch and log (a missing program, an unreadable file).
    Raised instead of sys.exit, which slipped past every `except Exception` and silently
    ended the Sunday task, a batch run, or the app's background scanner."""


def report_error(kind, message=None):
    """Send the error being handled to the program's maker (see wwoz_report.py). Waits up
    to ten seconds, since the Sunday task may end right after; never raises."""
    try:
        import wwoz_report
        if message is None:
            wwoz_report.send_exception(kind, data_dir=DATA_DIR, wait=True)
        else:
            wwoz_report.send(kind, message, data_dir=DATA_DIR, wait=True)
    except Exception:
        pass


class NoStartTime(ToolError):
    """A recording whose name doesn't say when it was made, so no song list can be
    looked up for it. Trying again won't help."""


class AlreadyDownloading(ToolError):
    """Another program (the app, the Sunday task) is downloading this show right now.
    Not a fault: it will be there when that one finishes."""


def _session():
    """One HTTP session for everything, retrying a dropped connection or a server error
    with backoff: the Sunday task often starts before Wi-Fi is back from sleep."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=2, backoff_factor=3,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update(UA)
    return s


HTTP = _session()


def find_tool(name):
    p = shutil.which(name)
    if p:
        return p
    if sys.platform == "darwin":
        # an app opened from the Finder, or the Sunday job, gets a PATH without Homebrew's
        for d in ("/opt/homebrew/bin", "/usr/local/bin"):
            if os.access(f"{d}/{name}", os.X_OK):
                return f"{d}/{name}"
        raise ToolError(f"{name} isn't installed. Double-click Setup (Mac).command in the "
                        f"program's folder to add it.")
    # winget-installed ffmpeg may not be on PATH in this shell yet
    links = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if links.exists():
        return str(links)
    pkgs = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if pkgs.exists():
        hits = sorted(pkgs.glob(f"*FFmpeg*/**/bin/{name}.exe"))
        if hits:
            return str(hits[-1])
    raise ToolError(f"{name} isn't installed. Double-click Setup.cmd in the program's folder "
                    f"to add it.")


_DURATIONS = {}


def ffprobe_duration(path):
    """A recording's length in seconds (remembered per file, size and date, since the
    festival code measures every broadcast of the festival for each download)."""
    try:
        st = Path(path).stat()
        key = (str(path), st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if key in _DURATIONS:
        return _DURATIONS[key]
    secs = _probe_duration(path)
    if key:
        _DURATIONS[key] = secs
    return secs


def _probe_duration(path):
    ffprobe = find_tool("ffprobe")
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                       creationflags=NOWIN, timeout=120)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise ToolError(f"could not read the length of {path}: {r.stderr.strip() or 'not an audio file?'}")


# ---------------------------------------------------------------- archive ---

def fetch_archive_shows():
    """Scrape the 2-week archive page. Returns a list of dicts sorted newest first."""
    r = HTTP.get(ARCHIVE_URL, headers=UA, timeout=60)
    r.raise_for_status()
    shows = []
    if r.text.count('<li class="list-group-item">') < 20:
        # the archive always holds ~130 shows; far fewer means wwoz.org changed its page,
        # and the Sunday task would otherwise report "0 new episodes" week after week
        raise ToolError("the WWOZ archive page didn't list its shows the usual way "
                        "(wwoz.org may have changed it); nothing was downloaded")
    for li in r.text.split('<li class="list-group-item">')[1:]:
        m_stream = re.search(r'data-stream="([^"]+)"', li)
        m_title = re.search(r'show-title[^>]*>\s*(.*?)\s*</div>', li, re.S)
        m_when = re.search(r'show\.php\?date=(\d{4}-\d{2}-\d{2})&(?:amp;)?time=(\w+)', li)
        if not (m_stream and m_title and m_when):
            continue
        url = m_stream.group(1)
        m_ts = re.search(r'/(\d{14})-', url)
        start = datetime.strptime(m_ts.group(1), "%Y%m%d%H%M%S").replace(tzinfo=TZ) if m_ts else None
        shows.append({
            "title": html.unescape(m_title.group(1)).strip(),
            "url": url,
            "date": m_when.group(1),
            "time_label": m_when.group(2),
            "start": start,
            "filename": url.rsplit("/", 1)[-1],
        })
    shows.sort(key=lambda s: s["start"] or datetime.min.replace(tzinfo=TZ), reverse=True)
    return shows


def cmd_list(args):
    shows = fetch_archive_shows()
    out(f"{len(shows)} shows in the archive:\n")
    last_date = None
    for s in shows:
        if s["date"] != last_date:
            out(f"--- {s['date']} ---")
            last_date = s["date"]
        out(f"  {s['time_label']:>4}  {s['title']}")
    out("\nDownload with:  py -3.12 wwoz.py download --date YYYY-MM-DD --time 6AM")
    out('           or:  py -3.12 wwoz.py download --show "part of show name"')


def http_download(url, dest: Path, attempts=3):
    """Download with resume support, retrying a dropped connection from where it broke
    off. Returns True if downloaded, False if already complete. Only one program at a
    time downloads a given file: the app and the Sunday task appending to the same
    .part file at once would interleave the two into a corrupt mp3."""
    if dest.exists():
        return False
    lock = dest.with_suffix(dest.suffix + ".downloading")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        try:
            age = time.time() - lock.stat().st_mtime
            holder = int(lock.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            age, holder = 0, 0
        if age < 3 * 3600 and (holder == 0 or _process_alive(holder)):
            raise AlreadyDownloading(f"{dest.name} is already being downloaded by another window or the Sunday task")
        lock.write_text(str(os.getpid()), encoding="utf-8")   # left by a download that was cut off
    _HELD_LOCKS.add(lock)
    try:
        for n in range(1, attempts + 1):
            try:
                return _http_download_once(url, dest)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                if n == attempts:
                    raise
                out(f"    connection dropped ({e.__class__.__name__}); resuming in {10 * n}s …")
                time.sleep(10 * n)
    finally:
        lock.unlink(missing_ok=True)
        _HELD_LOCKS.discard(lock)


# Download locks this program holds. Closing the app mid-download ends the download
# thread without its cleanup, which used to leave a lock that blocked that show for
# three hours, the Sunday task included; they are removed as the program exits.
_HELD_LOCKS = set()


@atexit.register
def _drop_held_locks():
    for lock in list(_HELD_LOCKS):
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def _process_alive(pid):
    """Is a program with this process id still running? (A lock left by one that was
    killed outright, or by a computer that restarted, belongs to nobody.)"""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _http_download_once(url, dest: Path):
    part = dest.with_suffix(dest.suffix + ".part")
    pos = part.stat().st_size if part.exists() else 0
    headers = dict(UA)
    mode = "wb"
    if pos:
        headers["Range"] = f"bytes={pos}-"
        mode = "ab"
    with HTTP.get(url, headers=headers, stream=True, timeout=120) as r:
        if pos and r.status_code == 200:  # server ignored Range; start over
            pos, mode = 0, "wb"
        if pos and r.status_code == 416:  # the .part is already the whole file
            part.rename(dest)
            return True
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) + pos
        done = pos
        t0 = time.time()
        with open(part, mode) as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - t0 > 5:
                    pct = f"{done / total * 100:5.1f}%" if total else f"{done >> 20} MB"
                    out(f"    ... {pct}")
                    t0 = time.time()
    if total and done < total:            # cut short: keep the .part and resume it
        raise requests.ConnectionError(f"stopped at {done >> 20} of {total >> 20} MB")
    os.replace(part, dest)
    return True


def download_show(show, dest_dir: Path, with_tracklist=True):
    dest = dest_dir / show["filename"]
    out(f"  {show['date']} {show['time_label']:>4}  {show['title']}")
    if dest.exists():
        out(f"    already downloaded: {dest.name}")
    else:
        http_download(show["url"], dest)
        out(f"    saved: {dest} ({dest.stat().st_size >> 20} MB)")
    remember_title(show, dest)
    if with_tracklist and not tracklist_csv(dest).exists():
        fetch_tracklist_or_mark(dest)
    fest = festival_for(dest)
    if fest:
        try:
            mark_festival(fest, dest_dir)
        except Exception as e:
            out(f"    festival set markers failed ({e})")
    return dest


def tracklist_failed_mark(path):
    return Path(str(Path(path).with_suffix("")) + ".tracklist.failed")


def fetch_tracklist_or_mark(dest):
    """Build a download's song list from the DJs' log. If Spinitron (where WWOZ's DJs
    log their playlists) can't be reached, leave a note so the Sunday task tries again:
    otherwise the show would never get its DJ list, since a Shazam scan creates the
    tracklist file and nothing would ever ask Spinitron again."""
    mark = tracklist_failed_mark(dest)
    try:
        build_tracklist(dest)
        mark.unlink(missing_ok=True)
        return True
    except NoStartTime as e:
        out(f"    no song list: {e}")
        return False
    except Exception as e:
        mark.write_text(str(e), encoding="utf-8")
        out(f"    couldn't get the DJs' song list ({e}); it will be tried again later")
        return False


def cmd_download(args):
    shows = fetch_archive_shows()
    sel = []
    for s in shows:
        if args.date and s["date"] != args.date:
            continue
        if args.time and s["time_label"].upper() != args.time.upper():
            continue
        if args.show and loose(args.show) not in loose(s["title"]):     # Dell'Apa = Dell’Apa
            continue
        sel.append(s)
    if not sel:
        sys.exit("No archive shows match. Run `wwoz.py list` to see what's available.")
    if not (args.date and args.time) and not args.all and len(sel) > 1:
        out(f"{len(sel)} shows match:")
        for s in sel:
            out(f"  {s['date']} {s['time_label']:>4}  {s['title']}")
        out("\nRe-run with --all to download all of them, or narrow with --date/--time.")
        return
    dest_dir = Path(args.out) if args.out else download_dir_from_config(load_config())
    dest_dir.mkdir(parents=True, exist_ok=True)
    remember_titles(shows, dest_dir)
    got = [download_show(s, dest_dir, with_tracklist=not args.no_tracklist) for s in sel]
    if not (args.no_tracklist or args.no_shazam):
        for dest in got:
            shazam_fill(dest)


# -------------------------------------------------------------- schedule ---

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Program pages on wwoz.org are thin: most carry only a genre tag, and the body is
# often just archive boilerplate. Text that is boilerplate rather than description:
_SCHEDULE_BOILERPLATE = [
    r"Two-Week Archive\s*:?\s*hear recent episodes of this show on demand\.?",
    r"Support WWOZ\s*\|\s*Listen Live",
    r"If you'?d like to contact .*?digital age\.",
    r"More about [^.]*\.",
    r"Recent playlists from this show",
    r"(?m)^\s*Authored by: [A-Z][a-z]+ [A-Z][a-z]+\s*",   # article byline on a profile page
    r"Hey friends, you can contact me here.*?(?:-->|$)",
]

# What a slot plays when the site says nothing about it, keyed on part of the title.
# These read the title only; anything more specific comes from the show page, the
# host profile, or the recent-playlist artists.
TITLE_HINTS = [
    ("new orleans music show", "WWOZ's all-New Orleans show: local artists across brass, funk, R&B, jazz and more."),
    ("traditional jazz", "Early New Orleans and traditional jazz."),
    ("morning set", "The weekday-morning contemporary jazz show (6-9 AM)."),
    ("jazz from jax brewery", "Afternoon drive-time jazz."),
    ("kitchen sink", "Late-night 'everything but the kitchen sink' mix."),
    ("overnight music", "Overnight music block."),
    ("over night music", "Overnight music block."),
    ("gospel", "Sunday-morning gospel."),
    ("50s r&b", "1950s rhythm & blues."),
    ("blues", "Blues."),
]


def _minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _covers(slot, hhmm):
    """Does this slot's [start, end) contain the clock time? An end of 00:00 means midnight."""
    s, e, t = _minutes(slot["start"]), _minutes(slot["end"]), _minutes(hhmm)
    if e <= s:
        e = 24 * 60
    return s <= t < e


def _drupal_field(page, name):
    """Inner HTML of <div class="field field-name-NAME ..."> (balanced divs), or ''."""
    m = re.search(r'<div class="field field-name-' + re.escape(name) + r'[^"]*">', page)
    if not m:
        return ""
    depth, pos, start = 1, m.end(), m.end()
    tag = re.compile(r"<div\b|</div>")
    while depth:
        mm = tag.search(page, pos)
        if not mm:
            return page[start:]
        depth += 1 if mm.group(0) == "<div" else -1
        pos = mm.end()
    return page[start:mm.start()]


def _field_text(seg):
    seg = re.sub(r'<div class="field-label">.*?</div>', "", seg, flags=re.S)
    seg = re.sub(r"<(br|/p|/div|/li|/h\d)\b[^>]*>", "\n", seg)
    seg = re.sub(r"</?(font|span|b|i|em|strong|u|a)\b[^>]*>", "", seg)
    seg = html.unescape(re.sub(r"<[^>]+>", " ", seg)).replace("\xa0", " ")
    seg = re.sub(r"[ \t]+", " ", seg)
    seg = re.sub(r" *\n *", "\n", seg)
    for b in _SCHEDULE_BOILERPLATE:
        seg = re.sub(b, "", seg, flags=re.S | re.I)
    return re.sub(r"\n{2,}", "\n", seg).strip(" \n:|.,")


def _get(path):
    r = HTTP.get(WWOZ + path, headers=UA, timeout=60)
    r.raise_for_status()
    return r


def fetch_schedule_weeks(weeks_ahead=4):
    """Weekly grids from the site's schedule API, this week first. Each item carries
    title, url, showhost and start/finish epoch timestamps (Central)."""
    now = datetime.now(TZ)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    # prev/<ts> returns the week before the one containing ts, so ask from next Monday
    ts = int((monday + timedelta(days=7)).timestamp())
    weeks = [_get(f"/api/schedule/week/one/prev/{ts}").json()]
    for _ in range(weeks_ahead):
        first = min(int(it["start"]["timestamp"]) for day in weeks[-1].values() for it in day)
        weeks.append(_get(f"/api/schedule/week/one/next/{first}").json())
        time.sleep(0.5)
    return weeks


def fetch_program_details(url):
    """Genres, description and hosts from a /programs/... page."""
    page = _get(url).text
    genres = [g.strip() for g in _field_text(_drupal_field(page, "field-genres"))
              .replace("\n", ",").split(",") if g.strip()]
    hosts = [{"name": html.unescape(n.strip()), "url": u} for u, n in re.findall(
        r'href="(/profiles/[^"#?]+)"[^>]*>\s*([^<]+?)\s*<', _drupal_field(page, "field-hosted-by"))]
    return {"genres": genres, "description": _field_text(_drupal_field(page, "body")), "hosts": hosts}


def fetch_host_bio(url):
    return _field_text(_drupal_field(_get(url).text, "body"))


def cached_schedule():
    """The schedule as last saved (however old), or None — never touches the network,
    so the app can show it instantly and refresh it in the background."""
    try:
        return json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def schedule_is_stale(data, days=7):
    try:
        return datetime.now(TZ) - datetime.fromisoformat(data["fetched"]) >= timedelta(days=days)
    except (KeyError, TypeError, ValueError):
        return True


def fetch_schedule(weeks_ahead=4, refresh=False):
    """The weekly schedule as slots (weekday, start, end, show), each with genre,
    hosts and a short description, cached in wwoz_schedule.json for a week."""
    cached = None if refresh else cached_schedule()
    if cached and not schedule_is_stale(cached):
        return cached
    out(f"  fetching the weekly schedule from {WWOZ}/programs/schedule …")
    weeks = fetch_schedule_weeks(weeks_ahead)

    # one row per (weekday, start time); note the weeks each show is on
    slots = {}
    for w in weeks:
        for day in WEEKDAYS:
            for it in w.get(day, []):
                s = datetime.fromtimestamp(int(it["start"]["timestamp"]), TZ)
                e = datetime.fromtimestamp(int(it["finish"]["timestamp"]), TZ)
                key = (day, s.strftime("%H:%M"))
                slot = slots.setdefault(key, {"day": day, "start": s.strftime("%H:%M"),
                                              "end": e.strftime("%H:%M"), "shows": {}})
                sh = slot["shows"].setdefault(it["url"], {"title": html.unescape(it["title"]).strip(),
                                                          "url": it["url"], "weeks": set()})
                sh["weeks"].add((s - timedelta(days=s.weekday())).strftime("%Y-%m-%d"))
    week_keys = set()
    for w in weeks:
        first = min(datetime.fromtimestamp(int(it["start"]["timestamp"]), TZ) for day in w.values() for it in day)
        week_keys.add((first - timedelta(days=first.weekday())).strftime("%Y-%m-%d"))
    for slot in slots.values():
        for sh in slot["shows"].values():
            sh["weeks"] &= week_keys

    # Slots that air (they are in the 2-week archive) but are missing from the published
    # schedule — e.g. Thursday 11 AM in Sept 2026 — are added from the archive.
    try:
        archive = fetch_archive_shows()
    except Exception as e:
        out(f"  (archive cross-check skipped: {e})")
        archive = []
    for a in archive:
        if not a["start"]:
            continue
        day, hhmm = WEEKDAYS[a["start"].weekday()], a["start"].strftime("%H:%M")
        if any(k[0] == day and _covers(slots[k], hhmm) for k in slots):
            continue
        later = sorted(k[1] for k in slots if k[0] == day and k[1] > hhmm)
        slot = slots.setdefault((day, hhmm), {"day": day, "start": hhmm, "end": later[0] if later else "00:00",
                                              "shows": {}, "from_archive": True})
        url = "/programs/" + re.sub(r"^\d{14}-\d+-\d+-", "", a["filename"]).rsplit(".", 1)[0]
        sh = slot["shows"].setdefault(url, {"title": a["title"], "url": url, "weeks": set()})
        sh["weeks"].add(a["date"])

    programs = {}
    for slot in slots.values():
        for url, sh in slot["shows"].items():
            programs.setdefault(url, {"title": sh["title"], "url": url})
    out(f"  {len(programs)} shows; reading each show page for genre/description …")
    for url, p in sorted(programs.items()):
        try:
            p.update(fetch_program_details(url))
        except Exception as e:
            p.update({"genres": [], "description": "", "hosts": [], "error": str(e)})
        time.sleep(0.4)
    hosts = {}
    for p in programs.values():
        for h in p.get("hosts", []):
            hosts.setdefault(h["url"], dict(h))
    out(f"  {len(hosts)} hosts; reading profiles …")
    for h in hosts.values():
        try:
            h["bio"] = fetch_host_bio(h["url"])
        except Exception as e:
            h["bio"], h["error"] = "", str(e)
        time.sleep(0.4)

    recent = recent_playlist_artists()

    rows = []
    for (day, _), slot in sorted(slots.items(), key=lambda kv: (WEEKDAYS.index(kv[0][0]), kv[0][1])):
        # the newest logged playlist that started inside this slot on this weekday
        pl = next((v for (d, t), v in sorted(recent.items(), key=lambda kv: kv[1]["date"], reverse=True)
                   if d == day and _covers(slot, t)), {})
        for url, sh in slot["shows"].items():
            p = programs.get(url) or {"title": sh["title"], "url": url}
            rows.append({
                "day": day, "start": slot["start"], "end": slot["end"],
                "title": p["title"], "url": WWOZ + url,
                "genres": p.get("genres", []),
                "hosts": [h["name"] for h in p.get("hosts", [])],
                "description": p.get("description", ""),
                "host_bios": {h["name"]: hosts.get(h["url"], {}).get("bio", "")
                              for h in p.get("hosts", []) if hosts.get(h["url"], {}).get("bio")},
                "recent_artists": pl.get("artists", []),
                "recent_playlist": pl.get("url"),
                "recent_playlist_date": pl.get("date"),
                "recent_playlists_sampled": pl.get("playlists", 0),
                "weeks_scheduled": sorted(sh["weeks"]), "weeks_checked": len(weeks),
                "from_archive_only": bool(slot.get("from_archive")),
            })
    data = {"fetched": datetime.now(TZ).isoformat(), "source": WWOZ + "/programs/schedule",
            "weeks": [min(datetime.fromtimestamp(int(it["start"]["timestamp"]), TZ).strftime("%Y-%m-%d")
                          for day in w.values() for it in day) for w in weeks],
            "slots": rows}
    write_text_atomic(SCHEDULE_PATH, json.dumps(data, indent=1, ensure_ascii=False))
    out(f"  saved {SCHEDULE_PATH.name} ({len(rows)} slots)")
    return data


def recent_playlist_artists(days=42, top=8, per_slot=3):   # Spinitron serves ~45 days
    """Artists on the last few logged playlists for every (weekday, start time) Spinitron
    shows in the last `days`: {(day, 'HH:MM'): {'artists', 'url', 'date'}}. Several
    playlists are merged so one themed night (47 Dolly Parton spins, say) does not
    define a show; an artist's count is capped per playlist for the same reason."""
    end = datetime.now(TZ)
    try:
        events = spinitron_events(end - timedelta(days=days), end)
    except Exception as e:
        out(f"  (recent playlists skipped: {e})")
        return {}
    by_slot = {}
    for ev in events:                      # oldest first
        by_slot.setdefault((WEEKDAYS[ev["start"].weekday()], ev["start"].strftime("%H:%M")), []).append(ev)
    out(f"  {len(by_slot)} slots have recent playlists; sampling artists …")
    result = {}
    for key, evs in by_slot.items():
        counts, newest = {}, evs[-1]
        for ev in evs[-per_slot:]:
            try:
                spins = spinitron_spins(ev)
            except Exception:
                continue
            seen = {}
            for sp in spins:
                a = sp["artist"].strip()
                if not a:
                    continue
                if a.islower() or a.isupper():           # DJs log in whatever case
                    a = a.title()
                seen[a] = min(seen.get(a, 0) + 1, 2)     # at most 2 points per playlist
            for a, n in seen.items():
                counts[a] = counts.get(a, 0) + n
            time.sleep(0.3)
        if counts:
            result[key] = {"artists": [a for a, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:top],
                           "url": newest["url"], "date": newest["start"].strftime("%Y-%m-%d"),
                           "title": newest["title"], "playlists": len(evs[-per_slot:])}
    return result


def slot_at(when):
    """The weekly-schedule slot on the air at an aware datetime, from the cached
    schedule (None if it was never fetched)."""
    slots = (cached_schedule() or {}).get("slots", [])
    day, hhmm = WEEKDAYS[when.weekday()], when.strftime("%H:%M")
    return next((s for s in slots if s["day"] == day and _covers(s, hhmm)), None)


def fmt_clock(hhmm):
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")


def show_blurb(slot, maxlen=300, with_artists=True):
    """One-paragraph description for a schedule slot: the show page's own text if it has
    any, else the host's profile bio, else a hint read from the title plus the genre tag;
    followed by the artists on its most recent playlist."""
    text = slot.get("description") or next(iter(slot.get("host_bios", {}).values()), "")
    if not text:
        low = slot["title"].lower()
        text = next((hint for key, hint in TITLE_HINTS if key in low), "")
        if slot.get("genres"):
            text = (text + " " if text else "") + "Genre: " + ", ".join(slot["genres"]) + "."
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > maxlen:
        text = text[:maxlen].rsplit(" ", 1)[0] + "…"
    if with_artists and slot.get("recent_artists"):
        n = slot.get("recent_playlists_sampled", 0)
        basis = (f"from {n} logged shows" if n > 1 else
                 "from 1 logged show" + (f", {slot['recent_playlist_date']}" if slot.get("recent_playlist_date") else ""))
        text = (text + " " if text else "") + "Recently played: " + ", ".join(slot["recent_artists"][:6]) + f" ({basis})."
    return text


def cmd_schedule(args):
    data = fetch_schedule(refresh=args.refresh)
    out(f"WWOZ weekly schedule (Central time), fetched {data['fetched'][:10]}, "
        f"weeks checked: {', '.join(data['weeks'])}\n")
    day = None
    for s in data["slots"]:
        if s["day"] != day:
            day = s["day"]
            out(f"--- {day.title()} ---")
        gaps = s["weeks_checked"] - len(s["weeks_scheduled"])
        note = ("  (airs per the archive; not on the published schedule)" if s.get("from_archive_only")
                else f"  (listed {len(s['weeks_scheduled'])}/{s['weeks_checked']} weeks)" if gaps else "")
        out(f"  {fmt_clock(s['start']):>8} - {fmt_clock(s['end']):<8} {s['title']}"
            + (f"  [{', '.join(s['genres'])}]" if s["genres"] else "") + note)
        if args.verbose and show_blurb(s):
            out("             " + show_blurb(s))
    out("\nFavorite a show: check it in WWOZ Studio's Weekly Favorites tab, or add its title to favorites.json.")


# ------------------------------------------------------------- spinitron ---

def spinitron_events(start, end, include_unlogged=False):
    """Shows on the Spinitron calendar overlapping [start, end]. Aware datetimes.
    By default only shows with a logged playlist; include_unlogged=True returns
    every scheduled show (playlist url may be empty)."""
    url = (f"{SPINITRON}/WWOZ/calendar-feed?timeslot=30"
           f"&start={start.strftime('%Y-%m-%dT%H:%M:%S')}&end={end.strftime('%Y-%m-%dT%H:%M:%S')}")
    r = HTTP.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    events = []
    for e in r.json():
        if not include_unlogged and (not e.get("url") or "/pl/" not in e["url"]):
            continue
        es = datetime.strptime(e["start"], "%Y-%m-%dT%H:%M:%S%z")
        ee = datetime.strptime(e["end"], "%Y-%m-%dT%H:%M:%S%z")
        if ee <= start or es >= end:
            continue
        events.append({"id": e["id"], "title": e["title"], "start": es, "end": ee,
                       "url": (SPINITRON + e["url"]) if e.get("url") else None})
    events.sort(key=lambda e: e["start"])
    return events


def is_placeholder_spin(spin):
    """During festival broadcasts the DJs log a stand-in entry ('WWOZ Live Broadcast /
    2026 Jazz & Heritage Festival LIVE Broadcast') rather than songs. It is not a
    song, and as a split point it chopped performances in two."""
    text = " ".join(str(spin.get(k) or "") for k in ("artist", "song", "release")).lower()
    return ("live broadcast" in text or "jazzfest" in text.replace(" ", "")
            or bool(re.search(r"jazz (and|&) heritage festival", text)))


def spinitron_spins(event):
    """Parse one playlist page into [{'time': aware dt, 'artist','song','release'}]."""
    r = HTTP.get(event["url"], headers=UA, timeout=60)
    r.raise_for_status()
    spins = []
    for m in re.finditer(
            r'<tr id="sp-\d+" class="spin-item" data-spin="([^"]*)"[^>]*>'
            r'.*?class="spin-time"><a[^>]*>([^<]+)</a>', r.text, re.S):
        try:
            d = json.loads(html.unescape(m.group(1)))
        except json.JSONDecodeError:
            continue
        clock = m.group(2).strip()
        try:
            t = datetime.strptime(clock, "%I:%M %p").time()
        except ValueError:
            continue
        when = datetime.combine(event["start"].date(), t, tzinfo=TZ)
        if when < event["start"] - timedelta(hours=2):  # show crossed midnight
            when += timedelta(days=1)
        spins.append({"time": when,
                      "artist": (d.get("a") or "").strip(),
                      "song": (d.get("s") or "").strip(),
                      "release": (d.get("r") or "").strip(),
                      "show": event["title"]})
    spins.sort(key=lambda s: s["time"])
    return spins


# ------------------------------------------------------------- tracklist ---

FILENAME_PATTERNS = [
    # vlc-record-2026-04-23-15h02m38s-... / wwoz-live-2026-04-23-15h02m38s
    (re.compile(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})h(\d{2})m(\d{2})s'),
     lambda m: datetime(*map(int, m.groups()), tzinfo=TZ)),
    # OBS: 2026-04-19 11-48-46.mkv
    (re.compile(r'(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})'),
     lambda m: datetime(*map(int, m.groups()), tzinfo=TZ)),
    # archive download: 20260831060006-45-188-....mp3
    (re.compile(r'^(\d{14})-'),
     lambda m: datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=TZ)),
]


def load_fest_schedule():
    """Every festival day we have a schedule for: {'YYYY-MM-DD': [set, …]}, merged
    from each jazzfest*_sets.json beside the code (parse_jazzfest_schedule.py makes
    one from the festival's official schedule PDF)."""
    days = {}
    for p in sorted(SCRIPT_DIR.glob("jazzfest*_sets.json")):
        try:
            days.update(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            out(f"  (skipping {p.name}: {e})")
    return days


def start_time_from_name(path: Path):
    for pat, conv in FILENAME_PATTERNS:
        m = pat.search(path.name)
        if m:
            return conv(m)
    return None


def piece_clock(path):
    """When a cut piece (a set or a show) begins on the air, from the pieces.json or
    festival.json its cut wrote; None for anything else."""
    path = Path(path)
    folder = path.parent
    idx = pieces_index(folder)
    for pc in (idx or {}).get("pieces", []):
        if pc["file"] == path.name and pc.get("clock"):
            return datetime.fromisoformat(pc["clock"])
    for fj_path, rel in ((folder / "festival.json", path.name),
                         (folder.parent / "festival.json", f"{folder.name}/{path.name}")):
        try:
            fj = json.loads(fj_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for s in fj.get("sets", []):
            if s["file"] == rel:
                return datetime.strptime(f"{s['day']} {s['aired_from']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        for pr in fj.get("programs", []):
            if pr["file"] == rel:
                return datetime.fromisoformat(pr["clock"])
    return None


def show_family(title):
    """Which show an episode belongs to, and its host: 'New Orleans Music Show with
    Missy Bowen' -> ('New Orleans Music Show', 'Missy Bowen'); 'Overnight Music -
    Thursday' -> ('Overnight Music', ''). WWOZ runs most shows daily or weekly with
    a rotating host, so the show is the part before 'with'."""
    t = re.sub(r"\s+-\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*$", "",
               title or "", flags=re.I)
    m = re.match(r"(.+?)\s+with\s+(.+)$", t, re.I)
    name, host = (m.group(1), m.group(2)) if m else (t, "")
    host = re.sub(r",?\s+(MID|\d{1,2}\s*(am|pm)).*$", "", host, flags=re.I)   # "D Rootzmaster, 3am - 6am"
    return name.strip(), host.strip()


def family_key(name):
    """loose() of a show's name without a leading 'The', so 'Morning Set' and 'The
    Morning Set' are one show."""
    return re.sub(r"^the", "", loose(name))


def recording_start(path: Path, duration=None):
    """When a recording began: from its file name, else (a file renamed by hand, like
    'Jazzfest day 1 first part.mp3') its last-modified time minus its length —
    recorders write the file until the end, so that is when recording started."""
    start = start_time_from_name(Path(path)) or piece_clock(path)
    if start is None:
        try:
            duration = duration or ffprobe_duration(path)
            start = datetime.fromtimestamp(Path(path).stat().st_mtime, tz=TZ) - timedelta(seconds=duration)
        except (SystemExit, OSError, ToolError, subprocess.SubprocessError):
            return None
    return start


def fmt_off(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


def sanitize(name, maxlen=120):
    """A file name Windows accepts that still reads well when sent to someone:
    'The Great Duets: Jolynda "Kiki" Chapman' -> The Great Duets - Jolynda 'Kiki' Chapman."""
    name = name.replace('"', "'").replace(":", " -").replace("/", "-")
    name = re.sub(r"\s{2,}", " ", name)
    name = re.sub(r'[<>\\|?*\x00-\x1f]', '_', name).strip(' .')
    return name[:maxlen] or "unknown"


TRACK_FIELDS = ["offset", "clock_time", "artist", "song", "release", "show", "offset_seconds"]


def is_marker(row):
    """A row we made (set markers, program changes, Shazam finds, the user's own
    markers), as opposed to a song from the DJs' Spinitron log."""
    show = str(row.get("show", ""))
    return show.startswith(("autocut", "set @")) or show in ("manual marker", "WWOZ program", "shazam")


def build_tracklist(path: Path, start=None, duration=None):
    """Fetch Spinitron songs for the recording's time window; write .csv/.txt/.cue.
    Markers already in the tracklist (sets, program changes, Shazam finds, the
    user's own) are kept; only the DJ-logged songs are replaced."""
    start = start or start_time_from_name(path)
    if start is None:
        raise NoStartTime(f"the name '{path.name}' doesn't say when it was recorded, so the "
                          f"DJs' song list can't be looked up")
    duration = duration or ffprobe_duration(path)
    end = start + timedelta(seconds=duration)
    out(f"  window: {start:%Y-%m-%d %I:%M %p} -> {end:%Y-%m-%d %I:%M %p} Central "
        f"({fmt_off(duration)})")

    events = spinitron_events(start - timedelta(hours=1), end + timedelta(hours=1))
    rows = []
    for ev in events:
        for sp in spinitron_spins(ev):
            # spins carry whole minutes and archive files start a few seconds past the
            # hour, so a song logged at "6:00 AM" is 06:00:00, before a 06:00:06 start
            if start - timedelta(seconds=60) <= sp["time"] <= end and not is_placeholder_spin(sp):
                off = max(0.0, (sp["time"] - start).total_seconds())
                rows.append({"offset_seconds": int(off), "offset": fmt_off(off),
                             "clock_time": sp["time"].strftime("%Y-%m-%d %I:%M %p"),
                             "artist": sp["artist"], "song": sp["song"],
                             "release": sp["release"], "show": sp["show"]})
    rows.sort(key=lambda r: r["offset_seconds"])
    markers = [r for r in read_tracklist(path) if is_marker(r)]
    write_tracklist(path, sorted(rows + markers, key=lambda r: int(r["offset_seconds"])))

    base = path.with_suffix("")
    csv_path = tracklist_csv(path)
    txt_path = Path(str(base) + ".tracklist.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"{path.name}\nRecorded {start:%A %B %d, %Y %I:%M %p} Central, "
                f"length {fmt_off(duration)}\n")
        f.write(f"Songs logged by WWOZ (Spinitron) during this window: {len(rows)}\n\n")
        show = None
        for r_ in rows:
            if r_["show"] != show:
                show = r_["show"]
                f.write(f"\n== {show} ==\n")
            f.write(f"{r_['offset']:>8}  {r_['artist']} - {r_['song']}"
                    + (f"  [{r_['release']}]" if r_['release'] else "") + "\n")

    out(f"  wrote {csv_path.name}  ({len(rows)} songs"
        + (f", {len(markers)} markers kept" if markers else "") + ")")
    out(f"  wrote {txt_path.name}, {Path(str(base) + '.cue').name}")
    return rows


def cmd_tracklist(args):
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"ERROR: {path} not found")
    start = None
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    elif args.from_mtime:
        dur = args.duration or ffprobe_duration(path)
        end = datetime.fromtimestamp(path.stat().st_mtime, tz=TZ)
        start = end - timedelta(seconds=dur)
        out(f"  assuming recording ENDED at file mtime {end:%Y-%m-%d %I:%M %p} "
            f"(only valid if the file wasn't modified after recording)")
    try:
        build_tracklist(path, start=start, duration=args.duration)
    except NoStartTime as e:
        raise ToolError(f"{e}. Give the start with --start \"YYYY-MM-DD HH:MM\" (Central).")


# -------------------------------------------------------------- identify ---

# Shazam answers a phone tagging a song every few seconds; hammer it (one request a
# second for minutes) and it stops answering this address at all — requests hang
# without a reply rather than failing. Hence a per-request timeout, a pause between
# samples, and backing off / giving up when it goes quiet.
SHAZAM_TIMEOUT = 25      # seconds to wait for one answer
SHAZAM_PAUSE = 4         # seconds between samples
SHAZAM_BACKOFF = 300     # after 3 straight timeouts, wait this long once, then give up


async def identify_async(path: Path, interval, clip, start_off, duration, csv_suffix,
                         pause=SHAZAM_PAUSE):
    """Sample the recording every `interval` seconds and ask Shazam what is playing.
    Writes a .shazam.csv of the distinct tracks heard. Returns (found, errors, aborted);
    aborted=True means Shazam stopped answering and nothing was written, so the scan
    should be tried again later."""
    ffmpeg = find_tool("ffmpeg")
    if str(Path(ffmpeg).parent) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(Path(ffmpeg).parent) + os.pathsep + os.environ.get("PATH", "")
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    from shazamio import Shazam
    total = ffprobe_duration(path)
    end = min(total, start_off + duration) if duration else total
    shazam = Shazam()
    hits = []          # (offset, artist, title)
    segments = []      # grouped consecutive matches
    errors = 0         # samples Shazam never answered
    quiet = 0          # consecutive unanswered samples
    backed_off = False
    aborted = False
    out(f"  scanning {path.name}: {fmt_off(start_off)} -> {fmt_off(end)} "
        f"every {interval}s ({int((end - start_off) // interval)} samples)")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        wav = Path(td) / "clip.wav"
        offset = start_off
        while offset < end:
            wav.unlink(missing_ok=True)       # a failed extract must not resend the last clip
            subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss", str(offset), "-i", str(path),
                            "-t", str(clip), "-vn", "-ac", "1", "-ar", "44100", "-y", str(wav)],
                           capture_output=True, stdin=subprocess.DEVNULL, creationflags=NOWIN)
            artist = title = None
            if wav.exists() and wav.stat().st_size > 1000:
                try:
                    res = await asyncio.wait_for(shazam.recognize(str(wav)), timeout=SHAZAM_TIMEOUT)
                    tr = res.get("track") or {}
                    artist, title = tr.get("subtitle"), tr.get("title")
                    quiet = 0
                except Exception as e:
                    errors += 1
                    quiet += 1
                    kind = "no answer" if isinstance(e, asyncio.TimeoutError) else f"error: {e}"
                    out(f"    {fmt_off(offset)}  ({kind})")
                    if quiet >= 3:
                        if backed_off:
                            out(f"    Shazam has stopped answering — giving up on this file for now "
                                f"({len(segments)} tracks heard so far)")
                            aborted = True
                            break
                        out(f"    Shazam is not answering; pausing {SHAZAM_BACKOFF // 60} minutes …")
                        await asyncio.sleep(SHAZAM_BACKOFF)
                        backed_off, quiet = True, 2   # one more silence after the pause = give up
                        continue                      # retry this same offset
            if title:
                hits.append((offset, artist, title))
                if segments and segments[-1]["artist"] == artist and segments[-1]["title"] == title:
                    segments[-1]["last"] = offset
                    segments[-1]["samples"] += 1
                else:
                    segments.append({"first": offset, "last": offset, "samples": 1,
                                     "artist": artist, "title": title})
                    out(f"    {fmt_off(offset)}  {artist} - {title}")
            offset += interval
            await asyncio.sleep(pause)

    if aborted:
        return len(segments), errors, True
    base = path.with_suffix("")
    csv_path = Path(str(base) + csv_suffix)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["first_heard", "last_heard", "samples", "artist", "title"])
        for s in segments:
            w.writerow([fmt_off(s["first"]), fmt_off(s["last"]), s["samples"],
                        s["artist"], s["title"]])
    out(f"  wrote {csv_path.name}  ({len(segments)} distinct tracks, {len(hits)} matched samples)")
    return len(segments), errors, False


def cmd_identify(args):
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"ERROR: {path} not found")
    asyncio.run(identify_async(path, args.interval, args.clip, args.start_offset,
                               args.duration, ".shazam.csv"))
    out("  NOTE: live in-concert performances usually do NOT fingerprint; matches are")
    out("  strongest for studio recordings played between sets / on regular shows.")


# ------------------------------------------------------ shazam -> tracklist ---

def tracklist_csv(path):
    return Path(str(path.with_suffix("")) + ".tracklist.csv")


def shazam_csv(path):
    return Path(str(path.with_suffix("")) + ".shazam.csv")


def shazam_lock(path):
    """Present while a scan is running, so the app and the Sunday task never both scan
    one file. A lock older than two hours is a crashed scan and is ignored."""
    return Path(str(path.with_suffix("")) + ".shazam.lock")


def shazam_pending(path):
    if shazam_csv(path).exists():
        return False
    lock = shazam_lock(path)
    return not lock.exists() or time.time() - lock.stat().st_mtime > 7200


def read_tracklist(path):
    rows = []
    p = tracklist_csv(path)
    if p.exists():
        with open(p, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["offset_seconds"] = int(float(r.get("offset_seconds") or 0))
        rows.sort(key=lambda r: r["offset_seconds"])
    return rows


def write_tracklist(path, rows):
    """The .tracklist.csv and .cue for a recording (what the Player's Save writes)."""
    path = Path(path)
    with open(tracklist_csv(path), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=TRACK_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in TRACK_FIELDS})

    def q(s):                       # a double quote would end the CUE field early
        return str(s or "").replace('"', "'")
    with open(Path(str(path.with_suffix("")) + ".cue"), "w", encoding="utf-8") as f:
        f.write(f'PERFORMER "WWOZ 90.7 FM"\nTITLE "{q(sanitize(path.stem))}"\nFILE "{q(path.name)}" MP3\n')
        for i, r in enumerate(rows, 1):
            mm, ss = divmod(int(r["offset_seconds"]), 60)
            f.write(f"  TRACK {i:02} AUDIO\n    TITLE \"{q(r.get('song'))}\"\n"
                    f"    PERFORMER \"{q(r.get('artist'))}\"\n    INDEX 01 {mm:02}:{ss:02}:00\n")


def shazam_rows(path):
    """A recording's Shazam finds as tracklist rows (show = 'shazam')."""
    rows = []
    p = shazam_csv(path)
    if p.exists():
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                h, m, s = r["first_heard"].split(":")
                sec = int(h) * 3600 + int(m) * 60 + int(s)
                rows.append({"offset": fmt_off(sec), "clock_time": "", "artist": r["artist"],
                             "song": r["title"], "release": "", "show": "shazam", "offset_seconds": sec})
    return rows


def merge_rows(rows, extra, min_gap=90):
    """rows + those of `extra` that don't sit within min_gap seconds of an existing
    entry (a DJ's own log wins over a fingerprint of the same song). Returns (rows, added)."""
    known = [int(r["offset_seconds"]) for r in rows]
    added = [e for e in extra if all(abs(int(e["offset_seconds"]) - k) > min_gap for k in known)]
    return sorted(rows + added, key=lambda r: int(r["offset_seconds"])), len(added)


def merge_shazam(path):
    """Fold a recording's Shazam finds into its tracklist files. Returns rows added."""
    rows, added = merge_rows(read_tracklist(path), shazam_rows(path))
    if added:
        write_tracklist(path, rows)
    return added


def shazam_fill(path, interval=90):
    """Fingerprint a downloaded show (one sample every 90 s) and fold the finds into its
    tracklist. Skips shows already scanned or being scanned; a scan that produced
    nothing but errors is forgotten so it is retried another time. Returns rows added."""
    path = Path(path)
    if not shazam_pending(path):
        return 0
    lock = shazam_lock(path)
    lock.write_text(str(time.time()), encoding="utf-8")
    try:
        out(f"  Shazam scan: {path.name}")
        found, errors, aborted = asyncio.run(identify_async(path, interval, 12, 0, None, ".shazam.csv"))
        if aborted:
            raise ShazamQuiet(path)
        samples = max(1, int(ffprobe_duration(path) // interval))
        if found == 0 and errors * 2 > samples:
            # mostly unanswered: try again another time. (Nothing found with only a few
            # errors is a real answer, as for a talk show; rescanning it weekly helps no one.)
            shazam_csv(path).unlink(missing_ok=True)
            out("    Shazam mostly didn't answer; will retry another time")
            return 0
        added = merge_shazam(path)
        out(f"    {added} song(s) added to the tracklist ({found} recognised)")
        return added
    finally:
        lock.unlink(missing_ok=True)


class ShazamQuiet(Exception):
    """Shazam stopped answering; the scan was abandoned and should be retried later."""


def pending_downloads(dest_dir):
    """Archive downloads still to be scanned, newest first."""
    return [p for p in sorted(Path(dest_dir).glob("*.mp3"), reverse=True) if shazam_pending(p)]


def scanner_active(dest_dir, mine=None):
    """Is another scanner (the app, the Sunday task, shazam_backlog.py) working in this
    folder right now? Two at once is what makes Shazam stop answering, so they take turns."""
    busy = False
    for lock in Path(dest_dir).glob("*.shazam.lock"):
        if mine is not None and lock == shazam_lock(mine):
            continue
        try:
            if time.time() - lock.stat().st_mtime < 7200:
                busy = True
            else:                    # left behind by a scan that was closed mid-way
                lock.unlink(missing_ok=True)
        except OSError:
            pass
    return busy


def wait_for_turn(dest_dir, max_minutes=180):
    """Block while another scanner is active (up to max_minutes). Returns False if it
    never freed up."""
    waited = 0
    while scanner_active(dest_dir):
        if waited == 0:
            out("  another Shazam scan is running in this folder; waiting for it …")
        if waited >= max_minutes * 60:
            return False
        time.sleep(60)
        waited += 60
    return True


def cmd_shazam(args):
    if args.all:
        files = pending_downloads(download_dir_from_config(load_config()))
    else:
        files = [Path(f) for f in args.files]
    if not files:
        out("Nothing to scan — every download already has a Shazam scan.")
        return
    out(f"{len(files)} recording(s) to scan, one sample every {args.interval}s:")
    for f in files:
        if not f.exists():
            out(f"  {f}: not found")
            continue
        if not wait_for_turn(f.parent):
            out("Another scanner has been busy for hours; giving up for now.")
            return
        try:
            shazam_fill(f, interval=args.interval)
        except ShazamQuiet:
            out("Shazam is not answering this computer right now (it does that after a burst of "
                "requests). Stopping; run this again in an hour or so.")
            return
        except Exception as e:
            out(f"    scan failed: {e}")
    out("Done.")


# ----------------------------------------------------------------- split ---

SPLIT_NAME = re.compile(r"^\d+ - .+\.(mp3|m4a|ogg|opus)$", re.I)


TRASH = "Recycle Bin" if os.name == "nt" else "Trash"


def recycle(paths):
    """Send files to the Windows Recycle Bin or the Mac's Trash (so a replaced split can
    be restored)."""
    paths = [str(Path(p).resolve()) for p in paths]
    if not paths:
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        buf = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
        # FO_DELETE with FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI |
        # FOF_WANTNUKEWARNING: without the last, a file the Recycle Bin can't take (bin
        # full or turned off) is deleted for good with no word; with it Windows asks first
        op = SHFILEOPSTRUCTW(None, 3, ctypes.cast(buf, ctypes.c_void_p), None,
                             0x40 | 0x10 | 0x04 | 0x400 | 0x4000, False, None, None)
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
    if sys.platform == "darwin":
        # macOS 15 has a trash command; before it, ask the Finder (macOS asks once whether
        # the app may). Either way "Put Back" in the Trash restores a file. If neither
        # works, the files stay where they are and the caller says so.
        if os.path.exists("/usr/bin/trash"):
            cmd = ["/usr/bin/trash"] + paths
        else:
            script = ["on run argv", "set fs to {}", "repeat with p in argv",
                      "set end of fs to (POSIX file (contents of p)) as alias", "end repeat",
                      'tell application "Finder" to delete fs', "end run"]
            cmd = ["osascript"] + [a for line in script for a in ("-e", line)] + paths
        try:
            return subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


PIECE_SIDECAR = re.compile(r"^\d+ - .+\.(tracklist\.csv|cue)$", re.I)


def row_kind(r):
    """What a tracklist row marks: 'set' (a festival performance), 'program' (a WWOZ
    show beginning), 'between' (talk between sets), 'record' (a record played between
    sets), or 'song' (a song inside a show, from the DJs' log, Shazam or typed in)."""
    show = str(r.get("show") or "")
    if show.startswith("set @"):
        return "set"
    if show.startswith("autocut"):
        if " @ " in show:
            return "set"
        if "WWOZ show" in show:
            return "program"
        if "record" in show:
            return "record"
        return "set" if r.get("artist") == "Unknown performance" else "between"
    if show == "WWOZ program":
        return "program"
    if show == "manual marker":
        return "song" if r.get("song") else "set"
    return "song"


def pieces_index(folder):
    """A cut folder's pieces.json (what split_pieces wrote), or None."""
    try:
        return json.loads((Path(folder) / "pieces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def unmarked_shows(path):
    """Shows the DJs logged songs under that no marker in the tracklist starts. A
    recording made outside festival hours gets its show changes from the station
    calendar (add_program_markers); one where that never ran, or ran before the
    recording was finished, would be cut into one piece many hours long."""
    rows = read_tracklist(path)
    marked = {family_key(show_family(r["artist"])[0]) for r in rows if row_kind(r) == "program"}
    first = min((r["offset_seconds"] for r in rows if row_kind(r) != "song"), default=None)
    return {r["show"] for r in rows
            if row_kind(r) == "song" and r.get("show") not in ("", "shazam", "manual marker")
            and first is not None and r["offset_seconds"] > first      # the show already on at the start
            and family_key(show_family(r["show"])[0]) not in marked}   # is cut by split_pieces itself


def pieces_stale(path):
    """Does a recording need cutting (again)? True when it has a tracklist but no
    pieces.json yet, or the tracklist changed after the last cut."""
    path = Path(path)
    tl = tracklist_csv(path)
    if not tl.exists() or not [r for r in read_tracklist(path) if not is_placeholder_spin(r)]:
        return False
    idx = path.parent / (path.stem + " - tracks") / "pieces.json"
    return not idx.exists() or tl.stat().st_mtime > idx.stat().st_mtime + 2


def split_pieces(path, lead_in=15, out_dir=None):
    """Cut a recording into whole pieces: every festival set, every WWOZ show, every
    break between sets. Songs are not cut out of a show any more; they go into the
    show's own tracklist, so the Player lists them when that show is opened. Writes
    pieces.json, which tells the library what each piece is. Returns the folder.

    Files keep the numbering the song-by-song split gave them (the number of the
    tracklist row that starts the piece), so a set's file name does not change."""
    path = Path(path)
    rows = [r for r in read_tracklist(path) if not is_placeholder_spin(r)]
    if not rows:
        raise ToolError(f"{path.name} has no tracklist to cut by")
    ffmpeg = find_tool("ffmpeg")
    total = ffprobe_duration(path)
    start = recording_start(path, total)
    fest = festival_for(path)
    out_dir = Path(out_dir) if out_dir else path.parent / (path.stem + " - tracks")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in out_dir.iterdir() if SPLIT_NAME.match(p.name) or PIECE_SIDECAR.match(p.name)}
    codec = subprocess.run([find_tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, creationflags=NOWIN, timeout=120).stdout.strip()
    ext = {"mp3": ".mp3", "aac": ".m4a", "vorbis": ".ogg", "opus": ".opus"}.get(codec, ".mp3")

    def lead(r):
        show = str((r or {}).get("show", ""))
        if r is None:
            return 0
        return 3 if show.startswith("autocut") else 5 if show == "WWOZ program" else lead_in

    # every row that isn't a song starts a piece; a show marked twice in a row (the
    # calendar's change, then the first song the DJ logged) stays one piece
    starts = []
    for i, r in enumerate(rows):
        kind = row_kind(r)
        if kind == "song":
            continue
        if (starts and kind == "program" and starts[-1]["kind"] == "program"
                and loose(starts[-1]["name"]) == loose(r["artist"])):
            continue
        starts.append({"n": i + 1, "row": r, "kind": kind, "name": r["artist"],
                       "off": int(r["offset_seconds"])})
    songs = [r for r in rows if row_kind(r) == "song"]
    # before the first marker: the show that was on when the recording began
    first = starts[0]["off"] if starts else int(total)
    if first > 120:
        import collections
        on_air = collections.Counter(r["show"] for r in songs if int(r["offset_seconds"]) < first
                                     and r.get("show") not in ("", "shazam", "manual marker")).most_common(1)
        if not on_air and start and first > 600:     # nothing logged: ask the weekly schedule
            slot = slot_at(start + timedelta(seconds=60))
            if slot:
                on_air = [(slot["title"], 0)]
        if starts and starts[0]["kind"] == "program" and (not on_air or loose(on_air[0][0]) == loose(starts[0]["name"])):
            starts[0]["t0"] = 0                     # the same show was already on
        elif on_air:
            starts.insert(0, {"n": 0, "row": None, "kind": "program", "name": on_air[0][0], "off": 0})
    if not starts:
        raise ToolError(f"{path.name}: nothing in its tracklist says where to cut")

    def stage_of(r):
        show = str((r or {}).get("show") or "")
        return show.split(" @ ", 1)[1] if " @ " in show else ""

    pieces, written = [], set()
    for k, s in enumerate(starts):
        t0 = s.get("t0", max(0, s["off"] - lead(s["row"])))
        nxt = starts[k + 1] if k + 1 < len(starts) else None
        t1 = max(0, nxt["off"] - lead(nxt["row"])) if nxt else int(total)
        if t1 - t0 < 5:
            continue
        name = sanitize(f"{s['n']:02} - {s['name']}") + ext
        dest = out_dir / name
        stage = stage_of(s["row"])
        day = (start + timedelta(seconds=t0)) if start else None     # when the piece's file begins
        if s["kind"] == "set":
            album = (f"{fest['name']} (WWOZ broadcast)" if fest else
                     f"Jazz Fest {day:%Y} (WWOZ broadcast)" if day else "Jazz Fest (WWOZ broadcast)")
            meta = [f"artist={s['name']}", f"title={s['name']} live" + (f", {stage}" if stage else ""),
                    f"album={album}"]
        elif s["kind"] == "program":
            meta = ["artist=WWOZ 90.7 FM", f"title={s['name']}", f"album={s['name']}"]
        else:
            meta = ["artist=WWOZ 90.7 FM", f"title={s['name']}", f"album={path.stem}"]
        meta += [f"track={len(pieces) + 1}"] + ([f"date={day:%Y-%m-%d}"] if day else [])
        tmp = dest.with_name(dest.stem + ".partial" + ext)
        res = subprocess.run([ffmpeg, "-v", "error", "-ss", str(t0), "-to", str(t1), "-i", str(path),
                              "-vn", "-c:a", "copy", "-map_metadata", "-1",
                              *[x for m in meta for x in ("-metadata", m)], "-y", str(tmp)],
                             capture_output=True, text=True, creationflags=NOWIN, timeout=1800)
        if res.returncode or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            out(f"  {name}: FAILED ({(res.stderr or '').strip()[:200]})")
            if name in before:
                written.add(name)              # keep the earlier cut rather than recycle it
            continue
        os.replace(tmp, dest)
        written.add(name)
        inside = [dict(r, offset_seconds=int(r["offset_seconds"]) - t0,
                       offset=fmt_off(int(r["offset_seconds"]) - t0))
                  for r in songs if t0 <= int(r["offset_seconds"]) < t1]
        if inside:                             # the show's own song list, for the Player
            write_tracklist(dest, inside)
            written.update({tracklist_csv(dest).name, Path(str(dest.with_suffix("")) + ".cue").name})
        pieces.append({"file": name, "kind": s["kind"], "name": s["name"],
                       "label": str((s["row"] or {}).get("show") or ""), "stage": stage,
                       "offset": t0, "seconds": t1 - t0, "songs": len(inside),
                       "clock": day.isoformat() if day else None})
        out(f"  {name}  ({fmt_off(t0)} -> {fmt_off(t1)}, {s['kind']}"
            + (f", {len(inside)} songs" if inside else "") + ")")
    idx = out_dir / "pieces.json"
    tmp_idx = idx.with_suffix(".json.partial")
    tmp_idx.write_text(json.dumps({"recording": path.name, "start": start.isoformat() if start else None,
                                   "pieces": pieces}, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_idx, idx)
    stale = sorted(before - written)           # e.g. the songs of the old song-by-song split
    if stale:
        if recycle([out_dir / n for n in stale]):
            out(f"  {len(stale)} file(s) from the previous cut moved to the {TRASH}")
        else:
            out(f"  note: {len(stale)} file(s) from an earlier cut are still in the folder")
    out(f"{len(pieces)} piece(s) in: {out_dir}")
    return out_dir


def cmd_split(args):
    if not getattr(args, "songs", False):
        split_pieces(Path(args.file), lead_in=args.lead_in, out_dir=args.out)
        return
    path = Path(args.file)
    csv_path = Path(args.tracklist) if args.tracklist else tracklist_csv(path)
    if not csv_path.exists():
        sys.exit(f"ERROR: {csv_path} not found. Run `wwoz.py tracklist` first.")
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: int(float(r.get("offset_seconds") or 0)))
    if not rows:
        sys.exit("Tracklist is empty; nothing to split.")
    ffmpeg = find_tool("ffmpeg")
    total = ffprobe_duration(path)
    start = recording_start(path, total)
    fest = festival_for(path)
    if fest:
        # A record the DJs logged during a festival broadcast was played while the
        # stages changed over; cutting there would hand the rest of the set to it.
        keep, in_set = [], False
        for r in rows:
            show = str(r.get("show") or "")
            if show.startswith("set @"):
                in_set = True
            elif show == "WWOZ program":
                in_set = False
            elif in_set:
                continue
            keep.append(r)
        rows = keep
    out_dir = Path(args.out) if args.out else path.parent / (path.stem + " - tracks")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in out_dir.iterdir() if SPLIT_NAME.match(p.name)}

    codec = subprocess.run([find_tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, creationflags=NOWIN).stdout.strip()
    ext = {"mp3": ".mp3", "aac": ".m4a", "vorbis": ".ogg", "opus": ".opus"}.get(codec, ".mp3")

    def lead_for(r):
        # detected sets get a long lead-in so the DJ's stage introduction is
        # included; program boundaries need almost none; songs a touch
        show = str(r.get("show", ""))
        if show.startswith("autocut"):
            return 3   # marker already sits at the start of the DJ's introduction
        if show == "WWOZ program":
            return 5
        return args.lead_in

    def tags(r, n):
        """What a music player shows: a festival set is filed under the festival and
        its stage, a song under the show it was played on."""
        show = str(r.get("show") or "")
        stage = show.split(" @ ", 1)[1] if show.startswith("autocut") and " @ " in show else ""
        if show.startswith("set @"):
            stage = show[len("set @ "):]
        title = r["song"] or r["artist"]
        if stage and fest:
            album = f"{fest['name']} (WWOZ broadcast)"
            title = r["song"] or f"{r['artist']} live, {stage}"
        elif stage:
            album = f"Jazz Fest {start:%Y} (WWOZ broadcast)" if start else "Jazz Fest (WWOZ broadcast)"
            title = r["song"] or f"{r['artist']} — {stage}"
        elif show.startswith("autocut") or show == "WWOZ program":
            album = r["artist"]                 # the WWOZ show on the air
        elif show in ("", "manual marker", "shazam"):
            album = path.stem
        else:
            album = show
        t = ["-metadata", f"artist={r['artist']}", "-metadata", f"title={title}",
             "-metadata", f"album={album}", "-metadata", f"track={n}"]
        if start:
            t += ["-metadata", f"date={start:%Y-%m-%d}"]
        if show.startswith("autocut"):
            t += ["-metadata", f"comment={show}"]
        return t

    # The festival's 'LIVE BROADCAST' stand-in rows are not songs; cutting at them
    # chopped performances in two.
    rows = [r for r in rows if not is_placeholder_spin(r)]
    written = set()
    for i, r_ in enumerate(rows):
        t0 = max(0, int(float(r_["offset_seconds"])) - lead_for(r_))
        t1 = (int(float(rows[i + 1]["offset_seconds"])) - lead_for(rows[i + 1])
              if i + 1 < len(rows) else int(total))
        if t1 <= t0:
            continue
        parts = [p for p in (r_["artist"], r_["song"]) if p]
        name = sanitize(f"{i+1:02} - " + " - ".join(parts)) + ext
        dest = out_dir / name
        tmp = dest.with_name(dest.stem + ".partial" + ext)    # the old file stays until this one is good
        cmd = [ffmpeg, "-v", "error", "-ss", str(t0), "-to", str(t1), "-i", str(path),
               "-vn", "-c:a", "copy", *tags(r_, i + 1), "-y", str(tmp)]
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN)
        if res.returncode or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            out(f"  {name}: FAILED ({(res.stderr or '').strip()[:200]})")
            if name in before:
                written.add(name)               # keep the earlier cut rather than recycle it
            continue
        os.replace(tmp, dest)
        written.add(name)
        out(f"  {name}  ({fmt_off(t0)} -> {fmt_off(t1)})")
    stale = sorted(before - written)      # left from an earlier split with other labels
    if stale:
        if recycle([out_dir / n for n in stale]):
            out(f"\n  {len(stale)} file(s) from the previous split moved to the {TRASH}")
        else:
            out(f"\n  note: {len(stale)} file(s) from an earlier split are still in the folder")
    out(f"\nTracks written to: {out_dir}")
    out("Note: detected sets start right where the DJ begins the introduction;")
    out("song boundaries fall where the DJ logged them (tune with --lead-in).")


# --------------------------------------------------------------- autocut ---

def cmd_autocut(args):
    import wwoz_autocut
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"ERROR: {path} not found")
    wwoz_autocut.autocut(path, start_off=args.start_offset, end_off=args.end_offset,
                         transcribe_regions=not args.no_transcribe, shazam=not args.no_shazam)


# -------------------------------------------------------------- programs ---

def add_program_markers(path: Path):
    """Insert a marker at every WWOZ program change (from the station calendar)
    covering the parts of a recording OUTSIDE festival broadcast hours, so
    morning shows / evening shows / overnight blocks split into named files."""
    start = start_time_from_name(path)
    duration = ffprobe_duration(path)
    if start is None:
        start = datetime.fromtimestamp(path.stat().st_mtime, tz=TZ) - timedelta(seconds=duration)
    end = start + timedelta(seconds=duration)

    # festival window to leave alone (set markers already live there)
    fest = []
    guide = load_fest_schedule()
    if guide:
        d = start
        while d < end + timedelta(days=1):
            day = d.strftime("%Y-%m-%d")
            if day in guide:
                y, mo, dd = map(int, day.split("-"))
                # WWOZ stays on the festival until 7 PM even when the gates close early
                # (it plays recordings from earlier sets), so that is the window
                last = max([s["end"] for s in guide[day]] + ["19:00"])
                h, m = map(int, last.split(":"))
                fest.append((datetime(y, mo, dd, 10, 45, tzinfo=TZ),
                             datetime(y, mo, dd, h, m, tzinfo=TZ)))
            d += timedelta(days=1)

    def in_fest(t):
        return any(a - timedelta(minutes=30) <= t < b - timedelta(minutes=1)
                   for a, b in fest)

    csv_path = Path(str(path.with_suffix("")) + ".tracklist.csv")
    rows = []
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    for r in rows:
        r["offset_seconds"] = int(float(r.get("offset_seconds") or 0))
    rows = [r for r in rows if r.get("show") != "WWOZ program"]  # rebuild these

    added = 0
    for ev in spinitron_events(start, end, include_unlogged=True):
        off = int((ev["start"] - start).total_seconds())
        if off < 60 or off > duration - 120 or in_fest(ev["start"]):
            continue
        # another marker already starts a piece here (a DJ's first song logged at the top
        # of the hour doesn't count: it used to stop the show's own marker being added)
        if any(abs(r["offset_seconds"] - off) < 150 for r in rows if row_kind(r) != "song"):
            continue
        rows.append({"offset": fmt_off(off),
                     "clock_time": ev["start"].strftime("%Y-%m-%d %I:%M %p"),
                     "artist": ev["title"], "song": "", "release": "",
                     "show": "WWOZ program", "offset_seconds": off})
        added += 1
    rows.sort(key=lambda r: r["offset_seconds"])
    write_tracklist(path, rows)                 # the .cue too, so players see the new chapters
    out(f"  {path.name}: +{added} program markers ({len(rows)} rows total)")
    return added


def cmd_programs(args):
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"ERROR: {path} not found")
    add_program_markers(path)


# --------------------------------------------------- festival broadcasts ---
# WWOZ carries some festivals live in place of its regular shows (the Crescent City
# Blues & BBQ Festival, for one). The archive keeps each broadcast under the regular
# show's file name; only its title says what aired: "Live from the Crescent City
# Blues and BBQ Fest: Marc Stone, Kenny Brown". A fest-*.json beside the code holds
# the festival's set times, which name the sets and say where to cut them.

def show_info_path(path):
    return Path(str(Path(path).with_suffix("")) + ".show.json")


def remember_title(show, dest: Path):
    """Keep an archive show's title beside its download, since the file name is the
    regular show's and is wrong whenever WWOZ aired something else in the slot."""
    p = show_info_path(dest)
    info = {"title": show["title"], "date": show["date"], "time_label": show["time_label"]}
    try:
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        old = old if isinstance(old, dict) else {}
        if all(old.get(k) == v for k, v in info.items()):
            return
        # (keeping anything else noted there, such as the festival act a slot continues)
        p.write_text(json.dumps(dict(old, **info), indent=1, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass


def remember_titles(shows, dest_dir: Path):
    """remember_title for every listed show that is already downloaded."""
    for s in shows:
        dest = Path(dest_dir) / s["filename"]
        if dest.exists():
            remember_title(s, dest)


def archive_title(path):
    """The archive's title for a download, or None if it was never recorded."""
    try:
        return json.loads(show_info_path(path).read_text(encoding="utf-8")).get("title")
    except (OSError, ValueError, AttributeError):
        return None


_LIVE_FROM = re.compile(r"^Live (?:from|at) (?:the )?([^:]+):\s*(.+)$", re.I)


def broadcast_parts(title):
    """Split 'Old Time Country and Bluegrass / Live from the Crescent City Blues and
    BBQ Fest: Dexter Allen' into the festival, its acts, and the slot's parts in
    airing order (None where the festival aired)."""
    fest = acts = None
    order = []
    for part in (p.strip() for p in (title or "").split(" / ")):
        m = _LIVE_FROM.match(part)
        if m and fest is None:
            fest, acts = m.group(1).strip(), m.group(2).strip()
            order.append(None)
        elif part:
            order.append(part)
    return {"festival": fest, "acts": acts, "order": order}


def load_broadcast_festivals():
    """Every festival with a fest-*.json beside the code."""
    fests = []
    for p in sorted(SCRIPT_DIR.glob("fest-*.json")):
        try:
            fests.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            out(f"  (skipping {p.name}: {e})")
    return fests


def festival_for(path, fests=None):
    """The broadcast festival a download belongs to, judged by its archive title and its
    date: the titles carry no year, so next October's broadcasts of the same festival
    must not count as this year's."""
    title = loose(archive_title(path))
    if not title:
        return None
    st = start_time_from_name(Path(path))
    for f in load_broadcast_festivals() if fests is None else fests:
        if loose(f.get("title_match")) and loose(f["title_match"]) in title and _in_fest_dates(f, st):
            return f
    return None


def _in_fest_dates(fest, when):
    """Whether a time falls within a festival's days, give or take a day (a file with no
    time in its name, or a festival with no days, is not ruled out)."""
    days = sorted(fest.get("days") or {})
    if not when or not days:
        return True
    first, last = _clock(days[0], "00:00") - timedelta(days=1), _clock(days[-1], "00:00") + timedelta(days=2)
    return first <= when < last


def festival_files(fest, dest_dir=None):
    """A festival's broadcast downloads, oldest first: those whose archive title names
    it, and any download that starts while a set is on, because a set that runs long
    spills into a slot the archive titles only by its regular show (Samantha Fish
    ran on into 'Soul Power with Soul Sister' on 2026-10-10)."""
    dest_dir = Path(dest_dir) if dest_dir else download_dir_from_config(load_config())
    days = _set_hours(fest)

    def during(p):
        st = start_time_from_name(p)
        return bool(st) and any(a <= st < b for a, b in days)
    return sorted(p for p in dest_dir.glob("*.mp3") if festival_for(p, [fest]) or during(p))


def _set_hours(fest):
    """[(first set's start, last set's end)] for each day of a festival's schedule."""
    return [(_clock(day, min(s["start"] for s in rows)), _clock(day, max(s["end"] for s in rows)))
            for day, rows in fest.get("days", {}).items() if rows]


def festival_folder(fest):
    """Where a festival's per-set files go (inside the recordings folder)."""
    return RECORDINGS_DIR / sanitize(fest.get("short") or fest["name"])


def _clock(day, hhmm):
    y, mo, d = map(int, day.split("-"))
    h, m = map(int, hhmm.split(":"))
    return datetime(y, mo, d, h, m, tzinfo=TZ)


def set_bounds(fest, files):
    """When each set was on the air: [(begin, end, set)], from the schedule, moved past
    the records the DJs logged while the stages changed over. A record logged just
    before a set's scheduled start, or within six minutes after it, means the set had
    not begun yet; one logged near the day's last scheduled end means regular
    programming had resumed. Records logged later than that are not trusted: on
    2026-10-09 a record logged at 5:44 PM never aired, and Johnny Sansone had been
    playing since 5:29."""
    records = []
    for p in files:
        st = start_time_from_name(p)
        if st:
            records += [st + timedelta(seconds=int(r["offset_seconds"]))
                        for r in read_tracklist(p) if not is_marker(r)]
    records.sort()
    sets = sorted(((_clock(day, s["start"]), _clock(day, s["end"]), dict(s, day=day))
                   for day, rows in fest.get("days", {}).items() for s in rows),
                  key=lambda x: x[0])
    begins = []
    for i, (s0, _, _) in enumerate(sets):
        first = i == 0 or sets[i - 1][0].date() != s0.date()
        near = [t for t in records if s0 - timedelta(minutes=20) <= t <= s0 + timedelta(minutes=6)]
        begins.append(max(near) + timedelta(seconds=90) if near
                      else s0 - timedelta(minutes=5 if first else 1))
    bounds = []
    for i, (s0, s1, s) in enumerate(sets):
        if i + 1 < len(sets) and sets[i + 1][0].date() == s0.date():
            end = begins[i + 1]
        else:
            after = [t for t in records if s1 - timedelta(minutes=20) <= t <= s1 + timedelta(minutes=30)
                     and t > begins[i]]
            end = min(after) - timedelta(seconds=30) if after else s1 + timedelta(minutes=10)
        bounds.append((begins[i], end, s))
    return bounds


def _windows(files):
    """(start, end, path) per download; each stops where the next begins, since the
    archive's hour-aligned files overlap by a few seconds."""
    w = sorted((st, st + timedelta(seconds=ffprobe_duration(p)), p)
               for p in files if (st := start_time_from_name(p)))
    return [(a, min(b, w[i + 1][0]) if i + 1 < len(w) else b, p) for i, (a, b, p) in enumerate(w)]


def mark_festival(fest, dest_dir=None):
    """Put a marker at the start of every set (and of the regular show sharing the
    slot) into each broadcast download's tracklist, so the Player lists the sets."""
    files = festival_files(fest, dest_dir)
    if not files:
        return 0
    bounds = set_bounds(fest, files)
    stages = {f"set @ {s['stage']}" for day in fest["days"].values() for s in day}
    for w0, w1, p in _windows(files):
        span = (w1 - w0).total_seconds()

        def row(t, artist, show):
            off = max(0, int((t - w0).total_seconds()))
            return {"offset": fmt_off(off), "clock_time": max(t, w0).strftime("%Y-%m-%d %I:%M %p"),
                    "artist": artist, "song": "", "release": "", "show": show, "offset_seconds": off}
        new = [row(a, s["artist"] + (" (continued)" if a < w0 - timedelta(minutes=5) else ""),
                   f"set @ {s['stage']}")
               for a, b, s in bounds if a < w1 - timedelta(seconds=60) and b > w0 + timedelta(seconds=60)]
        order = broadcast_parts(archive_title(p))["order"]
        if None not in order:                 # titled only by the regular show it ran into
            order = [None] + order if new and new[0]["offset_seconds"] == 0 else order + [None]
        if new:
            k = order.index(None)
            before, after = order[:k], order[k + 1:]
            ours = [(a, b) for a, b, _ in bounds if a < w1 and b > w0]
            if before and min(a for a, _ in ours) > w0 + timedelta(seconds=60):
                new.append(row(w0, before[-1], "WWOZ program"))
            end = max(b for _, b in ours)
            if after and end < w1 - timedelta(seconds=60):
                new.append(row(end, after[0], "WWOZ program"))
        keep = [r for r in read_tracklist(p) if r.get("show") not in stages | {"WWOZ program"}]
        write_tracklist(p, sorted(keep + new, key=lambda r: int(r["offset_seconds"])))
        out(f"  {p.name}: {sum(r['show'] != 'WWOZ program' for r in new)} set marker(s)"
            f" ({fmt_off(span)} long)")
    return len(bounds)


def cut_festival(fest, dest_dir=None):
    """One tagged mp3 per set, joined across the archive's 2-hour files where a set
    straddles two, into the festival's folder. Returns the folder."""
    files = festival_files(fest, dest_dir)
    if not files:
        out(f"No downloads of {fest['name']} yet.")
        return None
    ffmpeg = find_tool("ffmpeg")
    windows = _windows(files)
    out_dir = festival_folder(fest)
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in out_dir.iterdir() if SPLIT_NAME.match(p.name)}
    earlier = _festival_index(out_dir)
    earlier_sets = {e["file"]: e for e in earlier.get("sets", [])}
    album = f"{fest['name']} (WWOZ broadcast)"
    index, written = [], set()

    def keep_earlier(name, why):
        """A set that can't be cut this time keeps the file an earlier cut made."""
        if name in earlier_sets and (out_dir / name).exists():
            index.append(earlier_sets[name])
            written.add(name)
            out(f"  {name}: {why}; the earlier cut is kept")
            return True
        return False

    have = {p.name for p in files}
    out(f"Cutting {fest['name']} into sets, from {len(files)} broadcast download(s):")
    for n, (a, b, s) in enumerate(set_bounds(fest, files), 1):
        pieces = [(max(a, w0), min(b, w1), p, w0) for w0, w1, p in windows if w0 < b and w1 > a]
        name = sanitize(f"{n:02} - " + s["artist"].replace('"', "'")) + ".mp3"
        old_from = set(earlier_sets.get(name, {}).get("from_files", []))
        if old_from - have and keep_earlier(name, "a download it was cut from is gone"):
            continue                  # cutting again would leave only part of the set
        if not pieces:
            out(f"  {s['artist']}: not in the downloads")
            continue
        dest = out_dir / name
        tmp_dest = out_dir / (name[:-4] + ".partial.mp3")
        tags = ["-map_metadata", "-1", "-id3v2_version", "3",
                "-metadata", f"artist={s['artist']}",
                "-metadata", f"title={s['artist']} live, {s['stage']}",
                "-metadata", f"album={album}", "-metadata", f"track={n}",
                "-metadata", f"date={s['day']}",
                "-metadata", f"comment=Scheduled {s['start']}-{s['end']}, cut by the schedule "
                             f"and the DJs' log, so it may start or end a little off."]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            parts, failed = [], ""
            for k, (x, y, p, w0) in enumerate(pieces):
                part = Path(tmp) / f"{k}.mp3"
                rp = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{(x - w0).total_seconds():.2f}",
                                     "-to", f"{(y - w0).total_seconds():.2f}", "-i", str(p), "-vn",
                                     "-c:a", "copy", "-map_metadata", "-1", "-y", str(part)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    creationflags=NOWIN, timeout=1800)
                if rp.returncode or not part.exists() or part.stat().st_size == 0:
                    failed = (rp.stderr or "").strip()[:200] or "no sound came out"
                    break
                parts.append(part)
            if not failed:
                if len(parts) == 1:
                    src = ["-i", str(parts[0])]
                else:
                    # bare names, which ffmpeg finds beside the list: a full path breaks
                    # on an apostrophe, as in a Windows user folder named O'Brien
                    lst = Path(tmp) / "parts.txt"
                    lst.write_text("".join(f"file '{q.name}'\n" for q in parts), encoding="utf-8")
                    src = ["-f", "concat", "-safe", "0", "-i", str(lst)]
                r = subprocess.run([ffmpeg, "-v", "error", *src, "-vn", "-c:a", "copy", *tags, "-y",
                                    str(tmp_dest)], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", creationflags=NOWIN, timeout=1800)
                if r.returncode or not tmp_dest.exists() or tmp_dest.stat().st_size == 0:
                    failed = (r.stderr or "").strip()[:200] or "no sound came out"
        if failed:                    # the earlier cut, if any, stays as it was
            tmp_dest.unlink(missing_ok=True)
            if not keep_earlier(name, f"ffmpeg failed ({failed})"):
                out(f"  {name}: ffmpeg failed ({failed})")
            continue
        os.replace(tmp_dest, dest)
        written.add(name)
        length = sum((y - x).total_seconds() for x, y, *_ in pieces)
        index.append({"file": name, "artist": s["artist"], "stage": s["stage"], "day": s["day"],
                      "start": s["start"], "end": s["end"],
                      "aired_from": a.strftime("%H:%M:%S"), "aired_to": b.strftime("%H:%M:%S"),
                      "seconds": int(length), "from_files": [p.name for _, _, p, _ in pieces]})
        out(f"  {name}  ({a:%a %I:%M %p} to {b:%I:%M %p}, {fmt_off(length)}"
            + (f", joined from {len(pieces)} files" if len(pieces) > 1 else "") + ")")
    programs = cut_festival_programs(fest, files, windows, out_dir, earlier.get("programs", []))
    tmp_idx = out_dir / "festival.json.partial"
    tmp_idx.write_text(json.dumps(
        {"name": fest["name"], "short": fest.get("short"), "place": fest.get("place"),
         "sets": index, "programs": programs}, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_idx, out_dir / "festival.json")
    stale = sorted(before - written)
    if stale and recycle([out_dir / n for n in stale]):
        out(f"  {len(stale)} file(s) from an earlier cut moved to the {TRASH}")
    out(f"\n{len(written)} set(s) and {len(programs)} WWOZ show(s) written to: {out_dir}")
    return out_dir


def _festival_index(out_dir):
    """A festival folder's festival.json from the last cut ({} if none or unreadable)."""
    try:
        return json.loads((Path(out_dir) / "festival.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cut_festival_programs(fest, files, windows, out_dir, earlier=()):
    """The regular WWOZ shows that shared the festival broadcasts' slots (Jazz from Jax
    Brewery before the first set, Spirits of Congo Square after the last), cut out as
    whole shows with their own song lists, into the festival folder's 'WWOZ shows'
    subfolder. The library files them with the other episodes of each show. A show
    that fails to cut, or whose download is gone, keeps its earlier cut (`earlier`:
    the programs listed in the last festival.json)."""
    earlier = {e["file"]: e for e in earlier}
    bounds = set_bounds(fest, files)
    prog_dir = out_dir / "WWOZ shows"
    prog_dir.mkdir(exist_ok=True)
    before = {p.name for p in prog_dir.iterdir() if p.suffix.lower() in (".mp3", ".csv", ".cue")}
    written, programs = set(), []
    ffmpeg = find_tool("ffmpeg")
    for w0, w1, p in windows:
        ours = [(a, b) for a, b, _ in bounds if a < w1 and b > w0]
        if not ours:
            continue
        order = broadcast_parts(archive_title(p))["order"]
        first, last = min(a for a, _ in ours), max(b for _, b in ours)
        if None not in order:                 # titled only by the regular show it ran into
            order = [None] + order if first <= w0 + timedelta(minutes=5) else order + [None]
        k = order.index(None)
        spans = []
        if first - w0 > timedelta(minutes=5):
            spans.append((w0, first, order[k - 1] if k else None))
        if w1 - last > timedelta(minutes=5):
            spans.append((last, w1, order[k + 1] if k + 1 < len(order) else None))
        rows = [r for r in read_tracklist(p) if row_kind(r) == "song"]
        for x, y, name in spans:
            o0, o1 = int((x - w0).total_seconds()), int((y - w0).total_seconds())
            inside = [dict(r, offset_seconds=int(r["offset_seconds"]) - o0,
                           offset=fmt_off(int(r["offset_seconds"]) - o0))
                      for r in rows if o0 <= int(r["offset_seconds"]) < o1]
            if not name:                      # name it after the show the DJs logged songs under
                logged = [r["show"] for r in inside if r.get("show") not in ("", "shazam", "manual marker")]
                name = max(set(logged), key=logged.count) if logged else "WWOZ"
            fname = sanitize(f"{x:%Y-%m-%d %I.%M%p} {name}") + ".mp3"
            dest = prog_dir / fname
            tmp = dest.with_name(dest.stem + ".partial.mp3")
            r = subprocess.run([ffmpeg, "-v", "error", "-ss", str(o0), "-to", str(o1), "-i", str(p), "-vn",
                                "-c:a", "copy", "-map_metadata", "-1", "-metadata", "artist=WWOZ 90.7 FM",
                                "-metadata", f"title={name}", "-metadata", f"album={name}",
                                "-metadata", f"date={x:%Y-%m-%d}", "-y", str(tmp)],
                               capture_output=True, text=True, creationflags=NOWIN, timeout=1800)
            if r.returncode or not tmp.exists() or tmp.stat().st_size == 0:
                tmp.unlink(missing_ok=True)
                out(f"  WWOZ show {fname}: ffmpeg failed ({(r.stderr or '').strip()[:200]})")
                old = earlier.get(f"WWOZ shows/{fname}")
                if old and dest.exists():               # keep the earlier cut of it
                    written.update({fname, tracklist_csv(dest).name, dest.stem + ".cue"})
                    programs.append(old)
                continue
            os.replace(tmp, dest)
            written.add(fname)
            if inside:
                write_tracklist(dest, inside)
                written.update({tracklist_csv(dest).name, dest.stem + ".cue"})
            programs.append({"file": f"WWOZ shows/{fname}", "name": name, "clock": x.isoformat(),
                             "seconds": o1 - o0, "songs": len(inside), "from_file": p.name})
            out(f"  WWOZ show: {name}  ({x:%a %I:%M %p} to {y:%I:%M %p}, {len(inside)} songs)")
    have = {p.name for p in files}
    for key, old in earlier.items():                  # shows whose download is gone stay
        dest = out_dir / key
        if old.get("from_file") not in have and dest.name not in written and dest.exists():
            written.update({dest.name, tracklist_csv(dest).name, dest.stem + ".cue"})
            programs.append(old)
    stale = sorted(before - written)
    if stale:
        recycle([prog_dir / n for n in stale])
    return programs


def cmd_festival(args):
    fests = load_broadcast_festivals()
    if args.name:
        fests = [f for f in fests if loose(args.name) in loose(f["name"] + " " + f.get("short", ""))]
    if not fests:
        sys.exit("No festival schedule matches (fest-*.json beside wwoz.py).")
    dest_dir = download_dir_from_config(load_config())
    for f in fests:
        if not festival_files(f, dest_dir):
            out(f"{f['name']}: no broadcast downloads yet.")
            continue
        mark_festival(f, dest_dir)
        if not args.markers_only:
            cut_festival(f, dest_dir)


# ---------------------------------------------- festivals, on their own ---
# The archive keeps a festival broadcast for two weeks only, so the Sunday task and the
# open app download the festivals WWOZ carries live without being asked: every slot the
# archive titles "Live from <a festival>: ...", the slot after one whose last act runs
# on, and, for a festival with set times (a fest-*.json), any slot that starts while a
# set is on. Only from the day the feature began on this computer ('auto_festivals_from'
# in the settings), so the festivals already in the archive then are left alone; and
# each broadcast once only (festival_downloads.json), so deleting the downloads after
# their sets are cut doesn't bring them back. A festival with set times is cut into sets
# as before; without them its broadcasts stay whole, and the app's library lists them
# under the festival, by day, named for their acts (broadcast_info).

FESTIVAL_RECORD = DATA_DIR / "festival_downloads.json"


def auto_festivals_on(cfg):
    return cfg.get("auto_festivals", True) is not False


def auto_festivals_from(cfg):
    """The first day (Central) whose festival broadcasts are downloaded on their own."""
    try:
        return datetime.strptime(str(cfg.get("auto_festivals_from")), "%Y-%m-%d").date()
    except ValueError:
        return datetime.now(TZ).date()            # unreadable: nothing from before today


def set_auto_festivals(on):
    """Turn the automatic festival downloads on or off (the box on the Weekly Favorites
    page). Turned back on, they start again from that day, so still nothing older."""
    cfg = load_config()
    if on and not auto_festivals_on(cfg):
        cfg["auto_festivals_from"] = f"{datetime.now(TZ):%Y-%m-%d}"
    cfg["auto_festivals"] = bool(on)
    save_config(cfg)
    if not on:                     # and no more live recordings (on again, the app schedules them)
        try:
            schedule_festival_recordings([])
        except Exception:
            pass
    return cfg


def festival_named(title, fests=None):
    """The festival an archive title says aired in its slot ('Crescent City Blues and BBQ
    Fest'), or None. A 'Live from' slot counts when the place is a festival (its name says
    Fest or Festival) or a fest-*.json names it; WWOZ's one-off concerts from a club or a
    theater ('Live from the Maple Leaf: ...') don't."""
    place = broadcast_parts(title)["festival"]
    if not place:
        return None
    if "fest" in place.lower():
        return place
    t = loose(title)
    fests = load_broadcast_festivals() if fests is None else fests
    if any(loose(f.get("title_match")) and loose(f["title_match"]) in t for f in fests):
        return place
    return None


_PART_AT_END = re.compile(r"([^,]+?)\s*\(pt\.?\s*\d+\)\s*$", re.I)


def _runs_on(title):
    """The act a festival slot ends with when it carries on into the next slot: 'Samantha
    Fish' for '...: Cedric Burnside (pt 2), Samantha Fish (pt 1)'. The archive titled
    the slot after that one only by its regular show, though she played on into it."""
    parts = broadcast_parts(title)
    if not parts["order"] or parts["order"][-1] is not None:
        return None                    # a regular show follows the festival in this slot
    m = _PART_AT_END.search(parts["acts"] or "")
    return m.group(1).strip() if m else None


def festival_downloads_record():
    """{file name: day downloaded} for the festival broadcasts downloaded on their own."""
    try:
        rec = json.loads(FESTIVAL_RECORD.read_text(encoding="utf-8")).get("downloaded", {})
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def remember_festival_download(show):
    rec = festival_downloads_record()
    rec[show["filename"]] = f"{datetime.now(TZ):%Y-%m-%d}"
    gone = datetime.now(TZ) - timedelta(days=60)          # long out of the archive by then
    rec = {k: v for k, v in rec.items() if not (st := start_time_from_name(Path(k))) or st > gone}
    write_text_atomic(FESTIVAL_RECORD, json.dumps(
        {"_help": "Festival broadcasts WWOZ Studio downloaded on its own, so each is downloaded "
                  "once only, even if you delete it afterwards.", "downloaded": rec}, indent=1))


def festival_picks(shows, cfg, dest_dir):
    """The archive shows (from fetch_archive_shows) to download because a festival aired
    in them, oldest first: none when the setting is off, none broadcast before its first
    day, none downloaded already or once before. Each carries '_festival' (its name); a
    slot that only continues an act carries '_continues' too ({'festival', 'act'})."""
    if not auto_festivals_on(cfg):
        return []
    first = auto_festivals_from(cfg)
    fests = load_broadcast_festivals()
    timed = sorted((s for s in shows if s.get("start")), key=lambda s: s["start"])
    picks = {}
    for i, s in enumerate(timed):
        if s["start"].date() < first:
            continue
        place = festival_named(s["title"], fests)
        if place:
            picks.setdefault(s["filename"], dict(s, _festival=place))
            act, nxt = _runs_on(s["title"]), (timed[i + 1] if i + 1 < len(timed) else None)
            if (act and nxt and nxt["start"] - s["start"] < timedelta(hours=4)
                    and not festival_named(nxt["title"], fests)):
                picks.setdefault(nxt["filename"],
                                 dict(nxt, _festival=place, _continues={"festival": place, "act": act}))
            continue
        for f in fests:                # a set that ran on into a slot titled by its regular show
            if any(a <= s["start"] < b for a, b in _set_hours(f)):
                picks.setdefault(s["filename"], dict(s, _festival=f.get("short") or f["name"]))
    done = festival_downloads_record()
    return [s for s in picks.values()
            if s["filename"] not in done and not (Path(dest_dir) / s["filename"]).exists()]


def download_festival_show(show, dest_dir):
    """Download one of festival_picks' shows, noting it so it is downloaded once only."""
    dest = download_show(show, dest_dir)
    if show.get("_continues"):
        p = show_info_path(dest)
        try:
            info = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            info["continues"] = show["_continues"]
            p.write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError):
            pass
    remember_festival_download(show)
    return dest


def broadcast_info(path, fests=None):
    """For a download a festival aired in: (the festival, what aired in the slot, the acts
    first since the library has room for little more), such as ('Crescent City Blues and
    BBQ Fest', 'Johnny Sansone, after Jazz from Jax Brewery with Charles Burchell'); else
    None."""
    try:
        info = json.loads(show_info_path(path).read_text(encoding="utf-8"))
        title = info.get("title") or ""
    except (OSError, ValueError, AttributeError):
        return None
    place = festival_named(title, fests)
    if place:
        parts = broadcast_parts(title)
        k = parts["order"].index(None)
        what = (parts["acts"] + "".join(f", after {x}" for x in parts["order"][:k])
                + "".join(f", then {x}" for x in parts["order"][k + 1:]))
        return place, what.replace(" (pt ", " (part ")
    cont = info.get("continues")
    if isinstance(cont, dict) and cont.get("festival") and cont.get("act"):
        return cont["festival"], f"{cont['act']} (continued), then {title}"
    return None


# ------------------------------------------------- festivals on the air ---
# Some festivals never reach the archive. WWOZ says some of its special live broadcasts
# "may not be included in the archive due to legal restrictions", and Jazz Fest is one,
# so those have to be recorded while they air. WWOZ lists its festival broadcasts ahead
# of time on its Live Event Broadcasts calendar, one entry a day with its hours (Jazz
# Fest 2026 went up a month early); its program schedule and Spinitron show only the
# regular shows. That calendar is the map: read by the app every few hours and by the
# Sunday task, it sets the times the computer records. The recording is its own program
# (`wwoz.py festival-record`), started by a Windows task that may wake the computer, or a
# Mac launch job, or by the app when it is open while a festival is on; it records from
# ten minutes before the listed start until half an hour after the listed end. A
# festival the archive does keep (the Blues & BBQ Fest) is noticed while it records: the
# recording then stops and is removed, and the archive downloads above take over.

LIVE_CALENDAR_URL = os.environ.get("WWOZ_LIVE_CALENDAR_URL") or WWOZ + "/calendar/live-broadcast"  # (tests)
LIVE_CALENDAR_PATH = DATA_DIR / "live_broadcasts.json"
RECORDING_LOCK = DATA_DIR / "festival-recording.lock"
RECORDING_LOG = DATA_DIR / "festival-recording.log"
RECORD_EARLY, RECORD_LATE = timedelta(minutes=10), timedelta(minutes=30)
ARCHIVE_LOOK = 20 * 60          # while recording, how often to see whether the archive keeps it
FESTIVAL_TASK = os.environ.get("WWOZ_FESTIVAL_TASK") or "WWOZ Festival Recording"      # (tests: another
FESTIVAL_AGENT = os.environ.get("WWOZ_FESTIVAL_AGENT") or "local.wwoz-studio.festival"  #  name)
# set only while testing: a scheduled recording made then keeps to the test's folders
_CARRIED = ("WWOZ_DATA", "WWOZ_RECORDINGS", "WWOZ_ARCHIVE_URL", "WWOZ_LIVE_CALENDAR_URL", "WWOZ_STREAM_URL",
            "WWOZ_NO_REPORTS", "WWOZ_REPORT_URL")
_WHEN = re.compile(r"(\w+ \d{1,2}, \d{4}) - (\d{1,2}:\d{2}[ap]m) to "
                   r"(?:(?:\w+day, )?(\w+ \d{1,2}, \d{4}) - )?(\d{1,2}:\d{2}[ap]m)")


def parse_live_calendar(page):
    """The broadcasts on a page of WWOZ's Live Event Broadcasts calendar that list a start
    and an end ("Thursday, October 22, 2026 - 11:00am to 7:00pm"): [{'title', 'start',
    'end', 'place', 'url'}]. A studio session or a talk lists a start only."""
    events = []
    for blk in re.split(r'<div id="node-\d+"\s+class="node node-event', page)[1:]:
        title = re.search(r'<h2>\s*<a href="(/events/\d+)">([^<]+)</a>', blk)
        when = re.search(r'field-name-field-event-date.*?(<span\s+class="date-display-single">.*?)</div>', blk, re.S)
        if not (title and when):
            continue
        m = _WHEN.search(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", when.group(1)))))
        if not m:
            continue
        day0, t0, day1, t1 = m.groups()
        try:
            start = datetime.strptime(f"{day0} {t0}", "%B %d, %Y %I:%M%p").replace(tzinfo=TZ)
            end = datetime.strptime(f"{day1 or day0} {t1}", "%B %d, %Y %I:%M%p").replace(tzinfo=TZ)
        except ValueError:
            continue
        if end <= start:                       # on past midnight
            end += timedelta(days=1)
        place = re.search(r'node-organization.*?<h2>\s*<a href="[^"]*">([^<]+)</a>', blk, re.S)
        events.append({"title": html.unescape(title.group(2)).strip(), "start": start, "end": end,
                       "place": html.unescape(place.group(1)).strip() if place else "",
                       "url": WWOZ + title.group(1)})
    return events


def is_festival_broadcast(ev):
    """A festival day on the calendar (Jazz Fest, Festing in Place, the Crescent City Blues
    & BBQ Fest), as opposed to a video stream, a studio session or a talk."""
    t = ev["title"].lower()
    return "fest" in t and not t.startswith("video")


def fetch_live_calendar():
    """Every broadcast with hours on WWOZ's Live Event Broadcasts calendar, all pages."""
    events, url, seen = [], LIVE_CALENDAR_URL, set()
    while url and url not in seen and len(seen) < 8:
        seen.add(url)
        r = HTTP.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        if "Live Event Broadcasts" not in r.text:
            raise ToolError("WWOZ's live broadcast calendar didn't look the usual way (wwoz.org may "
                            "have changed it), so festival recordings couldn't be planned")
        events += parse_live_calendar(r.text)
        url = next((urljoin(url, html.unescape(u)) for u in re.findall(r'href="([^"]*live-broadcast\?page=\d+)"', r.text)
                    if urljoin(url, html.unescape(u)) not in seen), None)
    return events


def _event_json(e):
    return dict(e, start=e["start"].isoformat(), end=e["end"].isoformat())


def saved_festival_calendar():
    """The festival broadcasts still to come or on the air, from the copy last saved
    (live_broadcasts.json in the data folder); never touches the network."""
    try:
        saved = json.loads(LIVE_CALENDAR_PATH.read_text(encoding="utf-8"))
        events = [dict(e, start=datetime.fromisoformat(e["start"]), end=datetime.fromisoformat(e["end"]))
                  for e in saved.get("events", [])]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    now = datetime.now(TZ)
    return sorted((e for e in events if is_festival_broadcast(e) and e["end"] + RECORD_LATE > now),
                  key=lambda e: e["start"])


def festival_calendar(refresh=False, max_age=timedelta(hours=6)):
    """The festival broadcasts still to come or on the air (saved_festival_calendar),
    read again from wwoz.org first when the saved copy is older than max_age. A broadcast
    already under way is kept even if the calendar no longer lists it (it may list only
    what hasn't begun), so a computer switched on mid-festival still records the rest.
    Offline, the saved copy serves."""
    try:
        saved = json.loads(LIVE_CALENDAR_PATH.read_text(encoding="utf-8"))
        age = datetime.now(TZ) - datetime.fromisoformat(saved["fetched"])
    except (OSError, ValueError, KeyError, TypeError):
        saved, age = {}, None
    if refresh or age is None or age >= max_age:
        try:
            events = fetch_live_calendar()
            now = datetime.now(TZ)
            listed = {e["url"] for e in events}
            old = {e.get("url"): e for e in saved.get("events", [])}
            begun = [e for e in old.values() if e.get("url") not in listed
                     and datetime.fromisoformat(e["start"]) <= now < datetime.fromisoformat(e["end"]) + RECORD_LATE]
            events = [dict(e, archived=True) if old.get(e["url"], {}).get("archived") else e for e in events]
            saved = dict(saved, fetched=now.isoformat(), source=LIVE_CALENDAR_URL,
                         events=[_event_json(e) for e in events] + begun)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            write_text_atomic(LIVE_CALENDAR_PATH, json.dumps(saved, indent=1, ensure_ascii=False))
        except Exception as e:
            out(f"  couldn't read WWOZ's live broadcast calendar ({e}); the saved copy is used")
    return saved_festival_calendar()


def airing_now(events, now=None):
    """The festival broadcast to be recording now (from ten minutes before its listed start
    until half an hour after its listed end), or None."""
    now = now or datetime.now(TZ)
    return next((e for e in events if e["start"] - RECORD_EARLY <= now < e["end"] + RECORD_LATE
                 and not e.get("archived")), None)


def _note_archived(ev):
    """Remember that the archive keeps this broadcast, so nothing starts recording it again."""
    try:
        saved = json.loads(LIVE_CALENDAR_PATH.read_text(encoding="utf-8"))
        for e in saved.get("events", []):
            if e.get("url") == ev.get("url"):
                e["archived"] = True
        write_text_atomic(LIVE_CALENDAR_PATH, json.dumps(saved, indent=1, ensure_ascii=False))
    except (OSError, ValueError, AttributeError):
        pass


def recording_under_way():
    """Is a festival recording running now (a scheduled one, or one the app started)?"""
    try:
        pid = int(RECORDING_LOCK.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return False
    return bool(pid) and _process_alive(pid)


def festival_archived(ev):
    """Has WWOZ put this festival in its two-week archive: a slot of the day, from two
    hours before the listed start, titled as a festival? Then the archive downloads get
    it, and recording it is not needed. (Never, when those downloads are turned off.)"""
    if not auto_festivals_on(load_config()):
        return False
    try:
        shows = fetch_archive_shows()
    except Exception:
        return False
    return any(s["start"] and ev["start"] - timedelta(hours=2) <= s["start"] < ev["end"]
               and festival_named(s["title"]) for s in shows)


def record_festival(ev, stop_flag=None):
    """Record a festival broadcast from now until half an hour after its listed end, into
    the recordings folder, unless a festival recording is under way already. Stops, and
    removes what it recorded, if the archive turns out to keep this festival. Returns
    the recording, or None."""
    try:
        fd = os.open(RECORDING_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if recording_under_way():
            out("  a festival recording is under way already")
            return None
        RECORDING_LOCK.unlink(missing_ok=True)           # left by one that was cut off
        fd = os.open(RECORDING_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    _HELD_LOCKS.add(RECORDING_LOCK)
    try:
        if festival_archived(ev):
            _note_archived(ev)
            out(f"WWOZ keeps {ev['title']} in its two-week archive, so it is downloaded from there "
                "instead of recorded.")
            return None
        start = datetime.now(TZ)
        until = ev["end"] + RECORD_LATE
        dest = RECORDINGS_DIR / live_file_name(start, ev["title"])
        info = {"title": ev["title"], "festival": ev["title"], "place": ev.get("place", ""),
                "listed": f"{ev['start']:%Y-%m-%d %H:%M} to {ev['end']:%H:%M}", "recorded_live": True}
        show_info_path(dest).write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
        out(f"Recording WWOZ's live broadcast of {ev['title']} until {until:%I:%M %p} Central: {dest.name}")
        stop, result = threading.Event(), {}

        def run():
            try:
                result["path"] = record_stream((until - start).total_seconds() / 3600, RECORDINGS_DIR,
                                               stop_flag=stop, dest=dest)
            except Exception as e:
                result["error"] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        look = time.time() + ARCHIVE_LOOK
        while t.is_alive():
            t.join(timeout=5)
            if stop_flag and stop_flag.is_set():
                stop.set()
            elif t.is_alive() and time.time() >= look:
                look = time.time() + ARCHIVE_LOOK
                if festival_archived(ev):
                    _note_archived(ev)
                    stop.set()
                    t.join(timeout=60)
                    dest.unlink(missing_ok=True)
                    show_info_path(dest).unlink(missing_ok=True)
                    out(f"WWOZ is keeping {ev['title']} in its two-week archive, so the recording stopped "
                        "and was removed; the festival is downloaded from the archive instead.")
                    return None
        if "error" in result:
            raise result["error"]
        return dest
    finally:
        RECORDING_LOCK.unlink(missing_ok=True)
        _HELD_LOCKS.discard(RECORDING_LOCK)


def schedule_festival_recordings(events):
    """Have the computer start `wwoz.py festival-record` ten minutes before each festival
    broadcast still to come, whether WWOZ Studio is open or not: a Windows task that may
    wake the computer from sleep, or a Mac launch job (which can't wake a sleeping Mac,
    and runs when it wakes). Done again only when the times change; with none left it is
    removed. Its definition is also saved in the data folder. WWOZ_NO_SCHEDULE (tests)
    saves the definition only. Returns the start times."""
    now = datetime.now(TZ)
    # (a start already passed stays until that broadcast is over: the times, and so the
    # schedule, don't change while it is being recorded)
    times = sorted({e["start"] - RECORD_EARLY for e in events if e["end"] + RECORD_LATE > now})
    env = {k: os.environ[k] for k in _CARRIED if os.environ.get(k)}
    want = {"times": [t.isoformat() for t in times], "program": str(SCRIPT_DIR), "env": env,
            "name": FESTIVAL_TASK if os.name == "nt" else FESTIVAL_AGENT}
    try:
        saved = json.loads(LIVE_CALENDAR_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if saved.get("scheduled") == want:
        return times
    if os.name == "nt":
        _schedule_windows(times, env)
    elif sys.platform == "darwin":
        _schedule_mac(times, env)
    saved["scheduled"] = want
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_text_atomic(LIVE_CALENDAR_PATH, json.dumps(saved, indent=1, ensure_ascii=False))
    return times


def _xml(s):
    return html.escape(str(s), quote=True)


def _schedule_windows(times, env):
    copy = DATA_DIR / "festival-recording-task.xml"
    if not times:
        copy.unlink(missing_ok=True)
        if not os.environ.get("WWOZ_NO_SCHEDULE"):
            subprocess.run(["schtasks", "/Delete", "/TN", FESTIVAL_TASK, "/F"], capture_output=True,
                           creationflags=NOWIN)
        return
    pyw = Path(sys.executable).with_name("pythonw.exe")       # no console window
    cmd = str(pyw if pyw.exists() else sys.executable)
    args = f'"{SCRIPT_DIR / "wwoz.py"}" --log "{RECORDING_LOG}" festival-record --launch'
    if env:                                       # testing: run with the test's folders
        sets = "".join(f'set "{k}={v}"&& ' for k, v in env.items())
        cmd, args = os.environ.get("COMSPEC", "cmd.exe"), f'/c {sets}start "" "{cmd}" {args}'
    triggers = "".join(f"<TimeTrigger><StartBoundary>{t.isoformat()}</StartBoundary><Enabled>true</Enabled>"
                       f"</TimeTrigger>" for t in times)
    copy.write_text(f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>WWOZ Studio: records the festivals WWOZ broadcasts live that its archive doesn't keep, at the times on wwoz.org's Live Event Broadcasts calendar. Turn it off on WWOZ Studio's Weekly Favorites page.</Description>
  </RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals>
    <Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml(cmd)}</Command>
      <Arguments>{_xml(args)}</Arguments>
      <WorkingDirectory>{_xml(SCRIPT_DIR)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""", encoding="utf-16")
    if os.environ.get("WWOZ_NO_SCHEDULE"):
        return
    r = subprocess.run(["schtasks", "/Create", "/TN", FESTIVAL_TASK, "/XML", str(copy), "/F"],
                       capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    if r.returncode:
        raise ToolError(f"the festival recordings couldn't be scheduled in Windows "
                        f"({(r.stderr or r.stdout).strip()})")


def _schedule_mac(times, env):
    copy = DATA_DIR / "festival-recording.plist"
    agent = Path.home() / "Library" / "LaunchAgents" / f"{FESTIVAL_AGENT}.plist"
    live = not os.environ.get("WWOZ_NO_SCHEDULE")
    if live:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{FESTIVAL_AGENT}"], capture_output=True)
    if not times:
        copy.unlink(missing_ok=True)
        if live:
            agent.unlink(missing_ok=True)
        return
    when = "".join(f"<dict><key>Month</key><integer>{t.month}</integer><key>Day</key><integer>{t.day}</integer>"
                   f"<key>Hour</key><integer>{t.hour}</integer><key>Minute</key><integer>{t.minute}</integer>"
                   f"</dict>" for t in (t.astimezone() for t in times))         # the Mac's own clock
    env = dict(env, PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{_xml(FESTIVAL_AGENT)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_xml(sys.executable)}</string>
        <string>{_xml(SCRIPT_DIR / "wwoz.py")}</string>
        <string>--log</string>
        <string>{_xml(RECORDING_LOG)}</string>
        <string>festival-record</string>
        <string>--launch</string>
    </array>
    <key>WorkingDirectory</key><string>{_xml(SCRIPT_DIR)}</string>
    <key>EnvironmentVariables</key>
    <dict>{"".join(f"<key>{_xml(k)}</key><string>{_xml(v)}</string>" for k, v in env.items())}</dict>
    <key>StartCalendarInterval</key>
    <array>{when}</array>
</dict>
</plist>
"""
    copy.write_text(text, encoding="utf-8")
    if live:
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(text, encoding="utf-8")
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent)], capture_output=True, text=True)
        if r.returncode:
            r = subprocess.run(["launchctl", "load", "-w", str(agent)], capture_output=True, text=True)
        if r.returncode:
            raise ToolError(f"the festival recordings couldn't be scheduled ({(r.stderr or r.stdout).strip()})")


def start_recorder():
    """Start `wwoz.py festival-record` as a program of its own, so the recording goes on
    when whatever started it ends: the app being closed, or the computer's scheduled task
    or launch job, which only launches it (a task that is changed or removed while it
    runs is stopped, and a recording must not stop with it)."""
    exe = Path(sys.executable)
    if os.name == "nt":
        pyw = exe.with_name("pythonw.exe")
        exe, kw = (pyw if pyw.exists() else exe), {"creationflags": 0x00000008 | 0x00000200}
    else:                                       # (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        kw = {"start_new_session": True}
    subprocess.Popen([str(exe), str(SCRIPT_DIR / "wwoz.py"), "--log", str(RECORDING_LOG), "festival-record"],
                     cwd=str(SCRIPT_DIR), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kw)


def cmd_festival_record(args):
    """Started at a festival broadcast's time (by the computer's schedule or the app)."""
    if args.launch:
        start_recorder()
        out("started the festival recorder")
        return
    cfg = load_config()
    if not auto_festivals_on(cfg):
        out("Festivals are turned off on the Weekly Favorites page, so nothing is recorded.")
        schedule_festival_recordings([])
        return
    events = festival_calendar()           # (the schedule is left alone here: see start_recorder)
    ev = airing_now(events)
    if not ev:
        out("No festival is on WWOZ right now.")
        return
    record_festival(ev)


def cmd_festivals(args):
    """What WWOZ's live broadcast calendar lists, and when this computer will record."""
    events = festival_calendar(refresh=True)
    if not events:
        out(f"No festival broadcasts on WWOZ's live broadcast calendar now ({LIVE_CALENDAR_URL}).")
    for e in events:
        out(f"  {e['start']:%a %b %d, %I:%M %p} to {e['end']:%I:%M %p}  {e['title']}"
            + (f"  ({e['place']})" if e.get("place") else ""))
    if auto_festivals_on(load_config()):
        times = schedule_festival_recordings(events)
        if times:
            out(f"This computer records them as they air ({len(times)} start(s) scheduled), unless the "
                "archive turns out to keep them.")
    else:
        schedule_festival_recordings([])
        out("Festivals are turned off on the Weekly Favorites page, so none of these is recorded.")


# ---------------------------------------------------------------- record ---

STREAM_URL = os.environ.get("WWOZ_STREAM_URL") or "https://wwoz-sc.streamguys1.com/wwoz-hi.mp3"  # (tests)


def live_file_name(start, label=""):
    """A live recording's file name, starting with when it began (the song list and the
    markers map onto it by that), then what it is, if anything."""
    return f"wwoz-live-{start:%Y-%m-%d-%Hh%Mm%Ss}" + (f" {sanitize(label)}" if label else "") + ".mp3"


def record_stream(hours, dest_dir: Path, stop_flag=None, dest=None):
    """Record the live WWOZ stream for `hours`. Filename carries the start time
    so tracklist/markers map onto it automatically. Reconnects on dropouts."""
    dest = Path(dest) if dest else Path(dest_dir) / live_file_name(datetime.now(TZ))
    end_at = time.time() + hours * 3600
    out(f"  recording live stream -> {dest.name} for {hours}h "
        f"(safe to keep using the PC; stop by closing/Ctrl+C)")
    awake = _stay_awake()      # the file name's clock assumes the sound never stops
    try:
        with open(dest, "wb") as f:
            while time.time() < end_at and not (stop_flag and stop_flag.is_set()):
                try:
                    with HTTP.get(STREAM_URL, headers=UA, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        for chunk in r.iter_content(chunk_size=1 << 15):
                            try:
                                f.write(chunk)
                            except OSError as e:
                                raise ToolError(f"the recording stopped: {dest.name} couldn't be written "
                                                f"({e.strerror or e}). Is the disk full?")
                            if time.time() >= end_at or (stop_flag and stop_flag.is_set()):
                                break
                except ToolError:
                    raise
                except Exception as e:
                    out(f"  stream hiccup ({e}); reconnecting in 5s…")
                    time.sleep(5)
    finally:
        awake()
    out(f"  done: {dest.name} ({dest.stat().st_size >> 20} MB)")
    return dest


def _stay_awake():
    """Keep the computer from going to sleep (the screen may still turn off) until the
    returned function is called. Call both from the same thread."""
    if os.name == "nt":
        import ctypes
        es = ctypes.windll.kernel32.SetThreadExecutionState
        es(0x80000000 | 0x00000001)              # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        return lambda: es(0x80000000)
    if sys.platform == "darwin":
        try:
            p = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            return lambda: (p.kill(), p.wait())
        except OSError:
            pass
    return lambda: None


def cmd_record(args):
    dest_dir = Path(args.out) if args.out else RECORDINGS_DIR
    record_stream(args.hours, dest_dir)


# ---------------------------------------------------------------- weekly ---

def loose(s):
    """Lower-case ASCII letters and digits only: 'Hep Cat's Ball' == 'Hep Cats Ball'."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def favorite_matches(fav, show):
    """Does an archive show satisfy a favorites.json entry? By weekday + start time when
    the entry carries them (the archive titles a third of the shows differently from the
    schedule), and by title substring ignoring case, accents and punctuation."""
    if fav.get("day") and fav.get("start") and show.get("start"):
        st = show["start"]
        if (WEEKDAYS[st.weekday()] == fav["day"]
                and abs(st.hour * 60 + st.minute - _minutes(fav["start"])) <= 5):
            return True
    return bool(loose(fav.get("match"))) and loose(fav["match"]) in loose(show["title"])


def cmd_weekly(args):
    cfg = load_config()
    dest_dir = download_dir_from_config(cfg)
    favs = [f for f in cfg.get("favorites", []) if f.get("enabled")]
    if not favs and not auto_festivals_on(cfg):
        out("No favorites enabled yet. Check some shows in WWOZ Studio's Weekly Favorites tab")
        out("(or set \"enabled\": true in favorites.json), then re-run.")
        return
    if auto_festivals_on(cfg):                  # the festivals that air but aren't archived
        try:
            coming = festival_calendar(refresh=True)
            schedule_festival_recordings(coming)
            out("Festival broadcasts on WWOZ's calendar, recorded as they air unless the archive keeps them: "
                + ("; ".join(f"{e['title']} {e['start']:%b %d}" for e in coming) if coming else "none now"))
        except Exception as e:
            out(f"    couldn't schedule the festival recordings: {e}")
            report_error("festival schedule")
    shows = fetch_archive_shows()
    remember_titles(shows, dest_dir)
    new = [s for s in shows
           if any(favorite_matches(f, s) for f in favs)
           and not (dest_dir / s["filename"]).exists()]
    if favs:
        wanted = [f.get("match") or f"{f['day']} {f['start']}" for f in favs]
        out(f"{len(new)} new episode(s) matching favorites ({', '.join(wanted)}) -> {dest_dir}:")
    else:
        out("No favorite shows checked yet (WWOZ Studio's Weekly Favorites page).")
    fests = {}

    def get(s, festival=False):
        try:
            f = festival_for(download_festival_show(s, dest_dir) if festival else download_show(s, dest_dir))
            if f:
                fests[f["name"]] = f
        except AlreadyDownloading as e:   # the app is getting it at this moment
            out(f"    skipped: {e}")
        except Exception as e:
            out(f"    FAILED: {e}")
            report_error("Sunday download")
    for s in new:
        get(s)
    if auto_festivals_on(cfg):
        taken = {s["filename"] for s in new}
        live = [s for s in festival_picks(shows, cfg, dest_dir) if s["filename"] not in taken]
        first = auto_festivals_from(cfg)
        names = sorted({s["_festival"] for s in live})
        out(f"{len(live)} new festival broadcast(s) from {first:%B} {first.day} on"
            + (f" ({', '.join(names)})" if names else "") + (":" if live else "."))
        for s in live:
            get(s, festival=True)
    else:
        out("Festival broadcasts aren't downloaded on their own (turned off on the Weekly Favorites page).")
    for f in fests.values():                    # a festival broadcast among them: recut its sets
        try:
            cut_festival(f, dest_dir)
        except Exception as e:
            out(f"    cutting {f['name']} into sets failed: {e}")
            report_error("festival cut")
    retry = sorted(dest_dir.glob("*.tracklist.failed"))
    if retry:
        out(f"Retrying the DJ song list for {len(retry)} earlier download(s):")
        for m in retry:
            p = Path(str(m)[:-len(".tracklist.failed")] + ".mp3")
            if p.exists():
                fetch_tracklist_or_mark(p)       # keeps any Shazam finds already merged
            else:
                m.unlink(missing_ok=True)
    todo = [] if args.no_shazam else pending_downloads(dest_dir)   # the new ones, and any missed before
    if todo:
        out(f"Shazam-scanning {len(todo)} download(s) for songs the DJ didn't log:")
        for p in todo:
            if not wait_for_turn(dest_dir):
                out("Another scanner has been busy for hours; the rest will be scanned next time.")
                break
            try:
                shazam_fill(p)
            except ShazamQuiet:
                out("Shazam stopped answering; the rest will be scanned next time.")
                break
            except Exception as e:
                out(f"    scan failed for {p.name}: {e}")
    out("Done.")


# ------------------------------------------------------------------ main ---

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="wwoz.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", help="append all output to this file (for running windowless "
                                  "with pythonw, e.g. from Task Scheduler)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list shows in the 2-week archive").set_defaults(fn=cmd_list)

    sc = sub.add_parser("schedule", help="weekly program schedule from wwoz.org, with descriptions")
    sc.add_argument("--refresh", action="store_true", help="re-fetch even if the cached copy is fresh")
    sc.add_argument("-v", "--verbose", action="store_true", help="print a description under each show")
    sc.set_defaults(fn=cmd_schedule)

    d = sub.add_parser("download", help="download archive shows")
    d.add_argument("--date", help="YYYY-MM-DD")
    d.add_argument("--time", help="time label as shown by list, e.g. 6AM, 11AM, 10PM")
    d.add_argument("--show", help="case-insensitive substring of the show title")
    d.add_argument("--all", action="store_true", help="download every match")
    d.add_argument("--out", help="output folder (default: from favorites.json)")
    d.add_argument("--no-tracklist", action="store_true")
    d.add_argument("--no-shazam", action="store_true", help="skip the fingerprint scan after downloading")
    d.set_defaults(fn=cmd_download)

    sz = sub.add_parser("shazam", help="fingerprint downloads and fold the finds into their tracklists")
    sz.add_argument("files", nargs="*", help="recordings to scan (default: --all)")
    sz.add_argument("--all", action="store_true", help="every archive download not yet scanned")
    sz.add_argument("--interval", type=int, default=90, help="seconds between samples (default 90)")
    sz.set_defaults(fn=cmd_shazam)

    t = sub.add_parser("tracklist", help="tracklist from WWOZ playlists for a recording")
    t.add_argument("file")
    t.add_argument("--start", help='recording start "YYYY-MM-DD HH:MM" (24h, Central) '
                                   'if not parseable from the filename')
    t.add_argument("--duration", type=float, help="seconds (default: probe the file)")
    t.add_argument("--from-mtime", action="store_true",
                   help="derive start time as file-modified-time minus duration")
    t.set_defaults(fn=cmd_tracklist)

    i = sub.add_parser("identify", help="fingerprint songs via Shazam")
    i.add_argument("file")
    i.add_argument("--interval", type=int, default=60, help="seconds between samples (default 60)")
    i.add_argument("--clip", type=int, default=12, help="sample length seconds (default 12)")
    i.add_argument("--start-offset", type=int, default=0, help="start scanning at this offset (s)")
    i.add_argument("--duration", type=int, help="only scan this many seconds")
    i.set_defaults(fn=cmd_identify)

    s = sub.add_parser("split", help="cut a recording into its sets and shows (--songs: one file per song)")
    s.add_argument("file")
    s.add_argument("--songs", action="store_true", help="the old way: cut at every song as well")
    s.add_argument("--tracklist", help="csv to use (default: FILE.tracklist.csv)")
    s.add_argument("--out", help="output folder")
    s.add_argument("--lead-in", type=int, default=15,
                   help="start each track this many seconds before its logged time (default 15)")
    s.set_defaults(fn=cmd_split)

    wk = sub.add_parser("weekly", help="download new episodes of favorites, and new festival broadcasts")
    wk.add_argument("--no-shazam", action="store_true", help="skip the fingerprint scan after downloading")
    wk.set_defaults(fn=cmd_weekly)

    ac = sub.add_parser("autocut", help="auto-detect performance boundaries (VAD + Whisper)")
    ac.add_argument("file")
    ac.add_argument("--start-offset", type=int, help="scan from this offset (s)")
    ac.add_argument("--end-offset", type=int, help="scan to this offset (s)")
    ac.add_argument("--no-transcribe", action="store_true",
                    help="skip Whisper labeling, just find boundaries")
    ac.add_argument("--no-shazam", action="store_true",
                    help="don't fingerprint the short gaps between sets for records")
    ac.set_defaults(fn=cmd_autocut)

    pg = sub.add_parser("programs", help="mark WWOZ program changes outside fest hours")
    pg.add_argument("file")
    pg.set_defaults(fn=cmd_programs)

    sub.add_parser("festivals", help="the festival broadcasts on WWOZ's live broadcast calendar, and when "
                                     "this computer records them").set_defaults(fn=cmd_festivals)
    fr = sub.add_parser("festival-record", help="record the festival on WWOZ now, if one is (the computer's "
                                                "schedule runs this)")
    fr.add_argument("--launch", action="store_true", help="start the recording as a program of its own and "
                                                          "return at once (what the schedule does)")
    fr.set_defaults(fn=cmd_festival_record)

    fe = sub.add_parser("festival", help="mark and cut a broadcast festival (fest-*.json) into one file per set")
    fe.add_argument("--name", help="part of the festival's name (default: every festival downloaded)")
    fe.add_argument("--markers-only", action="store_true", help="mark the sets in the Player, don't cut files")
    fe.set_defaults(fn=cmd_festival)

    r = sub.add_parser("record", help="record the live WWOZ stream")
    r.add_argument("--hours", type=float, default=8.0, help="how long to record (default 8)")
    r.add_argument("--out", help="output folder (default: recordings folder)")
    r.set_defaults(fn=cmd_record)

    args = ap.parse_args()
    if args.log:
        log = open(args.log, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        out(f"\n===== {datetime.now():%Y-%m-%d %I:%M %p}  wwoz.py {args.cmd}")
    try:
        args.fn(args)
    except ToolError as e:
        out(f"ERROR: {e}")
        report_error(f"wwoz.py {args.cmd}", str(e))
        sys.exit(1)
    except Exception:
        report_error(f"wwoz.py {args.cmd}")
        if not args.log:
            raise
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
