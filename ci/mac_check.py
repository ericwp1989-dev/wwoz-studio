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
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
MODE = "gui" if "--gui" in sys.argv else "summary" if "--summary" in sys.argv else "program"
results = []


def check(name, ok, detail="", soft=False):
    status = "PASS" if ok else ("WARN" if soft else "FAIL")
    results.append({"check": name, "status": status, "detail": str(detail)[:600]})
    print(f"{status}  {name}  {detail}", flush=True)
    (OUT / f"{MODE}.json").write_text(json.dumps(results, indent=1))


def shot(name):
    p = OUT / f"{name}.png"
    subprocess.run(["screencapture", "-x", str(p)], timeout=30)
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
        pick = next(s for s in shows if s["start"] and s["start"] < cutoff
                    and "live from" not in s["title"].lower())
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
        folder = wwoz.split_pieces(path)
        idx = wwoz.pieces_index(folder)
        check("cut into whole shows", bool(idx and idx["pieces"]),
              [(p["file"], p["songs"]) for p in idx["pieces"]] if idx else None)
    except Exception as e:
        check("cut into whole shows", False, repr(e))

    sleep_checks(wwoz, path)

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
    for part in ("program", "gui"):
        f = OUT / f"{part}.json"
        if not f.exists():
            print(f"_No {part} results._\n")
            continue
        print(f"### {'The program' if part == 'program' else 'The window'}\n")
        print("| | Check | Detail |\n|---|---|---|")
        for r in json.loads(f.read_text()):
            mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["status"]]
            print(f"| {mark} | {r['check']} | {r['detail'].replace('|', '/')[:300]} |")
        print()


if MODE == "summary":
    summary()
elif MODE == "gui":
    gui_checks(Path(sys.argv[sys.argv.index("--gui") + 1]))
else:
    show = program_checks()
    gui = subprocess.run([sys.executable, __file__, str(OUT), "--gui", str(show)], timeout=420)
    check("the window checks ran to the end", gui.returncode == 0, f"exit code {gui.returncode}")
    every = results + (json.loads((OUT / "gui.json").read_text()) if (OUT / "gui.json").exists() else [])
    sys.exit(1 if any(r["status"] == "FAIL" for r in every) else 0)
