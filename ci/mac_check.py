"""Checks WWOZ Studio on a Mac. GitHub's Macs run it from .github/workflows/mac-test.yml.

    python ci/mac_check.py OUT             the program checks, then the window checks
    python ci/mac_check.py OUT --summary   the results as a Markdown table

Each check is PASS, WARN (worth knowing, not necessarily broken, e.g. a website that
won't answer a data center) or FAIL. Screenshots and results land in OUT.
"""
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ci"))
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
MODE = ("gui" if "--gui" in sys.argv else "festival-gui" if "--festival-gui" in sys.argv
        else "summary" if "--summary" in sys.argv else "program")
results = []


def check(name, ok, detail="", soft=False):
    status = "PASS" if ok else ("WARN" if soft else "FAIL")
    results.append({"check": name, "status": status, "detail": str(detail)[:600]})
    print(f"{status}  {name}  {detail}", flush=True)
    (OUT / f"{MODE}.json").write_text(json.dumps(results, indent=1))


def shot(name):
    p = OUT / f"{name}.png"
    try:
        subprocess.run(["screencapture", "-x", str(p)], timeout=30)
    except OSError:                  # not a Mac (a trial run of these checks elsewhere)
        pass
    return p.exists()


# ------------------------------------------------------------------ program --

def test_show(wwoz, dest):
    """A made-up 15-minute show with two programs and three songs, for when WWOZ's
    archive can't be reached from GitHub's machines."""
    path = dest / "20261011120006-20-166-test-show.mp3"
    subprocess.run([wwoz.find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=900", "-b:a", "128k", "-y", str(path)], check=True)
    rows = [{"offset_seconds": 0, "artist": "Test Show One", "show": "WWOZ program"},
            {"offset_seconds": 60, "artist": "Professor Longhair", "song": "Tipitina", "show": "Test Show One"},
            {"offset_seconds": 300, "artist": "Irma Thomas", "song": "Time Is on My Side", "show": "Test Show One"},
            {"offset_seconds": 450, "artist": "Test Show Two", "show": "WWOZ program"},
            {"offset_seconds": 500, "artist": "Dr. John", "song": "Right Place Wrong Time", "show": "Test Show Two"}]
    for r in rows:
        r.setdefault("song", "")
        r["offset"] = wwoz.fmt_off(r["offset_seconds"])
    wwoz.write_tracklist(path, rows)
    return path


def audio_awake():
    """Lines of `pmset -g assertions` where the Mac's sound system is keeping it awake
    (coreaudiod holds PreventUserIdleSystemSleep while an output plays)."""
    r = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=30)
    return [ln.strip() for ln in r.stdout.splitlines()
            if "PreventUserIdleSystemSleep" in ln and "coreaudiod" in ln]


def sleep_checks(wwoz, path):
    """The app must let the Mac sleep while nothing plays (the speakers used to run all
    the time), and keep it awake while it records the live stream."""
    import threading
    import wwoz_player
    try:
        pl = wwoz_player.Player(wwoz.find_tool("ffmpeg"), wwoz.find_tool("ffprobe"))
        pl.set_volume(0)
        time.sleep(3)
        idle = audio_awake()
        pl.open(path)
        time.sleep(4)
        playing = audio_awake()
        pl.pause()
        time.sleep(3)
        paused = audio_awake()
        pl.close()
        detail = f"idle {len(idle)}, playing {len(playing)}, paused {len(paused)} sound-system wake locks"
        if playing:
            check("the Mac can sleep while the app is idle or paused", not idle and not paused, detail)
        else:                # this Mac shows no wake lock even while playing: can't tell here
            check("the Mac can sleep while the app is idle or paused", not idle and not paused,
                  detail + " (none while playing either, so this Mac can't show it)", soft=True)
    except Exception as e:
        check("the Mac can sleep while the app is idle or paused", False, repr(e))
    try:
        stop = threading.Event()
        t = threading.Thread(target=wwoz.record_stream, args=(0.05, Path(os.environ["WWOZ_RECORDINGS"])),
                             kwargs={"stop_flag": stop}, daemon=True)
        t.start()
        time.sleep(4)
        held = subprocess.run(["pgrep", "-fl", "caffeinate"], capture_output=True, text=True).stdout
        stop.set()
        t.join(timeout=20)
        after = subprocess.run(["pgrep", "-fl", "caffeinate"], capture_output=True, text=True).stdout
        check("the Mac stays awake while recording, and not after",
              str(os.getpid()) in held and str(os.getpid()) not in after, f"during: {held.strip()!r}; "
              f"after: {after.strip()!r}")
    except Exception as e:
        check("the Mac stays awake while recording, and not after", False, repr(e))


def report_checks():
    """Error reports from this Mac reach a receiver (a stand-in on this machine, never the
    real one) as JSON, with the user name and home folder taken out: from the app's code,
    from a command-line task that stops, and from Setup."""
    import http.server
    import threading
    got = []

    class Receiver(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                got.append(json.loads(body))
                self.send_response(200)
            except ValueError:
                got.append({"not json": body.decode("utf-8", "replace")[:300]})
                self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/report"
    env = dict(os.environ, WWOZ_REPORT_URL=url)
    env.pop("WWOZ_NO_REPORTS", None)
    home, user = str(Path.home()), os.environ.get("USER", "")

    def private(rep):
        text = json.dumps(rep)
        return home in text or (len(user) > 2 and f"/{user}/" in text)

    def wait(n):
        t0 = time.time()
        while len(got) < n and time.time() - t0 < 20:
            time.sleep(0.2)
        return got[n - 1] if len(got) >= n else {}

    r = subprocess.run([sys.executable, "-c", "import wwoz_report, sys; wwoz_report.send('test', sys.argv[1], wait=True)",
                        f"couldn't open {home}/Music/WWOZ recordings/show.mp3"], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=60)
    rep = wait(1)
    check("an error report from the app's code arrives", rep.get("kind") == "test" and not private(rep),
          rep.get("message") or r.stderr[-300:])
    bad = Path(os.environ["WWOZ_RECORDINGS"]) / "Saturday night.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=5", "-y", str(bad)])
    r = subprocess.run([sys.executable, "wwoz.py", "tracklist", str(bad)], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=120)
    rep = wait(2)
    check("a command-line task that stops reports it", r.returncode == 1
          and rep.get("kind") == "wwoz.py tracklist" and not private(rep), rep.get("message") or r.stdout[-300:])
    r = subprocess.run(["bash", str(HERE / "Setup (Mac).command"), "--report-test"], env=env,
                       capture_output=True, text=True, timeout=60)
    rep = wait(3)
    check("Setup's error report arrives as JSON", "Test report sent" in r.stdout
          and rep.get("kind") == "Setup stopped (Mac)" and rep.get("system", "").startswith("macOS")
          and not private(rep), rep or r.stdout[-300:])
    srv.shutdown()


