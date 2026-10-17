#!/usr/bin/env python3
"""WWOZ Studio — desktop app for WWOZ recordings.

Listen to recordings with their song lists, browse festival sets day by day and
WWOZ shows by show, download from WWOZ's 2-week archive and manage weekly
favorites. Song lists, set detection, cutting and Shazam scans run on their own.
"""

import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
    sys.path.insert(0, str(SCRIPT_DIR))
else:
    SCRIPT_DIR = Path(__file__).resolve().parent

APP = "WWOZ Studio"


def cannot_start(exc, log=None):
    """Say in a box why the app can't open. It runs without a console, so an error while
    starting would otherwise close it with no word at all: the desktop icon would just
    seem to do nothing."""
    import traceback
    setup = "Setup (Mac).command" if sys.platform == "darwin" else "Setup.cmd"
    if isinstance(exc, ModuleNotFoundError):
        why = (f"A part it needs (the Python package \"{exc.name}\") isn't installed. "
               f"Double-click {setup} in the program's folder, then open WWOZ Studio again.")
    else:
        why = (f"{exc}\n\nDouble-click {setup} in the program's folder and try again. If that "
               f"doesn't help, the details are in the error log.")
    log = log or (Path(os.environ.get("WWOZ_DATA") or Path.home() / "Documents" / "WWOZ Studio")
                  / "wwoz_studio_errors.log")
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %I:%M:%S %p}\n{traceback.format_exc()}")
        why += f"\n\nError log: {log}"
    except OSError:
        pass
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP, f"WWOZ Studio couldn't open.\n\n{why}", parent=root)
        root.destroy()
    except tk.TclError:
        pass
    sys.exit(1)


try:
    import wwoz  # the CLI module, reused as a library
    import wwoz_player  # the sound: ffmpeg and miniaudio in a helper process
except Exception as _e:
    cannot_start(_e)

RECORDINGS_DIR = wwoz.RECORDINGS_DIR
STATE_PATH = wwoz.DATA_DIR / "player_state.json"   # where you stopped in each show
AUDIO_EXT = {".mp3", ".mkv", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".mp4"}
FIELDS = ["offset", "clock_time", "artist", "song", "release", "show", "offset_seconds"]


def glob_escape(s):
    """A literal name for Path.glob (brackets in a file name would be a pattern)."""
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def fmt(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02}:{sec % 60:02}"


ERROR_LOG = wwoz.DATA_DIR / "wwoz_studio_errors.log"


def log_error():
    """Append the current exception's traceback to the error log (the app runs
    without a console, so nothing else would keep it)."""
    import traceback
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %I:%M:%S %p}\n{traceback.format_exc()}")
    except OSError:
        pass


def log_line(text):
    """One line in the error log, for a problem that isn't a crash (such as sound that
    wouldn't play), so the reason survives after the message box is closed."""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %I:%M:%S %p}\n{text}\n")
    except OSError:
        pass


def log_error_from(exc_info):
    """log_error for an exception Tk caught in a button or timer callback."""
    import traceback
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %I:%M:%S %p}\n"
                    + "".join(traceback.format_exception(*exc_info)))
    except OSError:
        pass


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    try:
        wwoz.write_text_atomic(STATE_PATH, json.dumps(st, indent=1))
    except Exception:
        pass


def archive_dir():
    """Where archive downloads land (favorites.json download_dir, inside the recordings
    folder by default) — the same folder the Player and Shows tabs list."""
    return wwoz.download_dir_from_config(wwoz.load_config())


def open_folder(folder):
    """Open a folder in File Explorer, or in the Finder on a Mac."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        os.startfile(folder)


def show_in_folder(path):
    """Open the folder a file is in with the file selected."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["explorer", "/select,", str(path)])


def wheel_steps(delta):
    """Lines to scroll for a mouse-wheel event: Windows counts 120 to a notch of the
    wheel, while a Mac sends the steps themselves (1, 2, 3...)."""
    if sys.platform == "darwin" and abs(delta) < 120:
        return -delta
    return -int(delta / 120)


# The French Quarter: plaster-cream walls, wrought-iron black, gas-lamp amber for
# what is playing, and the Mardi Gras purple, green and gold for everything you can
# act on. Headings are set like the street names inlaid in the sidewalk tiles.
BG, PANEL, INK, INK2, LINE = "#f4ecdd", "#fffaf1", "#1e1a21", "#6e5f52", "#e0d1b8"
IRON, IRON_HI, CREAM = "#1c1820", "#3a3340", "#f4ecdd"
PURPLE, PURPLE_HI, PURPLE_LO = "#5b2a86", "#6f3a9c", "#43206a"
GREEN, GOLD, GOLD_HI = "#1f6b45", "#c9971c", "#e2b545"
TILE = "#1f4e8c"                                     # sidewalk-tile blue
BRASS, BRASS_BG, VIOLET, SEL = "#9a6a12", "#f8e2a8", PURPLE, "#ece1f2"
SLATE, CHALK = "#2c3631", "#e8e3d4"                  # the po-boy shop chalkboard
MAC = sys.platform == "darwin"
if MAC:
    # the same faces from the Mac's own fonts, a third larger: Tk on a Mac counts 72
    # points to the inch where Windows counts 96, so this keeps the sizes on screen
    FONT, FONT_B, FONT_SM = ("Helvetica Neue", 13), ("Helvetica Neue", 13, "bold"), ("Helvetica Neue", 12)
    FONT_SM_B, FONT_PLAY, FONT_HEAD = ("Helvetica Neue", 12, "bold"), ("Helvetica Neue", 15, "bold"), \
        ("Helvetica Neue", 12, "bold")
    FONT_TITLE, FONT_MONO, FONT_LOG = ("Georgia", 23, "bold"), ("Menlo", 13), ("Menlo", 12)
    FONT_SIGN, FONT_SIGN_LT = ("Copperplate", 12, "bold"), ("Copperplate", 12)
    FONT_MAST, FONT_FLEUR, FONT_AIR = ("Bodoni 72", 28, "bold"), ("Apple Symbols", 37), ("Georgia", 15)
else:
    FONT, FONT_B, FONT_SM = ("Segoe UI", 10), ("Segoe UI Semibold", 10), ("Segoe UI", 9)
    FONT_SM_B, FONT_PLAY, FONT_HEAD = ("Segoe UI", 9, "bold"), ("Segoe UI Semibold", 11), \
        ("Segoe UI Semibold", 9)
    FONT_TITLE, FONT_MONO, FONT_LOG = ("Georgia", 17, "bold"), ("Consolas", 10), ("Consolas", 9)
    FONT_SIGN = ("Copperplate Gothic Bold", 9)           # engraved-sign capitals for headings
    FONT_SIGN_LT = ("Copperplate Gothic Light", 9)
    FONT_MAST, FONT_FLEUR, FONT_AIR = ("Bodoni MT Black", 21), ("Segoe UI Symbol", 28), ("Georgia", 11)
MOD, MOD_NAME = ("Command", "Command") if MAC else ("Control", "Ctrl")   # page keys, picking several

# Windows display scaling (125% or 150% on most laptops). The app tells Windows it draws
# at the screen's real size, so its text stays sharp instead of being stretched like a
# picture; fonts then grow on their own, and the sizes given in pixels grow by UI.
UI = 1.0


def px(n):
    return int(round(n * UI))


def set_ui_scale(root):
    """How much larger than 100% the screen is set (1.0 on a Mac, whose Tk does this
    itself). WWOZ_UI_SCALE tries another size on this screen (1.5 looks like 150%)."""
    global UI
    forced = os.environ.get("WWOZ_UI_SCALE")
    if forced:
        UI = float(forced)
        root.tk.call("tk", "scaling", UI * 96 / 72)
    elif not MAC:
        UI = max(1.0, root.winfo_fpixels("1i") / 96)


def pick_fonts(root):
    """The masthead and headings are set in Bodoni MT Black and Copperplate Gothic, which
    come with Microsoft Office, not Windows. Where they're missing Tk quietly uses plain
    Arial, so take the nearest faces every Windows computer has instead."""
    global FONT_SIGN, FONT_SIGN_LT, FONT_MAST
    if MAC:
        return                                       # the Mac's own fonts are always there
    have = set(tkfont.families(root))
    if "Copperplate Gothic Bold" not in have:
        FONT_SIGN = ("Segoe UI Semibold", 9)
    if "Copperplate Gothic Light" not in have:
        FONT_SIGN_LT = ("Segoe UI", 9)
    if "Bodoni MT Black" not in have:
        FONT_MAST = ("Georgia", 21, "bold")
NAV = [("Player", "tab_player"), ("Shows", "tab_shows"), ("Jazz Fest Guide", "tab_guide"),
       ("WWOZ Archive", "tab_archive"), ("Weekly Favorites", "tab_fav")]


