"""Error reports: when WWOZ Studio, its Sunday download or Setup runs into an error, a
short report goes to the person who made it, so it can be fixed without anyone having
to describe what went wrong.

A report holds what went wrong (the message and where in the program), which part was
running, the program's version, and the computer's system (Windows or macOS version,
Python version). Your user name is taken out of every path, and nothing else about you,
your recordings or your favorite shows is sent. Each computer sends a given error at
most once a day.

Turn reports off with the "Send error reports" box under Activity, or with
"send_error_reports": false in favorites.json. WWOZ_NO_REPORTS=1 turns them off for one
run (the tests use it), and WWOZ_REPORT_URL sends them somewhere else.

Only Python's own modules are used here, so a report still goes out when a package the
app needs is missing, which is exactly when one is wanted.
"""

import getpass
import hashlib
import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

VERSION = "2026-10-17"
ENDPOINT = "https://wwoz-studio-reports.ericwp1989.workers.dev/report"
HERE = Path(__file__).resolve().parent
ONCE_A_DAY = 24 * 3600


def _data_dir():
    """Where the settings live (wwoz.DATA_DIR when wwoz imports; this works without it)."""
    if os.environ.get("WWOZ_DATA"):
        return Path(os.environ["WWOZ_DATA"])
    try:
        import wwoz
        return wwoz.DATA_DIR
    except Exception:
        return Path.home() / "Documents" / "WWOZ Studio"


def enabled(data_dir=None):
    if os.environ.get("WWOZ_NO_REPORTS"):
        return False
    try:
        cfg = json.loads((Path(data_dir or _data_dir()) / "favorites.json").read_text(encoding="utf-8-sig"))
        return cfg.get("send_error_reports", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def set_enabled(on, data_dir=None):
    """Save the choice in favorites.json (the app's "Send error reports" box)."""
    import wwoz
    cfg = wwoz.load_config()
    cfg["send_error_reports"] = bool(on)
    wwoz.save_config(cfg)


def code_version():
    """The release date and a short fingerprint of the program's own files, so a report
    says exactly which copy of the program sent it."""
    h = hashlib.sha1()
    for name in ("wwoz.py", "wwoz_gui.py", "wwoz_player.py", "wwoz_report.py"):
        try:
            h.update((HERE / name).read_bytes())
        except OSError:
            pass
    return f"{VERSION} ({h.hexdigest()[:8]})"


def scrub(text):
    """Take the user's name out: their home folder becomes ~ and the name itself <user>."""
    text = str(text or "")
    home = str(Path.home())
    for h in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        text = re.sub(re.escape(h), "~", text, flags=re.I)
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if user and len(user) > 2:
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(user) + r"(?![A-Za-z0-9])", "<user>", text, flags=re.I)
    return text


def fingerprint(kind, message, tb=""):
    """The same error seen again gets the same fingerprint: the kind, the exception's
    type and the places in the code it passed through (not line numbers, which move
    between versions); with no traceback, the message with numbers and paths blurred."""
    places = re.findall(r'File "([^"]+)", line \d+, in (\S+)', tb or "")
    if places:
        exc = (tb.strip().splitlines() or [""])[-1].split(":")[0]
        key = kind + "|" + exc + "|" + ";".join(f"{Path(f).name}:{fn}" for f, fn in places[-6:])
    else:
        msg = re.sub(r"[A-Za-z]:\\[^\s'\")]+|/[^\s'\")]+", "<path>", message or "")
        key = kind + "|" + re.sub(r"\d+", "#", msg)[:120]
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]


def _install_id(data_dir):
    """A random id for this computer's copy, so reports from one person can be told
    apart from many people hitting the same thing. It says nothing about who they are."""
    p = Path(data_dir) / "report_id.txt"
    try:
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    except OSError:
        pass
    v = uuid.uuid4().hex[:12]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v, encoding="utf-8")
    except OSError:
        pass
    return v


def _seen_recently(data_dir, fp):
    """True if this computer sent this error in the last day; otherwise note it now."""
    p = Path(data_dir) / "reports_sent.json"
    now = time.time()
    try:
        sent = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sent = {}
    if now - sent.get(fp, 0) < ONCE_A_DAY:
        return True
    sent = {k: v for k, v in sent.items() if now - v < ONCE_A_DAY}
    sent[fp] = now
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sent), encoding="utf-8")
    except OSError:
        pass
    return False


def build(kind, message, tb="", data_dir=None):
    data_dir = Path(data_dir or _data_dir())
    message, tb = scrub(message)[:500], scrub(tb)[-12000:]
    return {
        "app": "WWOZ Studio",
        "kind": kind,
        "message": message,
        "traceback": tb,
        "version": code_version(),
        "system": scrub(f"{platform.system()} {platform.release()} ({platform.version()}), "
                        f"{platform.machine()}"),
        "python": platform.python_version(),
        "install": _install_id(data_dir),
        "fingerprint": fingerprint(kind, message, tb),
    }


def _post(report, timeout):
    url = os.environ.get("WWOZ_REPORT_URL") or ENDPOINT
    req = urllib.request.Request(url, data=json.dumps(report).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"WWOZ Studio {report['version']}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False                      # never let a report become a second problem


def send(kind, message, tb="", data_dir=None, wait=False, timeout=10):
    """Send one report (in the background unless wait=True). Returns the report sent,
    or None when reports are off or this computer sent the same one today."""
    try:
        data_dir = Path(data_dir or _data_dir())
        if not enabled(data_dir):
            return None
        report = build(kind, message, tb, data_dir)
        if _seen_recently(data_dir, report["fingerprint"]):
            return None
        if wait:
            _post(report, timeout)
        else:
            threading.Thread(target=_post, args=(report, timeout), daemon=True).start()
        return report
    except Exception:
        return None


def send_exception(kind, exc_info=None, data_dir=None, wait=False):
    """send() for the exception being handled (or the one in exc_info)."""
    exc_info = exc_info or sys.exc_info()
    if not exc_info or exc_info[0] is None:
        return None
    tb = "".join(traceback.format_exception(*exc_info))
    return send(kind, f"{exc_info[0].__name__}: {exc_info[1]}", tb, data_dir, wait)


if __name__ == "__main__":
    # py wwoz_report.py "kind" "message": the setups use this when Python is there
    print(json.dumps(send(sys.argv[1] if len(sys.argv) > 1 else "test",
                          sys.argv[2] if len(sys.argv) > 2 else "a test report", wait=True), indent=1))
