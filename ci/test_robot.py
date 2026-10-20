"""Checks the schedule robot on GitHub's Linux machines (the Mac test workflow runs it):

    python ci/test_robot.py OUT

  - reads Festing in Place 2026's schedule picture from WWOZ's calendar and compares it
    with the list typed in from it (schedules/broadcast-festing-in-place-2026.json);
  - reads Jazz Fest 2026's official schedule PDF and compares it with the saved one;
  - runs the robot itself on a copy of the schedules folder, to see what it would change.
Each check is PASS or FAIL; results land in OUT/robot.json.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ci"))
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
for k, v in (("WWOZ_DATA", tempfile.mkdtemp()), ("WWOZ_RECORDINGS", tempfile.mkdtemp()), ("WWOZ_NO_REPORTS", "1"),
             ("WWOZ_NO_SCHEDULE", "1"), ("WWOZ_SCHEDULES_URL", "off")):
    os.environ.setdefault(k, v)
import wwoz  # noqa: E402
import find_schedules  # noqa: E402
import read_grid  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": str(detail)[:3000]})
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail)[:3000], flush=True)
    (OUT / "robot.json").write_text(json.dumps(results, indent=1))


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower().replace("’", "'"))


typed = json.loads((HERE / "schedules" / "broadcast-festing-in-place-2026.json").read_text(encoding="utf-8"))
days = {d: ("11:00", "19:00") for d in typed["days"]}
try:
    events = [e for e in wwoz.fetch_live_calendar() if "festing" in e["title"].lower()]
    pics = find_schedules.pictures(events[0]["url"]) if events else []
    if not pics:                      # once the festival is over the calendar no longer lists it
        pics = ["https://www.wwoz.org/file/302546/download?token=XW-T0C7r"]
    check("the robot finds Festing in Place's schedule picture", bool(pics), pics)
    data = wwoz.HTTP.get(pics[0], headers=wwoz.UA, timeout=120).content
    got = read_grid.read(data, days)
    if got is None:                   # say what it saw, for fixing
        img = read_grid._image(data)
        cols = read_grid._columns(img, 4)
        check("the picture is read", False, f"columns {cols}; boxes {[len(read_grid._boxes(img, a, b)) for a, b in cols]}")
    else:
        same, off, rows = 0, [], []
        for d, sets in typed["days"].items():
            for want, have in zip(sets, got.get(d, [])):
                a, b = norm(want["artist"] + want.get("from", "")), norm(have["artist"] + have.get("from", ""))
                same += a == b
                mins = abs(int(want["start"][:2]) * 60 + int(want["start"][3:]) - int(have["start"][:2]) * 60
                           - int(have["start"][3:]))
                if mins > 3:
                    off.append((d, want["artist"], want["start"], have["start"]))
                if a != b:
                    rows.append((want["artist"], want.get("from"), have["artist"], have.get("from")))
        total = sum(len(v) for v in typed["days"].values())
        count = {d: len(v) for d, v in got.items()}
        check("Festing in Place's picture: every day, as many sets as typed in",
              count == {d: len(v) for d, v in typed["days"].items()}, count)
        check("its names read the same as typed in (at least 57 of 60)", same >= total - 3,
              f"{same} of {total}; differing: {rows}")
        check("its start times within 3 minutes of the typed-in ones", not off, off)
        (OUT / "festing-read.json").write_text(json.dumps(got, indent=1, ensure_ascii=False))
except Exception:
    import traceback
    check("Festing in Place's picture", False, traceback.format_exc()[-1500:])

try:
    r = wwoz.HTTP.get("https://www.nojazzfest.com/downloads/2026-nojhf-8day-cubes.pdf", headers=wwoz.UA, timeout=120)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "cubes.pdf").write_bytes(r.content)
        p = subprocess.run([sys.executable, str(HERE / "parse_jazzfest_schedule.py"), str(Path(tmp) / "cubes.pdf"),
                            str(Path(tmp) / "sets.json")], capture_output=True, text=True, timeout=900)
        new = json.loads((Path(tmp) / "sets.json").read_text(encoding="utf-8")) if (Path(tmp) / "sets.json").exists() else {}
    old = json.loads((HERE / "schedules" / "jazzfest2026_sets.json").read_text(encoding="utf-8"))
    diff = [d for d in old if len(old[d]) != len(new.get(d, []))]
    check("Jazz Fest 2026's official PDF reads into the same days and sets as saved",
          r.status_code == 200 and sorted(new) == sorted(old) and not diff,
          f"{r.status_code} {len(r.content)} bytes; days {sorted(new)}; days whose set count differs {diff}; "
          f"{(p.stderr or '')[-300:]}")
except Exception:
    import traceback
    check("Jazz Fest's official PDF", False, traceback.format_exc()[-1500:])

try:
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(HERE / "schedules", Path(tmp) / "schedules")
        p = subprocess.run([sys.executable, str(HERE / "ci" / "find_schedules.py"), str(Path(tmp) / "schedules"),
                            "--dry-run"], capture_output=True, text=True, timeout=1800, cwd=tmp)
    check("the robot runs on WWOZ's calendar as it is today", p.returncode in (0, 3),
          f"exit {p.returncode}: {(p.stdout + p.stderr)[-1500:]}")
except Exception:
    import traceback
    check("the robot", False, traceback.format_exc()[-1500:])

sys.exit(1 if any(r["status"] == "FAIL" for r in results) else 0)