def apply_style(root):
    st = ttk.Style(root)
    st.theme_use("clam")          # the one built-in theme that honours colours everywhere
    root.configure(background=BG)
    st.configure(".", background=BG, foreground=INK, font=FONT, bordercolor=LINE,
                 lightcolor=PANEL, darkcolor=LINE, troughcolor=LINE, focuscolor=SEL)
    st.configure("TFrame", background=BG)
    st.configure("Panel.TFrame", background=PANEL)
    st.configure("TLabel", background=BG, foreground=INK)
    st.configure("Panel.TLabel", background=PANEL)
    st.configure("Title.TLabel", background=PANEL, font=FONT_TITLE)
    st.configure("Meta.TLabel", background=PANEL, foreground=INK2, font=FONT_SM)
    st.configure("Hint.TLabel", background=BG, foreground=INK2, font=FONT_SM)
    st.configure("PanelHint.TLabel", background=PANEL, foreground=INK2, font=FONT_SM)
    st.configure("Section.TLabel", background=BG, foreground=TILE, font=FONT_SIGN)
    st.configure("TLabelframe", background=BG, bordercolor=LINE, lightcolor=BG, darkcolor=LINE)
    st.configure("TLabelframe.Label", background=BG, foreground=TILE, font=FONT_SIGN)
    st.configure("TButton", padding=(10, 4), background=PANEL, bordercolor=LINE,
                 lightcolor=PANEL, darkcolor=LINE)
    st.map("TButton", background=[("active", SEL), ("pressed", LINE)])
    st.configure("Play.TButton", font=FONT_PLAY, padding=(26, 7), background=PURPLE,
                 foreground="#ffffff", bordercolor=PURPLE_LO, lightcolor=PURPLE_HI, darkcolor=PURPLE_LO)
    st.map("Play.TButton", background=[("active", PURPLE_HI), ("pressed", PURPLE_LO)],
           foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
    st.configure("Go.TButton", font=FONT_B, background=GREEN, foreground="#ffffff",
                 bordercolor=GREEN, lightcolor=GREEN, darkcolor=GREEN)
    st.map("Go.TButton", background=[("active", "#2a8457"), ("pressed", "#174f33")],
           foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
    st.configure("Link.TButton", padding=(2, 1), background=PANEL, foreground=PURPLE, bordercolor=PANEL,
                 lightcolor=PANEL, darkcolor=PANEL, font=FONT_SM)
    st.map("Link.TButton", background=[("active", PANEL)], foreground=[("active", BRASS)])
    st.configure("TEntry", fieldbackground=PANEL, bordercolor=LINE, lightcolor=PANEL, darkcolor=LINE, padding=4)
    st.configure("TCombobox", fieldbackground=PANEL, background=PANEL, bordercolor=LINE, arrowcolor=INK2)
    st.map("TCombobox", fieldbackground=[("readonly", PANEL)], background=[("readonly", PANEL)],
           foreground=[("readonly", INK)], selectbackground=[("readonly", PANEL)],
           selectforeground=[("readonly", INK)])
    st.configure("TCheckbutton", background=BG, indicatorbackground=PANEL, indicatorforeground=PURPLE)
    st.map("TCheckbutton", indicatorbackground=[("selected", PANEL)], background=[("active", BG)])
    # the notebook's own tabs are hidden; the masthead's street-sign menu switches pages
    st.layout("Bare.TNotebook.Tab", [])
    st.configure("Bare.TNotebook", background=BG, borderwidth=0, tabmargins=0)
    st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK, rowheight=px(27),
                 bordercolor=LINE, lightcolor=PANEL, darkcolor=LINE, font=FONT)
    st.configure("Treeview.Heading", background="#efe4d0", foreground=TILE, font=FONT_HEAD,
                 bordercolor=LINE, relief="flat", padding=(6, 5))
    st.map("Treeview.Heading", background=[("active", "#e8dbc3")])
    st.map("Treeview", background=[("selected", SEL)], foreground=[("selected", INK)])
    st.configure("TScale", background=PURPLE, troughcolor=LINE, bordercolor=LINE,
                 lightcolor=PURPLE_HI, darkcolor=PURPLE_LO)
    st.configure("TScrollbar", background=BG, troughcolor=BG, bordercolor=BG, arrowcolor=INK2,
                 lightcolor=BG, darkcolor=BG)
    st.configure("TPanedwindow", background=BG)
    st.configure("Sash", sashthickness=8, gripcount=0)


def style_text(t):
    t.configure(background=PANEL, foreground=INK, relief="flat", highlightthickness=1,
                highlightbackground=LINE, highlightcolor=LINE, padx=8, pady=6, font=FONT)


def draw_railing(c, width, height=30):
    """The cast-iron lace under a French Quarter gallery: a top rail with arches hanging
    from it, a rosette under each arch, thin balusters between, a light bottom rail, and
    a heavier post every sixth panel. Drawn at 100% and then scaled to the screen."""
    c.delete("all")
    width = int(width / UI) + 1
    unit, top, bot = 34, 4, height - 2
    c.create_rectangle(0, 0, width, top, fill=IRON, outline="")
    c.create_line(0, bot, width, bot, fill=IRON, width=2)
    for k, x in enumerate(range(0, width + unit, unit)):
        if k % 6 == 0:                                   # a post, with a ball at its foot
            c.create_rectangle(x - 2, 0, x + 2, bot, fill=IRON, outline="")
            c.create_oval(x - 4, bot - 4, x + 4, bot + 2, fill=IRON, outline="")
        else:
            c.create_line(x, top, x, bot, fill=IRON, width=2)
            c.create_polygon(x, 14, x + 3, 18, x, 22, x - 3, 18, fill=IRON, outline="")
        cx = x + unit / 2
        c.create_arc(x + 1, top - 13, x + unit - 1, top + 13, start=180, extent=180,
                     style="arc", outline=IRON, width=2)          # the hanging arch
        c.create_arc(x + 8, top - 6, x + unit - 8, top + 7, start=180, extent=180,
                     style="arc", outline=IRON, width=1)
        r = 4
        c.create_oval(cx - r, 21 - r, cx + r, 21 + r, outline=IRON, width=2)   # rosette
        c.create_line(cx, top + 13, cx, 21 - r, fill=IRON, width=1)
        c.create_line(cx, 21 + r, cx, bot, fill=IRON, width=1)
        for dx in (-7, 7):
            c.create_oval(cx + dx - 1.5, 20, cx + dx + 1.5, 23, fill=IRON, outline="")
    if UI != 1:
        c.scale("all", 0, 0, UI, UI)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        set_ui_scale(self)
        pick_fonts(self)
        self.title(APP)
        # fit the screen it opens on: a small laptop (1280x800, or a Mac's 1024x768 at its
        # largest text) is smaller than the 1240x880 the app likes, and a minimum size
        # bigger than the screen pushes the window off its edge and under the Dock
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(px(1240), sw - px(40)), min(px(880), sh - px(120))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, min(px(40), sh - h - px(100)))}")
        self.minsize(min(px(1100), w), min(px(720), h))
        self.compact = h < px(760) or w < px(1100)       # a small screen: trim what can go
        try:
            if MAC:                                  # the Dock's icon (a Mac reads no .ico)
                self.iconphoto(True, tk.PhotoImage(file=str(SCRIPT_DIR / "wwoz.png")))
            else:
                self.iconbitmap(str(SCRIPT_DIR / "wwoz.ico"))
        except tk.TclError:
            pass
        apply_style(self)
        self._slots = None          # weekly schedule slots, read once from wwoz_schedule.json
        self._schedule_loading = False   # the first (or weekly) read of wwoz.org is under way
        self._last_now = None       # song row last scrolled into view

        self.player = None          # wwoz_player.Player, started by _init_player
        self._play_error_said = ""  # the playback error last shown, so it is shown once
        self.current_file = None
        self.live = False           # playing WWOZ's live stream rather than a file
        self.rows = []            # tracklist rows (dicts, FIELDS keys)
        self.duration = 0
        self.seeking = False
        self.state = load_state()
        self.log_q = queue.Queue()
        self.busy = False
        self._fest_sets = {}        # per-set festival file -> (festival, its festival.json entry)
        self._fest_casts = {}       # festival broadcast download -> festival
        self._fest_progs = {}       # WWOZ show cut from a festival broadcast -> (festival, entry)
        self._fests = []
        self._items = {}            # every playable file -> what it is (see _catalog)
        self._seen_groups = set()
        self._loaded_keys = set()   # tracklist rows on disk when the open file was loaded
        self.guide_data = wwoz.load_fest_schedule()     # Jazz Fest days, needed by the library
        wwoz.out = self.logln  # all toolkit output -> Activity pane

        # background work (see _next_job): one job at a time, Shazam only while nothing plays
        self.job_text = ""
        self._job_done = set()
        self._job_tried = {}        # when a song list was last fetched (see _next_job)
        self._job_proc = None
        self._closing = False
        self.scan_now = None
        self.scan_resume_at = 0        # when Shazam goes quiet, scans wait until then
        self._other_scanner = False    # the Sunday task's scan lock was seen

        self._build_ui()
        self._init_player()
        self._bind_keys()
        self.refresh_library()
        self.refresh_shows()
        self.after(400, self._tick)
        self.after(200, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if MAC:                                      # Command-Q too, not only the red button
            self.createcommand("::tk::mac::Quit", self._on_close)
        threading.Thread(target=self._job_worker, daemon=True).start()
        self.after(3000, self._refresh_stale_schedule)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        self._build_masthead()
        nb = ttk.Notebook(self, style="Bare.TNotebook")
        self.nb = nb
        self.tab_player = ttk.Frame(nb)
        self.tab_shows = ttk.Frame(nb)
        self.tab_guide = ttk.Frame(nb)
        self.tab_archive = ttk.Frame(nb)
        self.tab_fav = ttk.Frame(nb)
        for label, attr in NAV:
            nb.add(getattr(self, attr), text=label)
        nb.bind("<<NotebookTabChanged>>", lambda e: self._page_changed())

        # ---- activity: the chalkboard at the bottom of every page ----
        logf = tk.Frame(self, background=SLATE)
        logf.pack(fill="x", side="bottom")
        tk.Label(logf, text="ACTIVITY", background=SLATE, foreground=GOLD_HI, font=FONT_SIGN,
                 anchor="w").pack(fill="x", padx=12, pady=(6, 0))
        self.log = tk.Text(logf, height=2 if self.compact else 4, wrap="word", state="disabled", relief="flat",
                           background=SLATE, foreground=CHALK, insertbackground=CHALK,
                           highlightthickness=0, padx=12, pady=4, font=FONT_LOG)
        self.log.pack(fill="x", padx=0, pady=(0, 8))
        nb.pack(fill="both", expand=True, pady=(4, 0))

        self._build_player_tab()
        self._build_shows_tab()
        self._build_guide_tab()
        self._build_archive_tab()
        self._build_fav_tab()

    # ------------------------------------------------------------ masthead --

    def _build_masthead(self):
        """The wrought-iron band across the top: the station's name, what is on the air
        now with a Listen live button, and the page menu; under it a Mardi Gras stripe
        and a cast-iron gallery railing."""
        band = tk.Frame(self, background=IRON)
        band.pack(fill="x", side="top")
        top = tk.Frame(band, background=IRON)
        top.pack(fill="x", padx=18, pady=(10, 0))
        tk.Label(top, text="⚜", background=IRON, foreground=GOLD,
                 font=FONT_FLEUR).pack(side="left")
        name = tk.Frame(top, background=IRON)
        name.pack(side="left", padx=(8, 0))
        tk.Label(name, text="WWOZ Studio", background=IRON, foreground=CREAM,
                 font=FONT_MAST).pack(anchor="w")
        tk.Label(name, text="90.7 FM    NEW ORLEANS", background=IRON, foreground=GOLD,
                 font=FONT_SIGN_LT).pack(anchor="w", padx=(2, 0))

        air = tk.Frame(top, background=IRON)
        air.pack(side="right")
        self.live_btn = tk.Label(air, text="▶  Listen live", background=GOLD, foreground=IRON,
                                 font=FONT_B, padx=16, pady=7, cursor="hand2")
        self.live_btn.pack(side="right")
        self.live_btn.bind("<Button-1>", lambda e: self.toggle_live())
        self.live_btn.bind("<Enter>", lambda e: self.live_btn.configure(
            background=GOLD_HI if not self.live else "#d8574a"))
        self.live_btn.bind("<Leave>", lambda e: self.live_btn.configure(
            background=GOLD if not self.live else "#b8433a"))
        txt = tk.Frame(air, background=IRON)
        txt.pack(side="right", padx=(0, 16))
        tk.Label(txt, text="ON THE AIR NOW", background=IRON, foreground=GOLD,
                 font=FONT_SIGN).pack(anchor="e")
        self.air_lbl = tk.Label(txt, text="", background=IRON, foreground=CREAM, font=FONT_AIR)
        self.air_lbl.pack(anchor="e")
        self.air_sub = tk.Label(txt, text="", background=IRON, foreground="#b3a796", font=FONT_SM)
        self.air_sub.pack(anchor="e")

        # the page menu, lettered like street signs; the current page is lit in gold
        nav = tk.Frame(band, background=IRON)
        nav.pack(fill="x", padx=12, pady=(8, 0))
        self.nav_items = []
        for i, (label, attr) in enumerate(NAV):
            cell = tk.Frame(nav, background=IRON, cursor="hand2")
            cell.pack(side="left", padx=(0, 4))
            lbl = tk.Label(cell, text=label.upper(), background=IRON, foreground=CREAM,
                           font=FONT_SIGN, padx=12, pady=6, cursor="hand2")
            lbl.pack()
            bar = tk.Frame(cell, height=3, background=IRON)
            bar.pack(fill="x")
            for w in (cell, lbl):
                w.bind("<Button-1>", lambda e, a=attr: self.nb.select(getattr(self, a)))
                w.bind("<Enter>", lambda e, k=i: self._draw_nav(hover=k))
                w.bind("<Leave>", lambda e: self._draw_nav())
            self.nav_items.append((attr, lbl, bar))
            self.bind_all(f"<{MOD}-Key-{i + 1}>", lambda e, a=attr: self.nb.select(getattr(self, a)))

        stripe = tk.Canvas(band, height=px(5), background=IRON, highlightthickness=0)
        stripe.pack(fill="x")

        def paint_stripe(e):
            stripe.delete("all")
            w = e.width / 3
            for k, color in enumerate((PURPLE, GREEN, GOLD)):
                stripe.create_rectangle(k * w, 0, (k + 1) * w + 1, px(5), fill=color, outline="")
        stripe.bind("<Configure>", paint_stripe)
        rail = tk.Canvas(self, height=px(30), background=BG, highlightthickness=0)
        rail.pack(fill="x", side="top")
        rail.bind("<Configure>", lambda e: draw_railing(rail, e.width))
        self.after(500, self._update_on_air)

    def _page_changed(self):
        self._draw_nav()
        # the archive list loads itself the first time its page is opened
        if (self.nb.select() == str(self.tab_archive) and not getattr(self, "archive_shows", True)
                and not self.busy):
            self.load_archive()

    def _draw_nav(self, hover=None):
        if not hasattr(self, "nb"):
            return
        current = self.nb.select()
        for k, (attr, lbl, bar) in enumerate(self.nav_items):
            on = str(getattr(self, attr)) == current
            lbl.configure(foreground=GOLD_HI if on else ("#ffffff" if k == hover else CREAM))
            bar.configure(background=GOLD if on else ("#5a5160" if k == hover else IRON))

    def _update_on_air(self, again=True):
        """What WWOZ is broadcasting right now, from the weekly schedule (looked at again
        every 30 seconds; again=False only refreshes it now)."""
        slot = self._slot_at(datetime.now(wwoz.TZ))
        if slot:
            self.air_lbl.configure(text=slot["title"])
            self.air_sub.configure(text=f"{wwoz.fmt_clock(slot['start'])} to {wwoz.fmt_clock(slot['end'])}"
                                   " Central")
        else:
            self.air_lbl.configure(text="WWOZ, New Orleans")
            self.air_sub.configure(text="loading the schedule…" if self._schedule_loading
                                   else "schedule not loaded yet")
        if again:
            self.after(30_000, self._update_on_air)

    def toggle_live(self):
        """Play WWOZ's live stream (or stop it)."""
        if not self.player:
            messagebox.showinfo(APP, "The sound player isn't running, so nothing can play.")
            return
        if self.live:
            self.player.stop()
            self.live = False
            self.live_btn.configure(text="▶  Listen live", background=GOLD, foreground=IRON)
            self.play_btn.config(text="Play")
            self.time_lbl.config(text="0:00:00 / 0:00:00")
            self.logln("live stream stopped")
            return
        self.live = True
        self.current_file = None
        self.duration = 0
        self.rows = []
        self._render_rows()
        self._mark_playing(None)
        slot = self._slot_at(datetime.now(wwoz.TZ))
        self.now_lbl.config(text="WWOZ live" + (f": {slot['title']}" if slot else ""))
        self.sub_lbl.config(text="The live broadcast from New Orleans, 90.7 FM"
                                 + (f", {wwoz.fmt_clock(slot['start'])} to {wwoz.fmt_clock(slot['end'])}"
                                    if slot else ""))
        self.about_lbl.grid_remove()
        self.resume_btn.grid_remove()
        self.title(f"WWOZ live - {APP}")
        self._play_error_said = ""
        self.player.open_stream(wwoz.STREAM_URL)
        self.play_btn.config(text="Pause")
        self.pos_var.set(0)
        self.time_lbl.config(text="LIVE")
        self.live_btn.configure(text="■  Stop live", background="#b8433a", foreground="#ffffff")
        self.logln("playing WWOZ's live stream")
        self.nb.select(self.tab_player)

    def _build_player_tab(self):
        t = self.tab_player
        pw = ttk.PanedWindow(t, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        self.after(150, lambda: pw.sashpos(0, min(px(400), int(self.winfo_width() * 0.33))))

        # ---- library: every recording, grouped; one click plays ----
        lib = ttk.Frame(pw)
        pw.add(lib, weight=1)
        ttk.Label(lib, text="LIBRARY", style="Section.TLabel").pack(anchor="w")
        ttk.Label(lib, text="Click a show to play it from the start. Type to search.", style="Hint.TLabel"
                  ).pack(anchor="w", pady=(0, 4))
        self.lib_filter = tk.StringVar()
        self.lib_filter.trace_add("write", lambda *_: self.refresh_library())
        ttk.Entry(lib, textvariable=self.lib_filter).pack(fill="x", pady=(0, 6))
        bb = ttk.Frame(lib)
        bb.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(bb, text="Refresh", command=self.refresh_library).pack(side="left")
        body = ttk.Frame(lib)
        body.pack(fill="both", expand=True)
        self.lib_tree = ttk.Treeview(body, columns=("when",), show="tree", selectmode="browse")
        self.lib_tree.column("#0", width=px(150 if self.compact else 190), minwidth=px(120), stretch=True)
        self.lib_tree.column("when", width=px(80 if self.compact else 100), minwidth=px(70), anchor="e",
                             stretch=False)
        ysb = ttk.Scrollbar(body, orient="vertical", command=self.lib_tree.yview)
        self.lib_tree.configure(yscrollcommand=ysb.set)
        self.lib_tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="left", fill="y")
        self.lib_tree.tag_configure("group", font=FONT_SIGN, foreground=TILE, background="#f3e9d6")
        self.lib_tree.tag_configure("day", font=FONT_B, foreground=INK2)
        self.lib_tree.tag_configure("cut", foreground=PURPLE)
        self.lib_tree.tag_configure("set", foreground=GREEN)
        self.lib_tree.tag_configure("playing", background=BRASS_BG)
        self.lib_tree.tag_configure("hint", foreground=INK2)
        self.lib_tree.bind("<ButtonRelease-1>", self._lib_click)
        self.lib_tree.bind("<Return>", lambda e: self.open_selected())
        self.lib_paths = {}

        # ---- now playing ----
        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        card = tk.Frame(right, background=PANEL, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill="x")
        tk.Frame(card, width=px(5), background=GOLD).pack(side="left", fill="y")    # gas-lamp edge
        head = ttk.Frame(card, style="Panel.TFrame", padding=(16, 12))
        head.pack(side="left", fill="both", expand=True)
        head.columnconfigure(0, weight=1)
        self.now_lbl = ttk.Label(head, text="Pick a show on the left", style="Title.TLabel",
                                 wraplength=px(760))
        self.now_lbl.grid(row=0, column=0, sticky="w")
        self.sub_lbl = ttk.Label(head, text="It starts playing right away, and its songs are "
                                            "listed below. Click any song to jump to it.",
                                 style="Meta.TLabel", wraplength=px(760))
        self.sub_lbl.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.about_lbl = ttk.Label(head, text="", style="Panel.TLabel", wraplength=px(760), font=FONT_SM)
        self.about_lbl.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.about_lbl.grid_remove()
        self.resume_btn = ttk.Button(head, text="", style="Link.TButton", command=self._resume)
        self.resume_btn.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.resume_btn.grid_remove()

        ctrl = ttk.Frame(head, style="Panel.TFrame")
        ctrl.grid(row=4, column=0, sticky="w", pady=(12, 0))
        for txt, d in [("◀ 5 min", -300), ("◀ 30 s", -30)]:
            ttk.Button(ctrl, text=txt, width=7 if self.compact else 8,
                       command=lambda d=d: self.jump(d)).pack(side="left", padx=(0, 4))
        self.play_btn = ttk.Button(ctrl, text="Play", style="Play.TButton", command=self.toggle_play)
        self.play_btn.pack(side="left", padx=8)
        for txt, d in [("30 s ▶", 30), ("5 min ▶", 300)]:
            ttk.Button(ctrl, text=txt, width=7 if self.compact else 8,
                       command=lambda d=d: self.jump(d)).pack(side="left", padx=(4, 0))
        if not self.compact:
            ttk.Label(ctrl, text="Volume", style="Meta.TLabel").pack(side="left", padx=(28, 6))
        self.vol = tk.IntVar(value=int(self.state.get("_volume", 90)))
        ttk.Scale(ctrl, from_=0, to=100, variable=self.vol, length=px(70 if self.compact else 110),
                  command=lambda v: self.player and
                  self.player.set_volume(float(v) / 100)).pack(side="left")

        seekf = ttk.Frame(head, style="Panel.TFrame")
        seekf.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.pos_var = tk.DoubleVar()
        self.seek = ttk.Scale(seekf, from_=0, to=1000, variable=self.pos_var, command=self._seek_drag)
        self.seek.pack(side="left", fill="x", expand=True)
        # click anywhere on the bar to go there, or drag
        self.seek.bind("<ButtonPress-1>", self._seek_at)
        self.seek.bind("<B1-Motion>", self._seek_at)
        self.seek.bind("<ButtonRelease-1>", self._seek_release)
        self.time_lbl = ttk.Label(seekf, text="0:00:00 / 0:00:00", style="Panel.TLabel", font=FONT_MONO)
        self.time_lbl.pack(side="left", padx=(12, 0))
        keys = ttk.Label(head, text="Space plays or pauses. The arrow keys skip 30 seconds, or 5 minutes "
                                    f"with Shift. {MOD_NAME}+1 to {MOD_NAME}+5 change pages.",
                         style="PanelHint.TLabel")
        if not self.compact:                     # on a small screen the song list needs the room
            keys.grid(row=6, column=0, sticky="w", pady=(6, 0))

        def rewrap(e):                               # text wraps to the card, whatever its width
            for w in (self.now_lbl, self.sub_lbl, self.about_lbl, keys):
                w.configure(wraplength=max(px(300), e.width - px(40)))
        head.bind("<Configure>", rewrap)

        # ---- songs: one click jumps ----
        songs = ttk.Frame(right)
        songs.pack(fill="both", expand=True, pady=(10, 0))
        bar = ttk.Frame(songs)
        bar.pack(fill="x", pady=(0, 4))
        self.songs_lbl = ttk.Label(bar, text="SONGS", style="Section.TLabel")
        self.songs_lbl.pack(side="left")
        ttk.Label(bar, text="Click a song to jump to it. Blue rows are sets, green ones your markers.",
                  style="Hint.TLabel").pack(side="left", padx=10)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._render_rows())
        ttk.Entry(bar, textvariable=self.filter_var, width=22).pack(side="right")
        ttk.Label(bar, text="Filter", style="Hint.TLabel").pack(side="right", padx=6)
        cols = ("time", "artist", "song", "show")
        self.tree = ttk.Treeview(songs, columns=cols, show="headings", selectmode="browse")
        for c, head_txt, w in [("time", "Time", 80), ("artist", "Artist", 230),
                               ("song", "Song", 320), ("show", "Source", 170)]:
            self.tree.heading(c, text=head_txt, anchor="w")
            self.tree.column(c, width=px(w), anchor="w")
        ysb = ttk.Scrollbar(songs, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._jump_to_row)
        self.tree.tag_configure("manual", foreground=GREEN)
        self.tree.tag_configure("set", foreground=TILE)
        self.tree.tag_configure("auto", foreground=PURPLE)
        self.tree.tag_configure("now", background=BRASS_BG)

        # ---- what the app is doing on its own (song lists, set detection, cutting, Shazam) ----
        tools = ttk.Frame(right)
        tools.pack(fill="x", pady=(8, 0), side="bottom", before=songs)   # never squeezed out
        self.tools_row = tools
        self.scan_lbl = ttk.Label(tools, text="", style="Hint.TLabel")
        self.scan_lbl.pack(side="left")
        ttk.Label(tools, text="Right-click a row to name a set or song by hand.",
                  style="Hint.TLabel").pack(side="right")

        # right-click menus: fixing a name in the song list, and the rare redo in the library
        self.row_menu = tk.Menu(self, tearoff=0)
        self.lib_menu = tk.Menu(self, tearoff=0)
        # a Mac's Tk 8.6 calls the right button 2 (later Tks call it 3), and Control-click
        # is the right-click of a one-button mouse
        clicks = ("<Button-2>", "<Button-3>", "<Control-Button-1>") if MAC else ("<Button-3>",)
        for click in clicks:
            self.tree.bind(click, self._row_menu)
            self.lib_tree.bind(click, self._lib_menu)

    def _row_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.focus(iid)
            self.tree.selection_set(iid)
        m = self.row_menu
        m.delete(0, "end")
        if not self.current_file:
            return
        sec = int(self.player.position) if self.player else 0
        m.add_command(label=f"Mark a set or song starting now ({fmt(sec)})...", command=self.add_marker)
        if iid:
            m.add_command(label="Rename this row...", command=self.edit_marker)
            m.add_command(label="Delete this row", command=self.delete_marker)
        m.tk_popup(e.x_root, e.y_root)

    def _lib_menu(self, e):
        iid = self.lib_tree.identify_row(e.y)
        p = self.lib_paths.get(iid)
        if not p:
            return
        self.lib_tree.focus(iid)
        self.lib_tree.selection_set(iid)
        m = self.lib_menu
        m.delete(0, "end")
        m.add_command(label="Show the file in its folder", command=lambda: show_in_folder(p))
        it = self._items.get(p, {})
        if wwoz.recording_start(p) and it.get("kind") != "set":
            m.add_command(label="Get the DJs' song list again", command=lambda: self._redo(p, "tracklist"))
        if it.get("kind") == "full" and p.parent == RECORDINGS_DIR and self._is_fest_day(p):
            m.add_command(label="Find the sets again (takes a while)", command=lambda: self._redo(p, "autocut"))
        m.tk_popup(e.x_root, e.y_root)

    # ---------------------------------------------------------------- shows --

    def _build_shows_tab(self):
        t = self.tab_shows
        top = ttk.Frame(t)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="SHOWS", style="Section.TLabel").pack(anchor="w")
        ttk.Label(top, text="Every festival set, performance and downloaded show as its own file. Pick one to "
                            "play it, rename it, or find the file to send someone. Click a heading to sort.",
                  style="Hint.TLabel").pack(anchor="w")
        bar = ttk.Frame(t)
        bar.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(bar, text="Search:").pack(side="left")
        self.show_filter = tk.StringVar()
        self.show_filter.trace_add("write", lambda *_: self._render_shows())
        ttk.Entry(bar, textvariable=self.show_filter, width=28).pack(side="left", padx=4)
        ttk.Button(bar, text="▶ Play", command=self._show_play).pack(side="left", padx=8)
        ttk.Button(bar, text="Open file location (to send it)",
                   command=self._show_explore).pack(side="left", padx=2)
        ttk.Button(bar, text="Rename…", command=self._show_rename).pack(side="left", padx=2)
        ttk.Button(bar, text="Refresh", command=self.refresh_shows).pack(side="left", padx=8)

        mid = ttk.Frame(t)
        mid.pack(fill="both", expand=True, padx=8)
        cols = ("date", "name", "source", "size")
        self.shows_tree = ttk.Treeview(mid, columns=cols, show="headings",
                                       selectmode="browse")
        for c, w, grow in [("date", 190, False), ("name", 420, True), ("source", 360, True), ("size", 80, False)]:
            self.shows_tree.heading(c, text=c.title(), anchor="w", command=lambda c=c: self._sort_shows(c))
            self.shows_tree.column(c, width=px(w), anchor="e" if c == "size" else "w", stretch=grow)
        ysb = ttk.Scrollbar(mid, orient="vertical", command=self.shows_tree.yview)
        self.shows_tree.configure(yscrollcommand=ysb.set)
        self.shows_tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="left", fill="y")
        self.shows_tree.bind("<Double-Button-1>", lambda e: self._show_play())
        self.shows_tree.bind("<<TreeviewSelect>>", lambda e: self._show_songs())

        sf = ttk.LabelFrame(t, text="Songs in this show")
        sf.pack(fill="x", padx=8, pady=6)
        self.show_songs = tk.Text(sf, height=6, wrap="word", state="disabled")
        style_text(self.show_songs)
        self.show_songs.pack(fill="x", padx=4, pady=2)
        self.show_entries = []
        self.refresh_shows()

    def refresh_shows(self):
        """Every set, show and broadcast as its own file, from the same catalog as the library."""
        items = self._catalog(archive_dir())
        entries = []
        for p, it in items.items():
            if it["kind"] == "full" and p not in self._fest_casts:
                continue                              # the 12-hour recordings stay in the library only
            c = it.get("clock")
            if it["kind"] == "set":
                src = f"{it['fest']}" + (f", {it['stage']}" if it.get("stage") else "")
            elif it["kind"] == "episode":
                src = {"download": "WWOZ archive download",
                       "recording": f"from your recording of {c:%b} {c.day}" if c else "from your recording",
                       "festival": f"around the {it['fest_full'].get('short')} broadcast"
                       if it.get("fest_full") else ""}[it["source"]]
            elif it["kind"] == "between":
                src = (it.get("fest") or "your recording") + ", between sets"
            else:
                src = f"{it['fest_full'].get('short')}, full broadcast"
            name = it["name"] if it["kind"] != "full" else self._full_title(p, it)
            entries.append({"date": f"{c:%Y-%m-%d}" if c else "", "time": f"{c:%H:%M}" if c else "",
                            "seq": 0, "name": name, "path": p, "source": src})
        self.show_entries = entries
        self._render_shows()

    def _sort_shows(self, col):
        prev, rev = getattr(self, "_shows_sort", ("date", True))
        self._shows_sort = (col, not rev if col == prev else col in ("date", "size"))
        self._render_shows()

    def _render_shows(self):
        self.shows_tree.delete(*self.shows_tree.get_children())
        flt = self.show_filter.get().lower().strip()
        col, rev = getattr(self, "_shows_sort", ("date", True))

        def size(e):
            try:
                return e["path"].stat().st_size
            except OSError:
                return 0
        order = list(enumerate(self.show_entries))
        order.sort(key=lambda ie: (ie[1]["time"], ie[1]["seq"], ie[1]["name"].lower()))
        key = {"date": lambda ie: ie[1]["date"], "name": lambda ie: ie[1]["name"].lower(),
               "source": lambda ie: ie[1]["source"].lower(), "size": lambda ie: size(ie[1])}[col]
        order.sort(key=key, reverse=rev)
        for c in ("date", "name", "source", "size"):
            arrow = (" ▼" if rev else " ▲") if c == col else ""
            self.shows_tree.heading(c, text={"date": "When", "name": "Name", "source": "From",
                                             "size": "Size"}[c] + arrow)
        for i, e in order:
            if flt and flt not in (e["name"] + e["source"] + e["date"]).lower():
                continue
            when = ""
            if e["date"]:
                d = datetime.strptime(e["date"], "%Y-%m-%d")
                when = f"{d:%a %b} {d.day}, {d.year}"
                if e["time"]:
                    t = datetime.strptime(e["time"], "%H:%M")
                    when += "  " + t.strftime("%I:%M%p").lstrip("0").lower()
            self.shows_tree.insert("", "end", iid=str(i),
                                   values=(when, e["name"], e["source"], f"{size(e) >> 20} MB"))
        if not self.show_entries:
            self.shows_tree.insert("", "end", iid="hint", values=(
                "", "Nothing yet. Shows you download on the WWOZ Archive page, or record, "
                    "appear here one file each.", "", ""))

    def _show_sel(self):
        sel = self.shows_tree.focus()
        return self.show_entries[int(sel)] if sel and sel.isdigit() else None

    def _show_songs(self):
        e = self._show_sel()
        self.show_songs.configure(state="normal")
        self.show_songs.delete("1.0", "end")
        if e:
            songs = [f"{r['artist']} - {r['song']}" for r in wwoz.read_tracklist(e["path"]) if r.get("song")]
            if songs:
                self.show_songs.insert("end", "\n".join(songs))
            else:
                self.show_songs.insert("end", "No song list: this is a live set or a show nobody "
                                              "logged, and the file is the whole performance.")
        self.show_songs.configure(state="disabled")

    def _show_play(self):
        e = self._show_sel()
        if not e:
            messagebox.showinfo(APP, "Pick a show in the list first.")
            return
        self.open_file(e["path"])
        self.nb.select(self.tab_player)

    def _show_explore(self):
        e = self._show_sel()
        if e:
            show_in_folder(e["path"])

    def _show_rename(self):
        e = self._show_sel()
        if not e:
            return
        new = simpledialog.askstring(APP, "New name for this file:",
                                     initialvalue=e["path"].stem)
        if not new or new.strip() == e["path"].stem:
            return
        new = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", new.strip())
        target = e["path"].with_name(new + e["path"].suffix)
        try:
            e["path"].rename(target)
            self.logln(f"renamed to {target.name}")
            if e["path"] in self._fest_sets:         # keep the festival's own list in step
                idx_path = e["path"].parent / "festival.json"
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
                for s in idx.get("sets", []):
                    if s["file"] == e["path"].name:
                        s["file"] = target.name
                idx_path.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError) as err:
            messagebox.showerror(APP, f"Couldn't rename: {err}")
        self.refresh_shows()
        self.refresh_library()

    def _build_guide_tab(self):
        t = self.tab_guide
        top = ttk.Frame(t)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Day:").pack(side="left")
        self.g_day = tk.StringVar()
        days = sorted(self.guide_data)
        cb = ttk.Combobox(top, textvariable=self.g_day, values=days,
                          width=12, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._render_guide())
        ttk.Label(top, text="Stage:").pack(side="left", padx=(10, 0))
        self.g_stage = tk.StringVar(value="All stages")
        stages = ["All stages"] + sorted({s["stage"] for d in self.guide_data.values()
                                          for s in d})
        cb2 = ttk.Combobox(top, textvariable=self.g_stage, values=stages,
                           width=32, state="readonly")
        cb2.pack(side="left", padx=4)
        cb2.bind("<<ComboboxSelected>>", lambda e: self._render_guide())

        cols = ("time", "artist", "stage")
        self.guide = ttk.Treeview(t, columns=cols, show="headings",
                                  selectmode="extended")
        for c, w, grow in [("time", 120, False), ("artist", 480, True), ("stage", 280, True)]:
            self.guide.heading(c, text=c.title(), anchor="w")
            self.guide.column(c, width=px(w), anchor="w", stretch=grow)
        ysb = ttk.Scrollbar(t, orient="vertical", command=self.guide.yview)
        self.guide.configure(yscrollcommand=ysb.set)
        self.guide.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=4)
        ysb.pack(side="left", fill="y", pady=4)
        self.guide.bind("<Double-Button-1>", self._guide_jump)

        side = ttk.Frame(t)
        side.pack(side="left", fill="y", padx=8, pady=4)
        ttk.Button(side, text="Jump player to set",
                   command=self._guide_jump).pack(fill="x", pady=2)
        ttk.Button(side, text="Insert selected\nas markers",
                   command=lambda: self._guide_insert(False)).pack(fill="x", pady=2)
        ttk.Button(side, text="Insert ALL\nWWOZ Jazz Tent sets",
                   command=lambda: self._guide_insert(True)).pack(fill="x", pady=2)
        ttk.Label(side, text="Markers land at each set's\nscheduled clock time,\n"
                             "mapped into the open file.\nNudge them while listening;\n"
                             "the sets are cut again\non their own.", justify="left").pack(pady=8)
        if not self.guide_data:
            ttk.Label(t, text="jazzfest2026_sets.json not found").pack()
        elif days:                                   # open on the first day, not an empty list
            self.g_day.set(days[0])
            self._render_guide()

    def _guide_sets(self, selected_only=True):
        day = self.g_day.get()
        if not day:
            return []
        sets = self.guide_data.get(day, [])
        if selected_only:
            idx = [int(i) for i in self.guide.selection()]
            shown = self._guide_shown
            return [shown[i] for i in idx if i < len(shown)]
        return sets

    def _render_guide(self):
        self.guide.delete(*self.guide.get_children())
        day = self.g_day.get()
        stage = self.g_stage.get()
        shown = [s for s in self.guide_data.get(day, [])
                 if stage in ("All stages", s["stage"])]
        shown.sort(key=lambda s: (s["start"], s["stage"]))
        self._guide_shown = shown
        for i, s in enumerate(shown):
            who = s["artist"] + (f"  (interviewed by {s['interviewer']})" if s.get("interviewer") else "")
            self.guide.insert("", "end", iid=str(i),
                              values=(f"{s['start']}-{s['end']}", who, s["stage"]))

    def _set_offset(self, day, s):
        """Offset (s) of a set's scheduled start within the open file, or None."""
        if not self.current_file:
            return None
        fstart = wwoz.recording_start(self.current_file, self.duration)
        if fstart is None:
            return None
        hh, mm = map(int, s["start"].split(":"))
        y, mo, d = map(int, day.split("-"))
        sdt = datetime(y, mo, d, hh, mm, tzinfo=wwoz.TZ)
        off = (sdt - fstart).total_seconds()
        if off < 0 or (self.duration and off > self.duration):
            return None
        return int(off)

    def _guide_jump(self, _e=None):
        sel = self._guide_sets()
        if not sel:
            return
        off = self._set_offset(self.g_day.get(), sel[0])
        if off is None:
            messagebox.showinfo(APP, "That set's time isn't inside the open recording.\n"
                                     "(Open the recording from that day first.)")
            return
        self.player.seek(off)
        if not self.player.is_playing:
            self.toggle_play()

    def _guide_insert(self, all_jazz_tent):
        if not self.current_file:
            messagebox.showinfo(APP, "Open a recording first (Player tab).")
            return
        day = self.g_day.get()
        sets = ([s for s in self.guide_data.get(day, [])
                 if s["stage"] == "WWOZ Jazz Tent"] if all_jazz_tent
                else self._guide_sets())
        if not sets:
            messagebox.showinfo(APP, "Nothing selected.")
            return
        added = skipped = 0
        for s in sets:
            off = self._set_offset(day, s)
            if off is None:
                skipped += 1
                continue
            self.rows = [r for r in self.rows if r["offset_seconds"] != off]
            self.rows.append({"offset": fmt(off), "clock_time": f"{day} {s['start']}",
                              "artist": s["artist"], "song": "",
                              "release": "", "show": f"set @ {s['stage']}",
                              "offset_seconds": off})
            added += 1
        self.rows.sort(key=lambda r: r["offset_seconds"])
        self._render_rows()
        self.save_tracklist(quiet=True)
        self.logln(f"inserted {added} set marker(s)"
                   + (f", {skipped} outside this recording's window" if skipped else ""))

    def _build_archive_tab(self):
        t = self.tab_archive
        top = ttk.Frame(t)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="Refresh archive list", command=self.load_archive).pack(side="left")
        ttk.Button(top, text="Download selected", command=self.download_selected)\
            .pack(side="left", padx=6)
        self.rec_btn = ttk.Button(top, text="Record live stream…", command=self.toggle_record)
        self.rec_btn.pack(side="left", padx=20)
        self.rec_stop = None
        ttk.Button(top, text="Open downloads folder",
                   command=lambda: open_folder(archive_dir())).pack(side="left")
        ttk.Button(top, text="Recordings folder...", command=self.choose_recordings_dir).pack(side="left", padx=6)
        ttk.Label(t, text="Everything WWOZ keeps on demand (two weeks). Select one or more shows "
                          f"({MOD_NAME} or Shift to pick several) and click Download; a check mark means you "
                          "have it. Festival broadcasts are cut into sets automatically.",
                  style="Hint.TLabel").pack(anchor="w", padx=10, pady=(0, 4))

        cols = ("date", "time", "title")
        self.arch = ttk.Treeview(t, columns=cols, show="headings", selectmode="extended")
        for c, w, grow in [("date", 150, False), ("time", 70, False), ("title", 700, True)]:
            self.arch.heading(c, text=c.title(), anchor="w")
            self.arch.column(c, width=px(w), anchor="w", stretch=grow)
        self.arch.tag_configure("have", foreground=GREEN)
        self.arch.tag_configure("fest", foreground=PURPLE)
        self.arch.bind("<Double-Button-1>", lambda e: self.download_selected())
        ysb = ttk.Scrollbar(t, orient="vertical", command=self.arch.yview)
        self.arch.configure(yscrollcommand=ysb.set)
        self.arch.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=4)
        ysb.pack(side="left", fill="y", pady=4)
        self.archive_shows = []

    def _build_fav_tab(self):
        t = self.tab_fav
        ttk.Label(t, text="WEEKLY FAVORITES", style="Section.TLabel").pack(anchor="w", padx=10, pady=(8, 0))
        ttk.Label(t, text="WWOZ's whole weekly schedule, in Central time. Check the shows you love: every "
                          f"Sunday at 9 AM {'your Mac' if MAC else 'Windows'} downloads their new episodes. "
                          "Click a show's name to read "
                          "about it and what it plays. \"Download an episode\" grabs one right now.",
                  style="Hint.TLabel", justify="left", wraplength=px(1120)).pack(anchor="w", padx=10, pady=(0, 8))
        bf = ttk.Frame(t)
        bf.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Button(bf, text="Save favorites", command=self.save_favorites).pack(side="left")
        ttk.Button(bf, text="Download an episode…", command=self.download_latest_favorite)\
            .pack(side="left", padx=6)
        ttk.Button(bf, text="Run weekly download now", command=self.run_weekly).pack(side="left", padx=6)
        ttk.Button(bf, text="Add show by name…", command=self.add_favorite).pack(side="left", padx=(18, 0))
        ttk.Button(bf, text="Refresh schedule from wwoz.org", command=self.refresh_schedule)\
            .pack(side="left", padx=6)

        body = ttk.Frame(t)
        body.pack(fill="both", expand=True, padx=10)
        self.fav_canvas = tk.Canvas(body, highlightthickness=0, background=BG)
        ysb = ttk.Scrollbar(body, orient="vertical", command=self.fav_canvas.yview)
        self.fav_canvas.configure(yscrollcommand=ysb.set)
        self.fav_canvas.pack(side="left", fill="both", expand=True)
        ysb.pack(side="left", fill="y")
        self.fav_frame = ttk.Frame(self.fav_canvas)
        self.fav_win = self.fav_canvas.create_window((0, 0), window=self.fav_frame, anchor="nw")
        self.fav_frame.bind("<Configure>", lambda e: self.fav_canvas.configure(
            scrollregion=self.fav_canvas.bbox("all")))
        self.fav_canvas.bind("<Configure>", lambda e: self.fav_canvas.itemconfigure(
            self.fav_win, width=e.width))
        # wheel-scroll while the pointer is over the list (Windows sends the event to the
        # focused widget, so bind globally only while hovering)
        self.fav_canvas.bind("<Enter>", lambda e: self.fav_canvas.bind_all(
            "<MouseWheel>", lambda ev: self.fav_canvas.yview_scroll(wheel_steps(ev.delta), "units")))
        self.fav_canvas.bind("<Leave>", lambda e: self.fav_canvas.unbind_all("<MouseWheel>"))

        about = ttk.LabelFrame(t, text="About this show")
        about.pack(fill="x", padx=10, pady=6)
        self.fav_about = tk.Text(about, height=6, wrap="word", state="disabled")
        style_text(self.fav_about)
        self.fav_about.pack(fill="x", padx=4, pady=2)
        self.fav_rows = []          # dicts: kind, match, day, start, var, label, slot
        self.fav_selected = None
        self.load_favorites()

    # ----------------------------------------------------------------- log --

    def logln(self, msg):
        self.log_q.put(str(msg))

    def _drain_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(200, self._drain_log)

    def _bg(self, fn, done=None):
        """Run fn in a background thread, routing wwoz's prints to the log pane."""
        if self.busy:
            messagebox.showinfo(APP, "Another task is still running — check Activity.")
            return
        self.busy = True                 # set before the thread starts: a double-click can't start two

        def wrap():
            try:
                fn()
            except SystemExit as e:
                self.logln(f"stopped: {e}")
            except Exception as e:
                self.logln(f"ERROR: {e}  (details in {ERROR_LOG.name})")
                log_error()
            finally:
                self.busy = False
                if done:
                    self.after(0, done)
        threading.Thread(target=wrap, daemon=True).start()

    # ------------------------------------------------------------- library --

    # The library has four kinds of section: each festival's sets by day (Jazz Fest cut
    # from your own recordings, the others from WWOZ's broadcasts), the WWOZ shows
    # grouped by show with the newest episode first, and the full recordings the pieces
    # were cut from. _catalog decides where every file belongs.

    def _slot_at(self, when):
        """The weekly-schedule slot on the air at an aware datetime (None without a schedule)."""
        if self._slots is None:
            self._slots = (wwoz.cached_schedule() or {}).get("slots", [])
        day, hhmm = wwoz.WEEKDAYS[when.weekday()], when.strftime("%H:%M")
        return next((s for s in self._slots if s["day"] == day and wwoz._covers(s, hhmm)), None)

    def _index_festivals(self, arch: Path):
        """Which files belong to a festival WWOZ broadcast: the per-set cuts and the WWOZ
        shows listed in each festival folder's festival.json, and the broadcast downloads."""
        self._fest_sets, self._fest_casts, self._fest_progs = {}, {}, {}
        self._fests = []
        for fest in wwoz.load_broadcast_festivals():
            folder = wwoz.festival_folder(fest)
            try:
                idx = json.loads((folder / "festival.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                idx = {}
            sets = [s for s in idx.get("sets", []) if (folder / s["file"]).exists()]
            for s in sets:
                self._fest_sets[folder / s["file"]] = (fest, s)
            for pr in idx.get("programs", []):
                if (folder / pr["file"]).exists():
                    self._fest_progs[folder / pr["file"]] = (fest, pr)
            casts = [p for p in sorted(arch.glob("*.mp3")) if wwoz.festival_for(p, [fest])] if arch.exists() else []
            for p in casts:
                self._fest_casts[p] = fest
            if sets or casts:
                self._fests.append((fest, folder, sets, casts))
        self._fests.sort(key=lambda f: min(f[0]["days"]), reverse=True)    # newest festival first

    @staticmethod
    def _clock(dt, minutes=True):
        if minutes and dt.minute:
            return dt.strftime("%I:%M%p").lstrip("0").lower()
        return dt.strftime("%I%p").lstrip("0").lower()

    def _catalog(self, arch: Path):
        """Every playable file, sorted into what it is. self._items maps a path to an item:
        kind 'set', 'episode', 'between' or 'full'; name; clock (when it begins on the air,
        or None); section (the library group it goes in) and details for the header."""
        self._index_festivals(arch)
        items = {}
        fest_days = set(getattr(self, "guide_data", None) or {})
        jf_year = min(fest_days)[:4] if fest_days else ""

        # pieces cut from your own recordings
        jf_recordings = set()
        for folder in sorted(RECORDINGS_DIR.glob("* - tracks")):
            idx = wwoz.pieces_index(folder)
            if not idx:
                continue
            parent = folder.parent / idx.get("recording", "")
            for pc in idx.get("pieces", []):
                p = folder / pc["file"]
                if not p.exists():
                    continue
                clock = datetime.fromisoformat(pc["clock"]) if pc.get("clock") else None
                day = f"{clock:%Y-%m-%d}" if clock else ""
                base = {"name": pc["name"], "clock": clock, "label": pc.get("label", ""),
                        "stage": pc.get("stage", ""), "seconds": pc.get("seconds", 0), "recording": parent}
                if pc["kind"] == "set" and day in fest_days:
                    jf_recordings.add(parent)
                    items[p] = dict(base, kind="set", section="::fest", fest=f"Jazz Fest {jf_year}", day=day)
                elif pc["kind"] == "program":
                    items[p] = dict(base, kind="episode", section="::shows", source="recording")
                else:
                    items[p] = dict(base, kind="between", section=None,
                                    fest=f"Jazz Fest {jf_year}" if day in fest_days else "")
        # the same set caught by two overlapping recordings: keep the longer copy
        sets = sorted((p for p, it in items.items() if it["kind"] == "set"),
                      key=lambda p: (items[p]["day"], wwoz.loose(items[p]["name"]), -items[p]["seconds"]))
        for a, b in zip(sets, sets[1:]):
            ia, ib = items.get(a), items[b]
            if (ia and ia["day"] == ib["day"] and wwoz.loose(ia["name"]) == wwoz.loose(ib["name"])
                    and ia["clock"] and ib["clock"] and abs((ia["clock"] - ib["clock"]).total_seconds()) < 1800):
                items[b] = dict(ib, section=None, kind="between")     # still in Shows, not twice in the library

        # festivals WWOZ broadcast
        for p, (fest, s) in self._fest_sets.items():
            clock = datetime.strptime(f"{s['day']} {s['aired_from']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=wwoz.TZ)
            items[p] = {"kind": "set", "section": f"::bf:{fest['name']}", "fest": fest.get("short") or fest["name"],
                        "fest_full": fest, "name": s["artist"], "clock": clock, "day": s["day"],
                        "stage": s["stage"], "sched": (s["start"], s["end"]), "label": "schedule"}
        for p, (fest, pr) in self._fest_progs.items():
            items[p] = {"kind": "episode", "section": "::shows", "source": "festival", "fest_full": fest,
                        "name": pr["name"], "clock": datetime.fromisoformat(pr["clock"])}

        # archive downloads: festival broadcasts are full recordings, the rest episodes
        if arch.exists():
            for p in sorted(arch.glob("*.mp3")):
                start = wwoz.start_time_from_name(p)
                if p in self._fest_casts:
                    items[p] = {"kind": "full", "section": "::full", "sub": f"{self._fest_casts[p].get('short')} broadcasts",
                                "name": p.stem, "clock": start, "fest_full": self._fest_casts[p]}
                    continue
                slot = self._slot_at(start) if start else None
                title = wwoz.archive_title(p)
                if slot and (not title or wwoz.family_key(wwoz.show_family(title)[0])
                             == wwoz.family_key(wwoz.show_family(slot["title"])[0])):
                    title = slot["title"]                  # the schedule's spelling, with the host
                if not title:
                    m = re.match(r"\d{14}-\d+-\d+-(.+)", p.stem)
                    title = (m.group(1) if m else p.stem).replace("-", " ").title()
                items[p] = {"kind": "episode", "section": "::shows", "source": "download",
                            "name": title, "clock": start, "slot": slot}

        # the recordings themselves
        for p in sorted(RECORDINGS_DIR.iterdir()):
            if p.suffix.lower() not in AUDIO_EXT or not p.is_file():
                continue
            start = wwoz.start_time_from_name(p)
            items[p] = {"kind": "full", "section": "::full",
                        "sub": f"Jazz Fest {jf_year} days" if p in jf_recordings else "Other recordings",
                        "name": p.stem, "clock": start}

        # one name per show: the episodes' own spellings vary ("Hep Cat's Ball", "New
        # Orleans Music - Saturday"), so shows whose names reduce to the same letters,
        # or where one is the start of the other, are one show
        fams = {}
        for p, it in items.items():
            if it["kind"] == "episode":
                fam, host = wwoz.show_family(it["name"])
                it["family_name"], it["host"] = fam, host
                fams.setdefault(wwoz.family_key(fam), []).append(fam)
        keys = sorted(fams, key=len)
        alias = {}
        for i, k in enumerate(keys):
            alias[k] = next((alias.get(s, s) for s in keys[:i] if len(s) >= 10 and k.startswith(s)), k)
        names = {}
        for k, variants in fams.items():
            names.setdefault(alias[k], []).extend(variants)
        display = {k: max(set(v), key=lambda n: (v.count(n), len(n))) for k, v in names.items()}
        for it in items.values():
            if it["kind"] == "episode":
                it["family"] = display[alias[wwoz.family_key(it["family_name"])]]
        self._items = items
        return items

    def _describe(self, p: Path, arch: Path = None):
        """How a file is presented in the Player's header: title, subtitle, about."""
        it = self._items.get(p)
        clock = self._clock
        when = it.get("clock") if it else None
        if not it:
            st = wwoz.start_time_from_name(p)
            return {"title": p.stem, "subtitle": f"{st:%A, %B} {st.day}, {st.year}" if st else p.name,
                    "about": "", "when": ""}
        if it["kind"] == "set" and it.get("fest_full"):            # a festival WWOZ broadcast
            fest = it["fest_full"]
            begin = datetime.strptime(f"{it['day']} {it['sched'][0]}", "%Y-%m-%d %H:%M")
            end = datetime.strptime(f"{it['day']} {it['sched'][1]}", "%Y-%m-%d %H:%M")
            return {"title": it["name"],
                    "subtitle": f"{begin:%A, %B} {begin.day} on the {it['stage']}, {clock(begin)} to {clock(end)}",
                    "about": (f"{fest['name']}, from WWOZ's live broadcast"
                              + (f" at {fest['place']}" if fest.get("place") else "")
                              + ". Cut at the scheduled set change, so it can open with the end of the "
                                "previous act, a record, or the DJ's introduction.")}
        if it["kind"] == "set":                                     # Jazz Fest, from your recording
            how = self._how_named(it["label"])
            return {"title": it["name"],
                    "subtitle": (f"{when:%A, %B} {when.day}" + (f" at the {it['stage']}" if it["stage"] else "")
                                 + f", from {clock(when)}") if when else "",
                    "about": f"{it['fest']}, from your recording of WWOZ's broadcast. {how}"}
        if it["kind"] == "episode":
            # (a cut show starts a minute or so before its slot, so look a few minutes in)
            slot = it.get("slot") or (self._slot_at(when + timedelta(minutes=10)) if when else None)
            if slot and wwoz.family_key(wwoz.show_family(slot["title"])[0]) != wwoz.family_key(it["family_name"]):
                slot = None
            sub = f"{when:%A, %B} {when.day}, {when.year}, from {clock(when)}" if when else ""
            about = ""
            if slot:
                sub = (f"{when:%A, %B} {when.day}, {when.year}, "
                       f"{wwoz.fmt_clock(slot['start'])} to {wwoz.fmt_clock(slot['end'])}")
                bits = [", ".join(slot["hosts"]), ", ".join(slot["genres"])]
                about = ". ".join(b for b in bits if b)
                blurb = wwoz.show_blurb(slot, maxlen=170, with_artists=False)
                if blurb:
                    about = (about + ". " if about else "") + blurb
            if it["source"] == "recording":
                about = (about + " " if about else "") + "Cut from your own recording of that day."
            elif it["source"] == "festival":
                about = (about + " " if about else "") + f"Aired around WWOZ's broadcast of the {it['fest_full']['name']}."
            return {"title": it["name"], "subtitle": sub, "about": about.strip()}
        if it["kind"] == "between":
            return {"title": it["name"],
                    "subtitle": f"{when:%A, %B} {when.day}, from {clock(when)}" if when else "",
                    "about": "Talk and records between festival sets, cut from your recording."}
        # a full recording or broadcast
        if p in self._fest_casts:
            parts = wwoz.broadcast_parts(wwoz.archive_title(p))
            title = (parts["acts"] or wwoz.archive_title(p) or p.stem).replace(" (pt ", " (part ")
            return {"title": title,
                    "subtitle": f"{when:%A, %B} {when.day}, WWOZ's broadcast from {clock(when)}" if when else "",
                    "about": (f"The whole broadcast of the {it['fest_full']['name']} as WWOZ archived it. "
                              "Its sets are in the festival's section of the library, and the WWOZ "
                              "shows around them are with the other episodes of those shows.")}
        st = wwoz.recording_start(p, self.duration if self.current_file == p else None) if not when else when
        return {"title": self._full_title(p, it),
                "subtitle": f"{st:%A, %B} {st.day}, {st.year}, recorded from {clock(st)}" if st else p.name,
                "about": ("The whole recording. Its festival sets are in the Jazz Fest section and its WWOZ "
                          "shows with the other episodes of each show." if it["sub"].startswith("Jazz Fest")
                          else "")}

    @staticmethod
    def _how_named(label):
        """Say in words how Auto-detect named a set (the tracklist's show column)."""
        label = label or ""
        if "announced + scheduled" in label:
            return "The DJs named this set on the air, and it matches the schedule."
        if "announced" in label:
            return "The DJs named this set on the air."
        if "heard in" in label:
            return "The DJs named this set once on the air."
        if "schedule guess" in label:
            return "Nobody named this set on the air; the name comes from the schedule, so give it a listen."
        if "unlabeled" in label:
            return "Nobody named this performance on the air."
        if label.startswith("set @"):
            return "Placed at its scheduled time."
        if label == "manual marker":
            return "Named by you."
        return ""

    def _full_title(self, p, it):
        start = it.get("clock") or wwoz.start_time_from_name(p)
        if p in self._fest_casts:
            parts = wwoz.broadcast_parts(wwoz.archive_title(p))
            return (parts["acts"] or p.stem).replace(" (pt ", " (part ")
        if start and re.match(r"(vlc-record|wwoz-live|\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})", p.name):
            return f"{start:%A, %B} {start.day}, from {self._clock(start)}"
        return p.stem

    def _leaf(self, p, it):
        """The library line for a file: (text, when column)."""
        c = it.get("clock")
        if it["kind"] == "set":
            return it["name"], self._clock(c) if c else ""
        if it["kind"] == "episode":
            text = f"{c:%a %b} {c.day}" if c else it["name"]
            if it.get("host"):
                text += f", {it['host']}"
            return text, self._clock(c) if c else ""
        return self._full_title(p, it), (f"{c:%b} {c.day}" if c else "")

    def refresh_library(self):
        tree = self.lib_tree
        opened = set()

        def walk(node):
            for i in tree.get_children(node):
                if tree.item(i, "open"):
                    opened.add(i)
                walk(i)
        walk("")
        first_build = not tree.get_children("")
        tree.delete(*tree.get_children(""))
        self.lib_paths = {}
        flt = self.lib_filter.get().lower().strip()
        items = self._catalog(archive_dir())

        def hit(p, it):
            if not flt:
                return True
            hay = " ".join(str(x) for x in (it.get("name"), it.get("family"), it.get("host"), it.get("stage"),
                                             it.get("fest"), p.name, self._leaf(p, it)[0]))
            return flt in hay.lower()

        def node(parent, iid, text, value="", tags=(), default_open=False):
            is_open = bool(flt) or (default_open if (first_build or iid not in self._seen_groups) else iid in opened)
            self._seen_groups.add(iid)
            tree.insert(parent, "end", iid=iid, text=text, values=(value,), open=is_open, tags=tags)

        def leaf(parent, p, it, tags=()):
            text, when = self._leaf(p, it)
            tree.insert(parent, "end", iid=str(p), text=text, values=(when,),
                        tags=tags + (("playing",) if self.current_file == p else ()))
            self.lib_paths[str(p)] = p

        def festival(gid, title, sets, default_open):
            sets = [(p, it) for p, it in sets if hit(p, it)]
            if not sets:
                return
            node("", gid, f"{title.upper()}  ({len(sets)} sets)", tags=("group",), default_open=True)
            for day in sorted({it["day"] for _, it in sets}):
                d = datetime.strptime(day, "%Y-%m-%d")
                todays = sorted((x for x in sets if x[1]["day"] == day), key=lambda x: x[1]["clock"])
                node(gid, f"{gid}:{day}", f"{d:%A, %B} {d.day}",
                     f"{len(todays)} set" + ("s" if len(todays) != 1 else ""), ("day",), default_open)
                for p, it in todays:
                    leaf(f"{gid}:{day}", p, it, ("set",))

        # festivals: Jazz Fest first (your own recordings), then the broadcast ones, newest first
        jf = [(p, it) for p, it in items.items() if it["section"] == "::fest"]
        if jf:
            festival("::fest", jf[0][1]["fest"], jf, default_open=False)
        for fest, *_ in self._fests:
            gid = f"::bf:{fest['name']}"
            festival(gid, fest.get("short") or fest["name"],
                     [(p, it) for p, it in items.items() if it["section"] == gid], default_open=True)

        # the WWOZ shows, by show
        eps = [(p, it) for p, it in items.items() if it["section"] == "::shows" and hit(p, it)]
        if eps:
            node("", "::shows", f"WWOZ SHOWS  ({len(eps)})", tags=("group",), default_open=True)
            for fam in sorted({it["family"] for _, it in eps}, key=lambda f: re.sub(r"(?i)^the\s+", "", f).lower()):
                mine = sorted((x for x in eps if x[1]["family"] == fam),
                              key=lambda x: x[1]["clock"] or datetime.min.replace(tzinfo=wwoz.TZ), reverse=True)
                fid = f"::show:{wwoz.family_key(fam)}"
                node("::shows", fid, fam, str(len(mine)), ("day",))
                for p, it in mine:
                    leaf(fid, p, it)

        # the full recordings the pieces came from
        full = [(p, it) for p, it in items.items() if it["section"] == "::full" and hit(p, it)]
        if full:
            node("", "::full", f"FULL RECORDINGS  ({len(full)})", tags=("group",))
            subs = sorted({it["sub"] for _, it in full}, key=lambda s: (s == "Other recordings", s))
            for sub in subs:
                sid = f"::full:{sub}"
                mine = sorted((x for x in full if x[1]["sub"] == sub),
                              key=lambda x: (x[1]["clock"] or datetime.min.replace(tzinfo=wwoz.TZ), x[0].name))
                node("::full", sid, sub, str(len(mine)), ("day",))
                for p, it in mine:
                    leaf(sid, p, it)
                    tracks = p.parent / (p.stem + " - tracks")
                    if tracks.is_dir() and not wwoz.pieces_index(tracks):     # an old song-by-song split
                        for c in sorted(c for c in tracks.iterdir() if c.suffix.lower() in AUDIO_EXT):
                            tree.insert(str(p), "end", iid=str(c), text=re.sub(r"^\d+ - ", "", c.stem),
                                        values=("",), tags=("cut",))
                            self.lib_paths[str(c)] = c

        if not tree.get_children(""):                 # nothing yet (or nothing matches): say why
            lines = (["Nothing matches that search."] if flt else
                     ["No recordings yet.", "Click here to download shows", "from WWOZ's archive, or click",
                      "Listen live at the top."])
            for k, text in enumerate(lines):
                tree.insert("", "end", iid=f"::empty{k}", text=text, values=("",), tags=("hint",))

    def _lib_click(self, event):
        iid = self.lib_tree.identify_row(event.y)
        if not iid or "indicator" in self.lib_tree.identify_element(event.x, event.y):
            return                                   # the ▸ expander only opens/closes
        path = self.lib_paths.get(iid)
        if iid.startswith("::empty") and not self.lib_filter.get().strip():
            self.nb.select(self.tab_archive)         # the empty library's "click here"
            return
        if path is None:                             # a section header
            self.lib_tree.item(iid, open=not self.lib_tree.item(iid, "open"))
            return
        self.open_file(path)

    def open_selected(self):
        path = self.lib_paths.get(self.lib_tree.focus())
        if path:
            self.open_file(path)

    def _mark_playing(self, path):
        for iid in list(self.lib_tree.tag_has("playing")):
            self.lib_tree.item(iid, tags=[t for t in self.lib_tree.item(iid, "tags") if t != "playing"])
        iid = str(path)
        if self.lib_tree.exists(iid):
            self.lib_tree.item(iid, tags=list(self.lib_tree.item(iid, "tags")) + ["playing"])
            self.lib_tree.see(iid)

    # -------------------------------------------------------------- player --

    def _init_player(self):
        """Start the sound player: ffmpeg reads, miniaudio plays, in a helper process of
        its own (wwoz_player.py), so the app's own work can't make the sound skip."""
        try:
            self._player_errors = open(ERROR_LOG, "a", encoding="utf-8")   # the helper's own errors
        except OSError:
            self._player_errors = None
        try:
            self.player = wwoz_player.Player(wwoz.find_tool("ffmpeg"), wwoz.find_tool("ffprobe"),
                                             errors=self._player_errors)
            self.player.set_volume(self.vol.get() / 100)
        except Exception as e:
            self.player = None
            messagebox.showerror(APP, f"The sound player couldn't start, so recordings won't play.\n\n{e}")
            return
        self.after(1500, self._player_ready)

    def _player_ready(self, tries=0, said=""):
        """Say when the sound player is ready. If the speakers won't open yet (asleep,
        unplugged, busy), the player keeps trying on its own, so this only notes it and
        looks again; a box appears only when something needs doing (a missing package),
        or later if a recording won't play."""
        p = self.player
        if not p:
            return
        if p.backend:
            self.logln(f"sound player ready (ffmpeg and miniaudio, through {p.backend})")
            return
        if p.state == "error" and p.error and p.error != said:
            self.logln(f"sound player: {p.error}")
            said = p.error
            if "isn't installed" in p.error:
                messagebox.showerror(APP, f"Recordings can't play yet.\n\n{p.error}")
                return
        if tries < 60:                                   # keep looking for two minutes
            self.after(2000, lambda: self._player_ready(tries + 1, said))

    def open_file(self, path: Path, resume=False):
        """Play a recording from the top (or from the saved position when resume=True)
        and show its songs."""
        if self.live:
            self.live = False
            self.live_btn.configure(text="▶  Listen live", background=GOLD, foreground=IRON)
        self.current_file = path
        self.duration = 0
        try:
            self.duration = wwoz.ffprobe_duration(path)
        except (SystemExit, Exception):
            pass
        info = self._describe(path, archive_dir())
        self.title(f"{info['title']} - {APP}")
        self.now_lbl.config(text=info["title"])
        self.sub_lbl.config(text=info["subtitle"] + (f" · {fmt(self.duration)} long" if self.duration else ""))
        self.about_lbl.config(text=info["about"])
        (self.about_lbl.grid if info["about"] else self.about_lbl.grid_remove)()
        self._last_now = None
        self.load_tracklist()
        self._mark_playing(path)
        fstart = wwoz.recording_start(path, self.duration)
        if fstart and self.guide_data:
            day = fstart.strftime("%Y-%m-%d")
            if day in self.guide_data:
                self.g_day.set(day)
                self.g_stage.set("WWOZ Jazz Tent")
                self._render_guide()
                self.logln(f"Jazz Fest Guide loaded for {day} — see the Guide tab")
        saved = self.state.get(str(path), 0)
        if self.player:
            start = saved if resume and saved > 20 else 0
            self._play_error_said = ""
            self.player.open(path, start=start)
            self.play_btn.config(text="Pause")
            self.logln(f"playing {info['title']}")
            if start:
                self.logln(f"resumed at {fmt(saved)}")
        else:
            self.logln("the sound player isn't running, so this can't play (its song list still shows)")
        if saved > 20 and not resume:
            self.resume_btn.config(text=f"Resume where you left off ({fmt(saved)}) ›")
            self.resume_btn.grid()
        else:
            self.resume_btn.grid_remove()

    def _resume(self):
        if self.current_file:
            self.open_file(self.current_file, resume=True)

    def _check_playback(self):
        """Say once when what is playing stops with an error (called on every tick, so a
        live stream that can't be reached is reported when the player gives up on it)."""
        if not (self.player and (self.current_file or self.live)):
            return
        err = self.player.error
        if self.player.state == "error" and err and err != self._play_error_said:
            self._play_error_said = err
            what = "The live stream" if self.live else "This recording"
            self.logln(f"playback problem with {'the live stream' if self.live else self.current_file.name}: "
                       f"{err}")
            log_line(f"playback problem: {err}")
            if self.live:                            # the Listen live button can start it again
                self.live = False
                self.live_btn.configure(text="▶  Listen live", background=GOLD, foreground=IRON)
                self.time_lbl.config(text="0:00:00 / 0:00:00")
            messagebox.showerror(APP, f"{what} wouldn't play.\n\n{err[:1].upper()}{err[1:]}")

    def toggle_play(self):
        if not (self.player and (self.current_file or self.live)):
            return
        if self.player.is_playing:
            self.player.stop() if self.live else self.player.pause()   # a live stream can't pause
            self.play_btn.config(text="Play")
        else:
            if self.live and self.player.state == "stopped":
                self._play_error_said = ""
                self.player.open_stream(wwoz.STREAM_URL)               # start it again
            else:
                self.player.play()
            self.play_btn.config(text="Pause")

    def jump(self, delta):
        if self.player and self.current_file:
            t = self.player.position + delta
            if self.duration:
                t = min(t, self.duration - 1)
            self.player.seek(max(0, t))

    def _bind_keys(self):
        """Space plays or pauses; the arrows skip 30 s (5 min with Shift). Typing in a
        search box or the marker fields is left alone."""
        typing = (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, tk.Spinbox)

        def guard(fn):
            def handler(e):
                try:
                    w = self.focus_get()
                except (KeyError, tk.TclError):      # focus in a combobox's drop-down
                    return None
                if isinstance(w, typing) or isinstance(w, (ttk.Button, tk.Button)) and e.keysym == "space":
                    return None
                if isinstance(w, tk.Toplevel) and w is not self:
                    return None
                fn()
                return "break"
            return handler
        play = guard(self.toggle_play)
        self.bind_all("<space>", play)
        for tree in (self.lib_tree, self.tree):     # before the tree's own select/open/close keys
            tree.bind("<space>", play)
        for key, d in (("Left", -30), ("Right", 30), ("Shift-Left", -300), ("Shift-Right", 300)):
            h = guard(lambda d=d: self.jump(d))
            self.bind_all(f"<{key}>", h)
            for tree in (self.lib_tree, self.tree):
                tree.bind(f"<{key}>", h)

    def _seek_at(self, e):
        """Put the playhead where the bar was clicked (or dragged to)."""
        w = max(1, self.seek.winfo_width())
        self.pos_var.set(min(1000.0, max(0.0, e.x / w * 1000)))
        self.seeking = True
        self._seek_drag(None)
        return "break"

    def _seek_drag(self, _v):
        if self.seeking and self.duration:
            sec = self.pos_var.get() / 1000 * self.duration
            self.time_lbl.config(text=f"{fmt(sec)} / {fmt(self.duration)}")

    def _seek_release(self, _e):
        self.seeking = False
        if self.player and self.duration and self.current_file:
            self.player.seek(self.pos_var.get() / 1000 * self.duration)
        return "break"

    def _focused_row(self):
        """The tracklist row behind the song list's focused line (iid 'r<index>';
        two songs logged in the same minute share an offset, so the offset can't be the id)."""
        item = self.tree.focus()
        if not item or not item.startswith("r"):
            return None
        try:
            return self.rows[int(item[1:])]
        except (ValueError, IndexError):
            return None

    def _jump_to_row(self, _e=None):
        row = self._focused_row()
        if row is None or not (self.player and self.current_file):
            return
        self.player.seek(int(row["offset_seconds"]))
        if not self.player.is_playing:
            self.toggle_play()

    def _tick(self):
        if self.player and (self.current_file or self.live):
            self._check_playback()
            # keep the button honest when the player stops or reaches the end on its own
            want = "Pause" if self.player.is_playing else "Play"
            if self.play_btn.cget("text") != want:
                self.play_btn.config(text=want)
        if self.player and self.current_file and self.duration and not self.seeking:
            sec = max(0, self.player.position)
            self.pos_var.set(sec / self.duration * 1000)
            self.time_lbl.config(text=f"{fmt(sec)} / {fmt(self.duration)}")
            if int(sec) % 10 == 0 and sec > 30:
                self.state[str(self.current_file)] = int(sec)
                save_state(self.state)
            now_iid = None
            for i, r in enumerate(self.rows):
                if int(r["offset_seconds"]) <= sec:
                    now_iid = f"r{i}"
                else:
                    break
            for iid in list(self.tree.tag_has("now")):
                self.tree.item(iid, tags=[t for t in self.tree.item(iid, "tags")
                                          if t != "now"])
            if now_iid and self.tree.exists(now_iid):
                tags = [t for t in self.tree.item(now_iid, "tags") if t != "now"]
                self.tree.item(now_iid, tags=tags + ["now"])
                if now_iid != self._last_now:          # follow the show, once per song
                    self.tree.see(now_iid)
                    self._last_now = now_iid
        self._show_scan_status()
        self.after(400, self._tick)

    # ----------------------------------------------------------- tracklist --

    def tracklist_csv(self):
        return Path(str(self.current_file.with_suffix("")) + ".tracklist.csv")

    def load_tracklist(self):
        self.rows = []
        p = self.tracklist_csv() if self.current_file else None
        if p and p.exists():
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    r["offset_seconds"] = int(float(r.get("offset_seconds") or 0))
                    self.rows.append(r)
            self.rows.sort(key=lambda r: r["offset_seconds"])
        self._loaded_keys = {(r["offset_seconds"], r.get("artist", "")) for r in self.rows}
        self._render_rows()

    def _render_rows(self):
        self.tree.delete(*self.tree.get_children())
        self.songs_lbl.config(text=f"SONGS  ·  {len(self.rows)}" if self.rows else "SONGS")
        flt = self.filter_var.get().lower().strip()
        for i, r in enumerate(self.rows):
            if flt and flt not in (r.get("artist", "") + r.get("song", "")
                                   + r.get("show", "")).lower():
                continue
            show = r.get("show", "")
            tags = (("manual",) if show == "manual marker"
                    else ("set",) if show.startswith("set @")
                    else ("auto",) if show.startswith("autocut") else ())
            self.tree.insert("", "end", iid=f"r{i}",
                             values=(fmt(r["offset_seconds"]), r.get("artist", ""),
                                     r.get("song", ""), r.get("show", "")), tags=tags)

    def add_marker(self):
        if not self.current_file:
            return
        sec = int(self.player.position) if self.player else 0
        text = simpledialog.askstring(
            APP, f"What starts at {fmt(sec)}?\n\nType the artist for a set, or artist - song for a song.")
        if not text or not text.strip():
            return
        artist, _, song = (t.strip() for t in text.strip().partition(" - "))
        start = wwoz.recording_start(self.current_file, self.duration)
        clock = (start + timedelta(seconds=sec)).strftime("%Y-%m-%d %I:%M %p") if start else ""
        row = {"offset": fmt(sec), "clock_time": clock, "artist": artist,
               "song": song, "release": "", "show": "manual marker", "offset_seconds": sec}
        self.rows = [r for r in self.rows if r["offset_seconds"] != sec]
        self.rows.append(row)
        self.rows.sort(key=lambda r: r["offset_seconds"])
        self._render_rows()
        self.save_tracklist(quiet=True)
        self.logln(f"marked at {fmt(sec)}: {artist}" + (f" - {song}" if song else "")
                   + ("" if song else "  (its set will be cut on its own in a couple of minutes)"))

    def edit_marker(self):
        row = self._focused_row()
        if not row:
            return
        artist = simpledialog.askstring(APP, "Artist:", initialvalue=row.get("artist", ""))
        if artist is None:
            return
        song = simpledialog.askstring(APP, "Song:", initialvalue=row.get("song", ""))
        row["artist"], row["song"] = artist.strip(), (song or "").strip()
        if str(row.get("show", "")).startswith("autocut"):
            # your correction now: Auto-detect's next relabel replaces its own rows, not yours
            row["show"] = "manual marker"
        self._render_rows()
        self.save_tracklist(quiet=True)

    def delete_marker(self):
        row = self._focused_row()
        if not row:
            return
        self.rows = [r for r in self.rows if r is not row]
        self._render_rows()
        self.save_tracklist(quiet=True)

    def save_tracklist(self, quiet=False):
        if not self.current_file:
            return
        p = self.tracklist_csv()
        # the Sunday task (or a scan in another window) may have folded Shazam finds into
        # the file since it was loaded; keep them rather than write over them
        # (a find that was on disk at load and is gone from the list now was deleted on purpose)
        mine = {(int(r["offset_seconds"]), r.get("artist", "")) for r in self.rows} | self._loaded_keys
        extra = [r for r in wwoz.read_tracklist(self.current_file)
                 if r.get("show") == "shazam" and (int(r["offset_seconds"]), r.get("artist", "")) not in mine]
        if extra:
            self.rows = sorted(self.rows + extra, key=lambda r: int(r["offset_seconds"]))
            self._render_rows()
        wwoz.write_tracklist(self.current_file, self.rows)
        if not quiet:
            self.logln(f"saved {p.name} ({len(self.rows)} entries) + cue")
        self.refresh_library()

    # -------------------------------------------------------------- archive --

    def load_archive(self):
        def work():
            self.logln("fetching WWOZ 2-week archive list…")
            self.archive_shows = wwoz.fetch_archive_shows()
            wwoz.remember_titles(self.archive_shows, archive_dir())
            self.logln(f"{len(self.archive_shows)} shows available")

        def done():
            self.arch.delete(*self.arch.get_children())
            dest = archive_dir()
            for i, s in enumerate(self.archive_shows):
                have = (dest / s["filename"]).exists()
                d = datetime.strptime(s["date"], "%Y-%m-%d")
                live = "Live from" in s["title"] or "Live at" in s["title"]
                self.arch.insert("", "end", iid=str(i),
                                 values=(f"{d:%a %b} {d.day}", s["time_label"].lower(),
                                         s["title"] + ("   ✓" if have else "")),
                                 tags=("have",) if have else ("fest",) if live else ())
        self._bg(work, done=done)

    def choose_recordings_dir(self):
        """Show where recordings are kept and let the folder be changed."""
        new = filedialog.askdirectory(
            parent=self, initialdir=str(RECORDINGS_DIR), mustexist=False,
            title=f"Recordings are kept in {RECORDINGS_DIR}. Pick another folder, or Cancel to keep it")
        if not new or Path(new) == RECORDINGS_DIR:
            return
        wwoz.set_recordings_dir(Path(new))
        messagebox.showinfo(APP, f"From now on recordings go in\n{new}\n\nClose and reopen WWOZ Studio "
                                 "to switch to it. Files already in the old folder stay there; move them "
                                 "across yourself if you want them in the new one.")

    def download_selected(self):
        sel = self.arch.selection()
        if not sel:
            messagebox.showinfo(APP, "Select one or more shows first.")
            return
        shows = [self.archive_shows[int(i)] for i in sel]

        def work():
            dest = wwoz.download_dir_from_config(wwoz.load_config())
            self._cut_new_festivals([wwoz.download_show(s, dest) for s in shows])
        self._bg(work, done=lambda: (self.refresh_library(), self.refresh_shows(), self.load_archive()))

    def toggle_record(self):
        if self.rec_stop:  # currently recording -> stop
            self.rec_stop.set()
            self.rec_btn.config(text="Record live stream…")
            self.rec_stop = None
            return
        hrs = simpledialog.askfloat(
            APP, "Record WWOZ's live stream for how many hours?\n"
                 "(During Jazz Fest: the live broadcast runs ~11am-7pm.\n"
                 "You can stop early with the same button.)", initialvalue=8.0,
            minvalue=0.1, maxvalue=24)
        if not hrs:
            return
        stop, done = threading.Event(), threading.Event()
        self.rec_stop, self._rec_done = stop, done
        self.rec_btn.config(text="■ Stop recording")

        def work():
            try:
                wwoz.record_stream(hrs, RECORDINGS_DIR, stop_flag=stop)
            except Exception as e:
                self.logln(f"recording ERROR: {e}")
                self.after(0, lambda e=e: messagebox.showerror(APP, f"The live recording stopped.\n\n{e}"))
            finally:
                done.set()                       # the file is closed (see _on_close)
                if self.rec_stop is stop:        # not a newer recording started after Stop
                    self.rec_stop = None
                    self.after(0, lambda: self.rec_btn.config(text="Record live stream…"))
                self.after(0, self.refresh_library)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ favorites --

    def _schedule_slots(self):
        """The cached weekly schedule (wwoz_schedule.json); [] until it is fetched.
        Read straight from disk: re-reading wwoz.org takes minutes, so a week-old copy
        is refreshed in the background (_refresh_stale_schedule) instead."""
        return (wwoz.cached_schedule() or {}).get("slots", [])

    def _refresh_stale_schedule(self):
        data = wwoz.cached_schedule()
        if data and not wwoz.schedule_is_stale(data):
            return

        def work():
            try:
                wwoz.fetch_schedule(refresh=True)
            except Exception as e:
                self.logln(f"couldn't get the weekly schedule from wwoz.org ({e}); "
                           + ("the saved copy is still used" if data else
                              "it is tried again the next time WWOZ Studio opens"))
                return
            finally:
                self._schedule_loading = False
                self.after(0, lambda: self._update_on_air(again=False))
                if not data:                          # the Favorites page's note, either way
                    self.after(0, self.load_favorites)
            self.after(0, self._schedule_refreshed)
        self._schedule_loading = True
        self._update_on_air(again=False)
        if not data:
            self.load_favorites()                     # "loading" rather than "click Refresh"
        self.logln("getting WWOZ's weekly schedule for the first time (a couple of minutes)…" if not data else
                   "the weekly schedule is over a week old; refreshing it from wwoz.org in the background…")
        threading.Thread(target=work, daemon=True).start()

    def _schedule_refreshed(self):
        self._slots = None
        self._update_on_air(again=False)
        self.load_favorites()
        self.refresh_library()

    def _fav_row(self, parent, text, var, kind, match, day=None, start=None, slot=None):
        row = ttk.Frame(parent)
        row.pack(fill="x")
        entry = {"kind": kind, "match": match, "day": day, "start": start,
                 "var": var, "label": None, "slot": slot}
        ttk.Checkbutton(row, variable=var, command=lambda en=entry: self._fav_toggled(en))            .pack(side="left")
        lbl = tk.Label(row, text=text, anchor="w", cursor="hand2", padx=4, background=BG,
                       foreground=INK, font=FONT)
        lbl.pack(side="left", fill="x", expand=True)
        entry["label"] = lbl
        lbl.bind("<Button-1>", lambda e, en=entry: self._fav_select(en))
        self.fav_rows.append(entry)
        return entry

    def _fav_toggled(self, entry):
        """A checked box is a pick: select the row and save right away, so "Run weekly
        download now" and the Sunday task see it without a separate Save."""
        self._fav_select(entry)
        self.save_favorites()

    def load_favorites(self):
        for w in self.fav_frame.winfo_children():
            w.destroy()
        self.fav_rows, self.fav_selected = [], None
        cfg = wwoz.load_config()
        favs = cfg.get("favorites", [])
        slots = self._schedule_slots()
        if not slots:
            ttk.Label(self.fav_frame, text=(
                "Loading WWOZ's weekly schedule from wwoz.org. The first time takes a couple of "
                "minutes; the shows appear here when it's done." if self._schedule_loading else
                "No schedule yet. Click \"Refresh schedule from wwoz.org\" (it takes a couple of "
                "minutes).")).pack(anchor="w", pady=6)

        def enabled_for(slot):
            for f in favs:
                if not f.get("enabled"):
                    continue
                if f.get("day") == slot["day"] and f.get("start") == slot["start"]:
                    return True
                if not f.get("day") and wwoz.loose(f.get("match")) == wwoz.loose(slot["title"]):
                    return True
            return False

        day = None
        for s in slots:
            if s["day"] != day:
                day = s["day"]
                ttk.Label(self.fav_frame, text=day.upper(), style="Section.TLabel")\
                    .pack(anchor="w", pady=(10, 2))
            text = (f"{wwoz.fmt_clock(s['start']):>8} – {wwoz.fmt_clock(s['end']):<8}   {s['title']}"
                    + (f"   · {', '.join(s['genres'])}" if s["genres"] else "")
                    + ("   (airs, but not on the published schedule)" if s.get("from_archive_only") else ""))
            self._fav_row(self.fav_frame, text, tk.BooleanVar(value=enabled_for(s)), "slot",
                          s["title"], s["day"], s["start"], s)

        # favorites typed by name that are not one of the schedule's shows
        custom = [f for f in favs if not f.get("day")
                  and not any(wwoz.loose(f.get("match")) == wwoz.loose(s["title"]) for s in slots)]
        if custom:
            ttk.Label(self.fav_frame, text="ADDED BY NAME (matches part of a title)",
                      font=FONT_SM_B).pack(anchor="w", pady=(10, 2))
            for f in custom:
                self._fav_row(self.fav_frame, f"{'':>8}   {'':<8}   {f['match']}",
                              tk.BooleanVar(value=bool(f.get("enabled"))), "custom", f["match"])
        self.fav_canvas.yview_moveto(0)

    def _fav_select(self, entry):
        if self.fav_selected:
            self.fav_selected["label"].configure(background=BG)
        self.fav_selected = entry
        entry["label"].configure(background=SEL)
        s = entry.get("slot")
        if s:
            hosts = ", ".join(s["hosts"]) if s["hosts"] else ""
            text = (f"{s['title']}\n{s['day'].title()} {wwoz.fmt_clock(s['start'])} – "
                    f"{wwoz.fmt_clock(s['end'])}" + (f"   ·   host: {hosts}" if hosts else "")
                    + (f"   ·   {', '.join(s['genres'])}" if s["genres"] else "") + "\n\n"
                    + (wwoz.show_blurb(s, maxlen=700) or "(WWOZ has published nothing about this show.)")
                    + (f"\n\nShow page: {s['url']}" if s.get("url") else "")
                    + (f"\nLatest logged playlist: {s['recent_playlist']}" if s.get("recent_playlist") else ""))
        else:
            text = f"Added by name: downloads any archive show whose title contains \"{entry['match']}\"."
        self.fav_about.configure(state="normal")
        self.fav_about.delete("1.0", "end")
        self.fav_about.insert("1.0", text)
        self.fav_about.configure(state="disabled")

    def add_favorite(self):
        name = simpledialog.askstring(APP, "Part of the show title\n"
                                           "(as it appears in the Archive tab):")
        if name and name.strip():
            self._fav_row(self.fav_frame, f"{'':>8}   {'':<8}   {name.strip()}",
                          tk.BooleanVar(value=True), "custom", name.strip())
            self.fav_canvas.yview_moveto(1)

    def save_favorites(self):
        cfg = wwoz.load_config()
        favs = []
        for r in self.fav_rows:
            if r["kind"] == "slot":
                if r["var"].get():           # unchecked schedule shows are implied by the schedule
                    favs.append({"match": r["match"], "day": r["day"], "start": r["start"], "enabled": True})
            else:
                favs.append({"match": r["match"], "enabled": bool(r["var"].get())})
        cfg["favorites"] = favs
        wwoz.save_config(cfg)
        on = [f["match"] for f in favs if f["enabled"]]
        self.logln(f"favorites saved ({len(on)} enabled): " + (", ".join(on) if on else "(none)"))

    def _pick_episodes(self, title, hits):
        """Ask which of a show's archive episodes to download (newest pre-checked)."""
        dest = archive_dir()
        dlg = tk.Toplevel(self)
        dlg.title(APP)
        dlg.configure(background=BG)
        dlg.transient(self)
        ttk.Label(dlg, text=title, font=FONT_B).pack(anchor="w", padx=18, pady=(16, 0))
        ttk.Label(dlg, text="WWOZ keeps two weeks. Which episode(s) do you want?",
                  style="Hint.TLabel").pack(anchor="w", padx=18, pady=(2, 8))
        choices = []
        for i, s in enumerate(hits):                     # newest first
            have = (dest / s["filename"]).exists()
            d = datetime.strptime(s["date"], "%Y-%m-%d")
            note = "   · already downloaded" if have else ("   · newest" if i == 0 else "")
            var = tk.BooleanVar(value=(i == 0 and not have))
            ttk.Checkbutton(dlg, text=f"{d:%A, %B} {d.day} · {s['time_label']}{note}", variable=var
                            ).pack(anchor="w", padx=28, pady=2)
            choices.append((s, var))
        picked = []

        def ok():
            picked.extend(s for s, v in choices if v.get())
            dlg.destroy()
        bf = ttk.Frame(dlg)
        bf.pack(fill="x", padx=18, pady=16)
        ttk.Button(bf, text="Download", style="Play.TButton", command=ok).pack(side="left")
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="left", padx=10)
        dlg.grab_set()
        dlg.wait_window()
        return picked

    def download_latest_favorite(self):
        """Download episodes of the clicked show (else every checked show): the newest
        one, or a choice of dates when the archive holds more than one."""
        targets = [self.fav_selected] if self.fav_selected else [r for r in self.fav_rows if r["var"].get()]
        if not targets:
            messagebox.showinfo(APP, "Check a show (or click its name) first.")
            return
        found = {}

        def fetch():
            found["shows"] = wwoz.fetch_archive_shows()

        def choose():
            picks = []
            for entry in targets:
                fav = {"match": entry["match"], "day": entry["day"], "start": entry["start"]}
                hits = [s for s in found.get("shows", []) if wwoz.favorite_matches(fav, s)]
                if not hits:
                    self.logln(f"no episode of \"{entry['match']}\" in the 2-week archive")
                    continue
                picks += self._pick_episodes(entry["match"], hits) if len(hits) > 1 else hits
            if not picks:
                return

            def download():
                self._cut_new_festivals([wwoz.download_show(s, archive_dir()) for s in picks])
            self._bg(download, done=lambda: (self.refresh_library(), self.refresh_shows(), self.load_archive()))
        self._bg(fetch, done=choose)

    def refresh_schedule(self):
        if self._schedule_loading:
            messagebox.showinfo(APP, "The schedule is already being loaded from wwoz.org. "
                                     "The shows appear on this page when it's done.")
            return
        self._bg(lambda: wwoz.fetch_schedule(refresh=True), done=self._schedule_refreshed)

    def run_weekly(self):
        """Download new episodes of every checked show; the app's own scanner then
        fingerprints them (the CLI's scan is skipped here so the UI stays responsive)."""
        self.save_favorites()
        cfg = wwoz.load_config()
        dest = wwoz.download_dir_from_config(cfg)
        favs = [f for f in cfg.get("favorites", []) if f.get("enabled")]

        def work():
            if not favs:
                self.logln("No favorites checked yet.")
                return
            shows = wwoz.fetch_archive_shows()
            new = [s for s in shows if any(wwoz.favorite_matches(f, s) for f in favs)
                   and not (dest / s["filename"]).exists()]
            self.logln(f"{len(new)} new episode(s) of your favorites to download")
            got = []
            for s in new:
                try:
                    got.append(wwoz.download_show(s, dest))
                except Exception as e:
                    self.logln(f"    FAILED: {e}")
            self._cut_new_festivals(got)
        self._bg(work, done=lambda: (self.refresh_library(), self.refresh_shows(), self.load_favorites()))

    # ----------------------------------------------------------- background --
    # What the old tool buttons did now happens on its own, one job at a time:
    #   1. a recording with no song list gets one from the DJs' log (Spinitron);
    #   2. a new Jazz Fest day recording has its sets found (Auto-detect, 20-40 min);
    #   3. a recording whose list changed is cut again into sets and shows, two minutes
    #      after the last change, so a few quick fixes by hand make one cut;
    #   4. a download whose song list could not be fetched is tried again;
    #   5. a broadcast festival is cut again when its downloads change;
    #   6. downloads get their Shazam scan, only while nothing is playing.

    def _is_fest_day(self, p):
        """Does a recording cover at least 20 minutes of a Jazz Fest broadcast day? Only
        recordings whose name carries the time count (or ones already scanned), so a
        renamed file with a guessed start is never scanned by mistake."""
        if not self.guide_data:
            return False
        st = wwoz.start_time_from_name(p)
        if st is None:
            return Path(str(p.with_suffix("")) + ".autocut.json").exists()
        try:
            end = st + timedelta(seconds=wwoz.ffprobe_duration(p))
        except Exception:
            return False
        for d in self.guide_data:
            y, mo, dd = map(int, d.split("-"))
            a = max(st, datetime(y, mo, dd, 10, 45, tzinfo=wwoz.TZ))
            b = min(end, datetime(y, mo, dd, 19, 0, tzinfo=wwoz.TZ))
            if (b - a).total_seconds() >= 1200:
                return True
        return False

    def _next_job(self):
        """The next piece of background work: (what the status line says, function, key),
        or None. A key that failed once this session is not tried again until restart;
        a song list that couldn't be fetched (no internet, say) is tried again an hour
        later, not every few seconds."""
        now = time.time()

        def due(key):
            return key not in self._job_done and now - self._job_tried.get(key, 0) >= 3600
        for p in sorted(RECORDINGS_DIR.iterdir()):
            if p.suffix.lower() not in AUDIO_EXT or not p.is_file():
                continue
            if now - p.stat().st_mtime < 120:          # still being recorded
                continue
            name = self._describe(p)["title"]
            tl = wwoz.tracklist_csv(p)
            key = (str(p), "tracklist")
            if not tl.exists() and due(key):
                return (f"getting the DJs' song list for {name}", lambda p=p: wwoz.fetch_tracklist_or_mark(p), key)
            key = (str(p), "autocut")
            if (key not in self._job_done and p != self.current_file
                    and not Path(str(p.with_suffix("")) + ".autocut.json").exists() and self._is_fest_day(p)):
                return (f"finding the sets in {name} (20 to 40 minutes)", lambda p=p: self._run_autocut(p), key)
            key = (str(p), "programs")
            if key not in self._job_done and tl.exists() and wwoz.unmarked_shows(p):
                # songs from shows no marker starts: take the show changes from WWOZ's calendar
                return (f"marking where each WWOZ show begins in {name}",
                        lambda p=p: wwoz.add_program_markers(p), key)
            key = (str(p), "cut", tl.stat().st_mtime if tl.exists() else 0)
            playing_piece = self.current_file and self.current_file.parent.name == p.stem + " - tracks"
            if (key not in self._job_done and not playing_piece and wwoz.pieces_stale(p)
                    and now - tl.stat().st_mtime > 120):
                return (f"cutting {name} into its sets and shows", lambda p=p: wwoz.split_pieces(p), key)
        arch = archive_dir()
        for m in sorted(arch.glob("*.tracklist.failed")) if arch.exists() else []:
            p = Path(str(m)[:-len(".tracklist.failed")] + ".mp3")
            key = (str(p), "retry-tracklist")
            if due(key):
                return (f"trying the song list again for {self._describe(p)['title']}",
                        lambda p=p: wwoz.fetch_tracklist_or_mark(p), key)
        for fest, folder, sets, casts in self._fests:
            idx = folder / "festival.json"
            files = wwoz.festival_files(fest, arch)
            newest = max((max(f.stat().st_mtime, wwoz.tracklist_csv(f).stat().st_mtime
                              if wwoz.tracklist_csv(f).exists() else 0) for f in files), default=0)
            key = (fest["name"], "festival", newest)
            if files and key not in self._job_done and (not idx.exists() or newest > idx.stat().st_mtime + 2):
                return (f"cutting the {fest.get('short') or fest['name']} into sets",
                        lambda f=fest: (wwoz.mark_festival(f), wwoz.cut_festival(f)), key)
        if (now >= self.scan_resume_at and not (self.player and self.player.is_playing)
                and arch.exists()):
            for p in wwoz.pending_downloads(arch):
                key = (str(p), "shazam")
                if key in self._job_done:
                    continue
                if wwoz.scanner_active(arch):         # the Sunday task is scanning: take turns
                    self._other_scanner = True
                    return None
                self._other_scanner = False
                return (f"Shazam is listening to {self._describe(p)['title']} for songs the DJ didn't log",
                        lambda p=p: self._shazam(p), key)
        return None

    def _job_worker(self):
        idle = 0
        while True:
            time.sleep(3 if idle < 2 else 10)
            if self.busy or self._closing:
                continue
            try:
                job = self._next_job()
            except Exception:
                log_error()
                job = None
            if not job:
                idle += 1
                continue
            idle = 0
            label, fn, key = job
            self.job_text = label
            self.logln(f"on its own: {label}")
            try:
                fn()
            except wwoz.ShazamQuiet:
                self.scan_resume_at = time.time() + 1800
                self.logln("Shazam stopped answering (it does that after a burst of requests); "
                           "scans resume in 30 minutes")
            except Exception as e:
                self._job_done.add(key)
                self.logln(f"couldn't finish {label}: {e}  (details in {ERROR_LOG.name})")
                log_error()
            else:
                if key[1] in ("tracklist", "retry-tracklist"):
                    self._job_tried[key] = time.time() # once an hour at most
                elif key[1] in ("shazam", "programs"):
                    self._job_done.add(key)            # once a session is enough
            finally:
                self.job_text = ""
                self._job_proc = None
                self.scan_now = None
            self.after(0, self._after_job)

    def _after_job(self):
        self.refresh_library()
        self.refresh_shows()
        if self.current_file and not self.live:
            self.load_tracklist()                  # the open show gains new songs or set names

    def _run_autocut(self, p):
        """Auto-detect in its own process at low priority, so the app stays quick."""
        cmd = [sys.executable, str(SCRIPT_DIR / "wwoz.py"), "autocut", str(p)]
        if os.name == "nt":
            low = {"creationflags": wwoz.NOWIN | 0x4000}               # BELOW_NORMAL_PRIORITY_CLASS
        else:
            cmd, low = ["nice", "-n", "10"] + cmd, {}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", cwd=str(SCRIPT_DIR), **low)
        self._job_proc = proc
        for line in proc.stdout:
            if line.strip():
                self.logln(line.rstrip())
        if proc.wait():
            raise RuntimeError(f"set detection stopped early (code {proc.returncode})")

    def _shazam(self, p):
        self.scan_now = p
        added = wwoz.shazam_fill(p)
        if added:
            self.logln(f"Shazam: {added} song(s) added to {self._describe(p)['title']}")

    def _redo(self, p, what):
        """The library's right-click redo: fetch the song list again, or find the sets again."""
        if what == "tracklist":
            self._bg(lambda: wwoz.fetch_tracklist_or_mark(p), done=self._after_job)
        elif messagebox.askokcancel(APP, "Listen through this recording again for the DJs' announcements "
                                         "and name its sets? It runs in the background (a minute or two if "
                                         "it was done before, 20 to 40 minutes if not), and the sets are "
                                         "cut again afterwards."):
            self._bg(lambda: self._run_autocut(p), done=self._after_job)

    def _cut_new_festivals(self, downloaded):
        """After downloads: cut any festival that a downloaded show belongs to."""
        seen = {}
        for p in downloaded:
            f = wwoz.festival_for(p)
            if f:
                seen[f["name"]] = f
        for f in seen.values():
            wwoz.cut_festival(f)

    def _show_scan_status(self):
        if self.job_text:
            text = f"Working on its own: {self.job_text}"
        elif time.time() < self.scan_resume_at:
            text = (f"Shazam went quiet; its scans resume at "
                    f"{datetime.fromtimestamp(self.scan_resume_at):%I:%M %p}".replace(" 0", " "))
        elif self._other_scanner:
            text = "Waiting while the Sunday download scans with Shazam"
        else:
            text = "Song lists, sets and Shazam scans are all up to date"
        if self.scan_lbl.cget("text") != text:
            self.scan_lbl.config(text=text)

    # ---------------------------------------------------------------- close --

    def _on_close(self):
        if self.rec_stop and not messagebox.askyesno(
                APP, "WWOZ Studio is recording the live stream. Closing stops the recording; "
                     "what it has recorded so far is kept.\n\nClose anyway?"):
            return
        if self.busy and not messagebox.askyesno(
                APP, "Something is still running (see Activity), such as a download. Closing now "
                     "stops it partway; a download picks up where it left off next time.\n\n"
                     "Close anyway?"):
            return
        if self.rec_stop:                            # let the recording close its file
            self.rec_stop.set()
            self._rec_done.wait(timeout=5)
        proc = self._job_proc
        if proc and proc.poll() is None:
            if not messagebox.askyesno(
                    APP, f"The app is {self.job_text or 'finding sets'} in the background. Closing stops "
                         "it, and it starts over the next time the app is open.\n\nClose anyway?"):
                return
            proc.kill()
        self._closing = True
        self.state["_volume"] = int(self.vol.get())
        if self.scan_now:
            wwoz.shazam_lock(self.scan_now).unlink(missing_ok=True)
        if self.player and self.current_file:
            sec = self.player.position
            if sec > 30:
                self.state[str(self.current_file)] = int(sec)
        save_state(self.state)
        if self.player:
            self.player.close()                      # the sound helper ends with the app
        self.destroy()


def main():
    if sys.stderr is None or sys.stdout is None:
        # pythonw has no console: a library that prints (a model download's progress
        # bar, say) would crash on a None stream, so point both at the error log
        try:
            stream = open(ERROR_LOG, "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stdout or stream
            sys.stderr = sys.stderr or stream
        except OSError:
            pass
    if os.name == "nt":
        # draw at the screen's real resolution (see UI): without this, Windows stretches
        # the whole window on a screen set above 100%, and every letter comes out blurry
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    try:
        app = App()
    except Exception as e:
        cannot_start(e, ERROR_LOG)
    for problem in (wwoz.RECORDINGS_PROBLEM, wwoz.SETTINGS_PROBLEM):
        if problem:          # a missing recordings drive, a settings file that was started over
            app.after(800, lambda p=problem: messagebox.showwarning(APP, p, parent=app))
    app.report_callback_exception = lambda *exc: (log_error_from(exc), app.logln(
        f"ERROR: {exc[1]}  (details in {ERROR_LOG.name})"))
    if "--selftest" in sys.argv:
        app.after(6000, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