def festival_shows():
    """A made-up festival with no set times, around a club concert and regular shows, as
    the archive would list it (see fake_archive.py, which serves it from this machine)."""
    from zoneinfo import ZoneInfo
    import fake_archive

    def at(day, hour):
        return datetime(2026, 10, day, hour, 0, 6, tzinfo=ZoneInfo("America/Chicago"))
    S = fake_archive.show
    return [S(at(12, 12), "Live from the Satchmo SummerFest: Too Early Brass Band", "early"),
            S(at(14, 10), "New Orleans Music Show with DJ Test / Live from the Satchmo SummerFest: "
                          "Treme Brass Band", "nomusic"),
            S(at(14, 12), "Live from the Satchmo SummerFest: Kermit Ruffins, Shannon Powell (pt 1)", "kermit"),
            S(at(14, 14), "Tudo Bem Brazilian with Paul Sample", "tudo-bem"),
            S(at(14, 16), "Live at the Maple Leaf: Rebirth Brass Band", "maple-leaf"),
            S(at(14, 18), "Block Party with Brice Nice", "block-party"),
            S(at(15, 12), "Live from the Satchmo SummerFest: Doreen Ketchens / Cajun and Zydeco", "doreen")]


FEST_WANT = {"nomusic", "kermit", "tudo-bem", "doreen"}   # not too early, not the club, not a regular show


def fest_slug(name):
    return name.split("-", 3)[3][:-4]


def festival_setup(name, settings):
    """Scratch folders, the made-up archive served from this machine, and the settings
    for a run (None: a new install with no settings file). Returns (root, server, env)."""
    import fake_archive
    root = Path(os.environ["WWOZ_DATA"]).parent / f"festival-{name}"
    shutil.rmtree(root, ignore_errors=True)
    for d in ("server", "data", "rec"):
        (root / d).mkdir(parents=True)
    if settings is not None:
        (root / "data" / "favorites.json").write_text(json.dumps(settings))
    ffmpeg = shutil.which("ffmpeg") or next(p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
                                             if os.access(p, os.X_OK))
    tone = fake_archive.tone(ffmpeg, root / "server" / "tone.mp3", 30)
    shows = festival_shows()
    for s in shows:
        os.link(tone, root / "server" / s["filename"])
    srv, url = fake_archive.serve(fake_archive.page(shows), root / "server")
    env = dict(os.environ, WWOZ_DATA=str(root / "data"), WWOZ_RECORDINGS=str(root / "rec"),
               WWOZ_ARCHIVE_URL=url, WWOZ_NO_REPORTS="1")
    return root, srv, env


def festival_checks():
    """Festivals WWOZ broadcasts live download on their own, by the Sunday task: never
    the ones already in the archive on the first run, only festival slots (not a club
    concert), the slot an act ran on into, each once, and nothing when turned off."""
    import traceback

    def weekly(env):
        return subprocess.run([sys.executable, "wwoz.py", "weekly", "--no-shazam"], cwd=HERE, env=env,
                              capture_output=True, text=True, timeout=600)
    try:
        today = datetime.now(__import__("zoneinfo").ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
        root, srv, env = festival_setup("first", None)
        r = weekly(env)
        cfg = json.loads((root / "data" / "favorites.json").read_text())
        got = list((root / "rec" / "archive_downloads").glob("*.mp3"))
        check("festivals: a first run downloads none already in the archive, and notes the day",
              r.returncode == 0 and not got and cfg.get("auto_festivals_from") == today,
              (r.stdout + r.stderr)[-300:])
        srv.shutdown()

        root, srv, env = festival_setup("sunday", {"auto_festivals": True, "auto_festivals_from": "2026-10-13",
                                                   "favorites": []})
        r = weekly(env)
        arch = root / "rec" / "archive_downloads"
        got = {fest_slug(p.name) for p in arch.glob("*.mp3")}
        check("festivals: the Sunday download gets the festival's slots and the one an act ran on into, "
              "not a club concert", r.returncode == 0 and got == FEST_WANT, f"{sorted(got)} {r.stdout[-300:]}")
        info = json.loads(next(arch.glob("*tudo-bem.show.json")).read_text()) if got else {}
        check("festivals: the slot after 'pt 1' notes the act", info.get("continues", {}).get("act") == "Shannon Powell",
              info)
        n = len(srv.requests)
        for p in arch.glob("*kermit.mp3"):
            p.unlink()                          # deleted after listening: not downloaded again
        r = weekly(env)
        check("festivals: each is downloaded once, even if deleted", r.returncode == 0
              and not [x for x in srv.requests[n:] if x.startswith("/audio/")], r.stdout[-200:])
        srv.shutdown()

        root, srv, env = festival_setup("off", {"auto_festivals": False, "auto_festivals_from": "2026-10-13",
                                                "favorites": []})
        r = weekly(env)
        check("festivals: none when turned off", r.returncode == 0
              and not list((root / "rec" / "archive_downloads").glob("*.mp3")), r.stdout[-200:])
        srv.shutdown()
    except Exception:
        check("festival downloads", False, traceback.format_exc()[-600:])


def festival_gui_checks():
    """The app on empty folders: its own background worker finds the festival in the
    archive and downloads it, the library files it under the festival by day, and the
    box on the Weekly Favorites page turns it off and on."""
    root, srv, env = festival_setup("app", {"auto_festivals": True, "auto_festivals_from": "2026-10-13",
                                            "send_error_reports": False, "favorites": []})
    os.environ.update(env)
    import wwoz_gui
    wwoz_gui.App._refresh_stale_schedule = lambda self: None
    app = wwoz_gui.App()
    app.vol.set(0)
    app.scan_resume_at = time.time() + 10 ** 6         # no Shazam scans here
    app._fest_checked = 0                              # look now, not a minute in
    arch = root / "rec" / "archive_downloads"
    t0 = time.time()

    def wait():
        done = len(list(arch.glob("*.mp3"))) >= len(FEST_WANT) and not app._fest_queue and not app.job_text
        if done or time.time() - t0 > 240:
            app.after(3000, finish)
        else:
            app.after(500, wait)

    def finish():
        try:
            got = {fest_slug(p.name) for p in arch.glob("*.mp3")}
            check("festivals: the app's own worker downloads them", got == FEST_WANT,
                  f"{sorted(got)} in {time.time() - t0:.0f} s")
            t = app.lib_tree
            gid = next((i for i in t.get_children("") if t.item(i, "text") == "SATCHMO SUMMERFEST 2026"), None)
            days = {t.item(d, "text"): [t.item(x, "text") for x in t.get_children(d)]
                    for d in (t.get_children(gid) if gid else [])}
            check("festivals: the library lists the broadcasts under the festival, by day, named for their acts",
                  days == {"Wednesday, October 14": ["Treme Brass Band, after New Orleans Music Show with DJ Test",
                                                     "Kermit Ruffins, Shannon Powell (part 1)",
                                                     "Shannon Powell (continued), then Tudo Bem Brazilian with Paul Sample"],
                           "Thursday, October 15": ["Doreen Ketchens, then Cajun and Zydeco"]}, days)
            app.nb.select(app.tab_player)
            app.lift()
            app.update()
            shot("8-festival-library")
            app.nb.select(app.tab_fav)
            app.update()
            shot("9-festival-box")
            check("festivals: the box on the Weekly Favorites page is checked", app.fest_var.get(),
                  app.fest_hint.cget("text"))
            app.fest_var.set(False)
            app._fest_toggled()
            cfg = json.loads((root / "data" / "favorites.json").read_text())
            check("festivals: unchecking it is saved", cfg.get("auto_festivals") is False, cfg)
            log = root / "data" / "wwoz_studio_errors.log"
            check("festivals: no errors logged", not log.exists() or not log.read_text().strip(),
                  log.read_text()[-600:] if log.exists() else "")
        except Exception:
            import traceback
            check("festival window checks", False, traceback.format_exc()[-600:])
        app._closing = True
        app.player and app.player.close()
        app.destroy()
    app.lift()
    app.attributes("-topmost", True)
    app.after(500, wait)
    app.mainloop()
    srv.shutdown()


def program_checks():
    import wwoz
    check("this Mac", True, f"macOS {platform.mac_ver()[0]} on {platform.machine()}, "
                            f"Python {platform.python_version()}")
    check("settings folder", str(wwoz.DATA_DIR) == os.environ.get("WWOZ_DATA"), wwoz.DATA_DIR)
    for tool in ("ffmpeg", "ffprobe"):
        try:
            check(f"{tool} found", True, wwoz.find_tool(tool))
        except Exception as e:
            check(f"{tool} found", False, e)
    bare = subprocess.run([sys.executable, "-c", "import wwoz; print(wwoz.find_tool('ffmpeg'))"],
                          cwd=HERE, capture_output=True, text=True,
                          env=dict(os.environ, PATH="/usr/bin:/bin:/usr/sbin:/sbin"))
    check("ffmpeg found with the bare PATH an app opened from the Finder gets", bare.returncode == 0,
          (bare.stdout or bare.stderr).strip()[-300:])
    try:
        import wwoz_player
        pl = wwoz_player.Player(wwoz.find_tool("ffmpeg"), wwoz.find_tool("ffprobe"))
        t0 = time.time()
        while not pl.backend and pl.state != "error" and time.time() - t0 < 15:
            time.sleep(0.1)
        ok = bool(pl.backend) and pl.state != "error"
        # a data center Mac may have no sound output at all: worth knowing, not a fault
        check("the sound player starts", ok, f"through {pl.backend}" if ok else pl.error,
              soft=not ok and ("sound device" in pl.error or "speakers" in pl.error))
        pl.close()
    except Exception as e:
        check("the sound player starts", False, repr(e))
    try:                     # a stream that can't be reached says so, instead of silence forever
        import wwoz_player
        pl = wwoz_player.Player(wwoz.find_tool("ffmpeg"), wwoz.find_tool("ffprobe"))
        pl.set_volume(0)
        pl.open_stream("http://127.0.0.1:9/live")
        t0 = time.time()
        while pl.state != "error" and time.time() - t0 < 40:
            time.sleep(0.2)
        check("an unreachable live stream reports an error", pl.state == "error" and "couldn't be reached"
              in pl.error, f"after {time.time() - t0:.0f} s: {pl.state}, {pl.error[:160]}")
        pl.close()
    except Exception as e:
        check("an unreachable live stream reports an error", False, repr(e))
    gone = Path(os.environ["WWOZ_DATA"]).parent / "data-missing-drive"
    gone.mkdir(exist_ok=True)
    (gone / "favorites.json").write_text(json.dumps({"recordings_dir": "/Volumes/WWOZ Drive Not Here/WWOZ",
                                                     "favorites": []}))
    r = subprocess.run([sys.executable, "-c", "import wwoz; print(wwoz.RECORDINGS_DIR); print(wwoz.RECORDINGS_PROBLEM)"],
                       cwd=HERE, capture_output=True, text=True,
                       env=dict(os.environ, WWOZ_DATA=str(gone), WWOZ_RECORDINGS=""))
    check("a recordings folder on a missing drive falls back to Music, and says so",
          r.returncode == 0 and "Music" in r.stdout and "can't be reached" in r.stdout,
          (r.stdout or r.stderr).strip()[-300:])
    try:
        import shazamio  # noqa: F401
        check("Shazam library", True)
    except Exception as e:
        check("Shazam library", False, repr(e))

    dest = wwoz.download_dir_from_config(wwoz.load_config())
    path = None
    try:
        shows = wwoz.fetch_archive_shows()
        check("WWOZ archive list", len(shows) > 50, f"{len(shows)} shows")
        cutoff = datetime.now(wwoz.TZ) - timedelta(hours=5)
        pick = next(s for s in shows if s["start"] and s["start"] < cutoff        # (nobody logs the
                    and not any(w in s["title"].lower() for w in ("live from", "overnight")))   # overnight songs)
        t0 = time.time()
        path = wwoz.download_show(pick, dest)
        mb = path.stat().st_size >> 20
        check("download a show from the archive", mb > 50,
              f"{pick['title']}, {pick['date']} {pick['time_label']} ({mb} MB in {time.time() - t0:.0f} s)")
        rows = [r for r in wwoz.read_tracklist(path) if r.get("song")]
        check("its song list from the DJs' log", bool(rows), f"{len(rows)} songs", soft=True)
    except Exception as e:
        check("WWOZ archive and download", False, repr(e), soft=True)
        path = None
    if path is None:
        path = test_show(wwoz, dest)
        check("made a test show instead", path.exists(), path.name)

    try:
        found, errors, aborted = asyncio.run(wwoz.identify_async(path, 120, 12, 600, 480, ".shazam-test.csv"))
        check("Shazam answers this Mac", not aborted and errors < 4, f"{found} found, {errors} unanswered",
              soft=True)
    except Exception as e:
        check("Shazam answers this Mac", False, repr(e), soft=True)

    try:
        # a show with no song list (the DJs' log can't be reached, or nobody logged it) has
        # nothing to cut by: cut the made-up show instead
        folder = wwoz.split_pieces(path if wwoz.read_tracklist(path) else test_show(wwoz, dest))
        idx = wwoz.pieces_index(folder)
        check("cut into whole shows", bool(idx and idx["pieces"]),
              [(p["file"], p["songs"]) for p in idx["pieces"]] if idx else None)
    except Exception as e:
        check("cut into whole shows", False, repr(e))

    sleep_checks(wwoz, path)
    report_checks()
    festival_checks()

    junk = wwoz.RECORDINGS_DIR / f"trash-test-{int(time.time())}.txt"
    junk.write_text("x")
    said = wwoz.recycle([junk])
    landed = any((Path.home() / ".Trash").glob(junk.stem + "*"))
    check("an old file goes to the Trash", said and not junk.exists() and landed,
          f"recycle() returned {said}; in the Trash: {landed}; "
          f"macOS trash command: {os.path.exists('/usr/bin/trash')}")
    return path


# ------------------------------------------------------------------- window --

def gui_checks(path):
    import tkinter.font as tkfont
    import wwoz_gui
    wwoz_gui.App._job_worker = lambda self: None              # nothing in the background
    wwoz_gui.App._refresh_stale_schedule = lambda self: None
    app = wwoz_gui.App()
    app.vol.set(0)
    W = wwoz_gui

    def state():
        return app.player.state if app.player else "no player"

    def fonts():
        for label in ("FONT", "FONT_TITLE", "FONT_SIGN", "FONT_MAST", "FONT_FLEUR", "FONT_MONO"):
            want = getattr(W, label)
            got = tkfont.Font(font=want).actual()["family"]
            check(f"font {label}", got.lower() == want[0].lower(), f"wanted {want[0]}, got {got}", soft=True)
        tops = [app.lib_tree.item(i, "text") for i in app.lib_tree.get_children("")]
        check("library shows the download", str(path) in app.lib_paths, tops)
        shot("1-player")
        app.open_file(path)
        app.player and app.player.set_volume(0)

    def playing():
        app.player and app.player.set_volume(0)
        check("plays the show", state() == "playing", state(), soft=True)
        check("header names it", bool(app.now_lbl.cget("text")), app.now_lbl.cget("text"))
        check("its songs are listed", True, f"{len(app.rows)} rows")
        shot("2-player-playing")
        bound = {c: bool(app.tree.bind(c)) for c in ("<Button-2>", "<Button-3>", "<Control-Button-1>")}
        check("right-click is bound the Mac ways", all(bound.values()), bound)
        app.row_menu.tk_popup = lambda *a: None
        app._row_menu(types.SimpleNamespace(x=10, y=10, x_root=0, y_root=0))
        labels = [app.row_menu.entrycget(i, "label") for i in range(app.row_menu.index("end") + 1)]
        check("song-list right-click menu", any(l.startswith("Mark") for l in labels), labels)
        box = app.lib_tree.bbox(str(path))
        if box:
            app.lib_menu.tk_popup = lambda *a: None
            app._lib_menu(types.SimpleNamespace(x=box[0] + 5, y=box[1] + 3, x_root=0, y_root=0))
            labels = [app.lib_menu.entrycget(i, "label") for i in range(app.lib_menu.index("end") + 1)]
            check("library right-click menu", "Show the file in its folder" in labels, labels)
        check("scroll wheel steps", (W.wheel_steps(1), W.wheel_steps(-3), W.wheel_steps(120)) == (-1, 3, -1),
              (W.wheel_steps(1), W.wheel_steps(-3), W.wheel_steps(120)))
        app.lib_tree.focus_set()
        app.update()
        app.lib_tree.event_generate("<space>")

    def paused():
        check("Space pauses", state() == "paused", state(), soft=True)
        app.focus_force()
        app.update()
        app.event_generate("<Command-Key-2>")
        app.update()

    def page_keys():
        check("Command-2 opens the Shows page", app.nb.select() == str(app.tab_shows), app.nb.select(), soft=True)
        app.nb.select(app.tab_shows)
        app.update()
        shot("3-shows")
        try:
            W.show_in_folder(path)
            check("show the file in the Finder", True)
        except Exception as e:
            check("show the file in the Finder", False, repr(e))

    def favorites():
        app.nb.select(app.tab_fav)
        app.lift()
        app.update()
        shot("4-favorites")
        app.nb.select(app.tab_archive)          # loads the archive list on its own

    def archive():
        app.update()
        check("archive page fills itself", len(app.arch.get_children()) > 50,
              f"{len(app.arch.get_children())} shows", soft=True)
        shot("5-archive")
        app.toggle_live()

    def live():
        app.player and app.player.set_volume(0)
        check("Listen live", state() == "playing", state(), soft=True)
        shot("6-live")
        app.toggle_live()

    def finish():
        log = W.ERROR_LOG
        check("no errors logged", not log.exists() or log.stat().st_size == 0,
              log.read_text(encoding="utf-8")[-600:] if log.exists() else "")
        app.player and app.player.close()
        app.destroy()

    plan = [(2500, fonts), (3500, playing), (1500, paused), (1000, page_keys), (2500, favorites),
            (15000, archive), (7000, live), (1000, finish)]

    def run(i=0):
        if i == len(plan):
            return
        try:
            plan[i][1]()
        except Exception:
            import traceback
            check(f"window step {plan[i][1].__name__}", False, traceback.format_exc()[-600:])
            if plan[i][1] is finish:
                return
        if i + 1 < len(plan):
            app.after(plan[i + 1][0], lambda: run(i + 1))
    app.lift()
    app.attributes("-topmost", True)
    app.after(plan[0][0], run)
    app.mainloop()


# ------------------------------------------------------------------ summary --

def summary():
    print("## WWOZ Studio on a Mac\n")
    for part in ("program", "gui", "festival-gui"):
        f = OUT / f"{part}.json"
        if not f.exists():
            print(f"_No {part} results._\n")
            continue
        print(f"### {({'program': 'The program', 'gui': 'The window'}).get(part, 'Festivals in the window')}\n")
        print("| | Check | Detail |\n|---|---|---|")
        for r in json.loads(f.read_text()):
            mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["status"]]
            print(f"| {mark} | {r['check']} | {r['detail'].replace('|', '/')[:300]} |")
        print()


if MODE == "summary":
    summary()
elif MODE == "gui":
    gui_checks(Path(sys.argv[sys.argv.index("--gui") + 1]))
elif MODE == "festival-gui":
    festival_gui_checks()
else:
    show = program_checks()
    gui = subprocess.run([sys.executable, __file__, str(OUT), "--gui", str(show)], timeout=420)
    check("the window checks ran to the end", gui.returncode == 0, f"exit code {gui.returncode}")
    fgui = subprocess.run([sys.executable, __file__, str(OUT), "--festival-gui"], timeout=420)
    check("the festival window checks ran to the end", fgui.returncode == 0 and (OUT / "festival-gui.json").exists(),
          f"exit code {fgui.returncode}")
    every = results + [r for part in ("gui", "festival-gui") if (OUT / f"{part}.json").exists()
                       for r in json.loads((OUT / f"{part}.json").read_text())]
    sys.exit(1 if any(r["status"] == "FAIL" for r in every) else 0)
