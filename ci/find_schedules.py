"""The schedule robot: finds the festivals' schedules and keeps the program's schedules
folder up to date. GitHub runs it every day on the public page
(.github/workflows/festival-schedules.yml) and commits what changed; every copy of
WWOZ Studio then fetches the folder (wwoz.refresh_shared_schedules), so nobody has to
download the program again for a new festival.

    python ci/find_schedules.py SCHEDULES_FOLDER [--dry-run]

It does three things:
  - reads WWOZ's Live Event Broadcasts calendar, and for each festival day on it the set
    list in that day's entry; when an entry has only a picture of the schedule (a PDF or
    an image, as Festing in Place 2026 had), it reads the picture (read_grid.py). Each
    festival's days go into broadcast-<festival>.json, beside the days already there: a
    day's list from the entry's text replaces what was saved, a list read from a picture
    never replaces one (a typed-in list is better than a read one);
  - fetches Jazz Fest's official schedule PDF from nojazzfest.com once it is posted for
    the coming festival, and saves it as jazzfest<year>_sets.json until the festival
    begins (parse_jazzfest_schedule.py);
  - when a picture can't be read reliably, writes nothing for it, lists it in
    needs-typing.txt in the current folder and exits with code 3, so the workflow opens an
    issue for someone to type it in.
Exit codes: 0 done (changed or not), 3 a picture needs typing, 1 something failed.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("WWOZ_DATA", tempfile.mkdtemp(prefix="wwoz-robot-data-"))
os.environ.setdefault("WWOZ_RECORDINGS", tempfile.mkdtemp(prefix="wwoz-robot-rec-"))
os.environ.setdefault("WWOZ_NO_REPORTS", "1")
os.environ.setdefault("WWOZ_NO_SCHEDULE", "1")
os.environ.setdefault("WWOZ_SCHEDULES_URL", "off")
import wwoz  # noqa: E402

JAZZ_FEST_PDF = os.environ.get("WWOZ_JAZZFEST_PDF_URL") or "https://www.nojazzfest.com/downloads/{year}-nojhf-8day-cubes.pdf"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", wwoz.unicodedata.normalize("NFKD", text).encode("ascii", "ignore")
                  .decode().lower()).strip("-")


def festival_name(title, day):
    return title if day[:4] in title else f"{title} {day[:4]}"


def pictures(page_url):
    """The schedule pictures a calendar entry links: its PDF attachments first, then
    images whose name says schedule ('cubes', 'schedule', 'lineup'), as absolute URLs."""
    r = wwoz.HTTP.get(page_url, headers=wwoz.UA, timeout=60)
    r.raise_for_status()
    pdfs = re.findall(r'href="([^"]+/download[^"]*)"[^>]*type="application/pdf', r.text)
    pdfs += re.findall(r'href="([^"]+\.pdf)"', r.text)
    imgs = [u for u in re.findall(r'src="([^"]+\.(?:png|jpe?g))', r.text, re.I)
            if re.search(r"cube|schedule|lineup|grid", u, re.I)]
    seen, found = set(), []
    for u in pdfs + imgs:
        u = wwoz.urljoin(page_url, wwoz.html.unescape(u))
        if u not in seen:
            seen.add(u)
            found.append(u)
    return found


def read_picture(url, days):
    """A festival's days read from its schedule picture, or None (see read_grid.py)."""
    import read_grid
    r = wwoz.HTTP.get(url, headers=wwoz.UA, timeout=120)
    r.raise_for_status()
    return read_grid.read(r.content, days)


def save(folder, title, source, days, from_picture, dry):
    """Merge a festival's days into broadcast-<festival>.json. Returns True if changed."""
    first = min(days)
    name = festival_name(title, first)
    path = Path(folder) / f"broadcast-{slug(name)}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"_help": ["What WWOZ broadcasts on each day of a festival, set by set, from WWOZ's Live Event "
                         "Broadcasts calendar, kept by the schedule robot (ci/find_schedules.py). Times are "
                         "Central, 24-hour."], "title": title, "source": source, "days": {}}
    before = json.dumps(doc, sort_keys=True)
    for day, sets in days.items():
        if from_picture and doc["days"].get(day):
            continue                                   # never over a list typed in or read as text
        doc["days"][day] = [{k: v for k, v in s.items() if v not in ("", None)} for s in sets]
    doc["days"] = dict(sorted(doc["days"].items()))
    if json.dumps(doc, sort_keys=True) == before:
        return False
    print(f"  {path.name}: {', '.join(sorted(days))} ({'from the picture' if from_picture else 'from the text'})")
    if not dry:
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return True


def wwoz_festivals(folder, dry):
    changed, needs = False, []
    events = [e for e in wwoz.fetch_live_calendar() if wwoz.is_festival_broadcast(e)]
    print(f"{len(events)} festival day(s) on WWOZ's calendar")
    by_festival = {}
    for e in events:
        by_festival.setdefault((e["title"], f"{e['start']:%Y}"), []).append(e)
    for (title, _), evs in sorted(by_festival.items()):
        texts, missing = {}, []
        for e in evs:
            day = f"{e['start']:%Y-%m-%d}"
            sets = wwoz.fetch_set_list(e)
            if sets:
                texts[day] = sets
            else:
                missing.append(e)
        if texts:
            changed |= save(folder, title, "WWOZ's Live Event Broadcasts calendar, each day's entry", texts, False, dry)
        if not missing:
            continue
        days = {f"{e['start']:%Y-%m-%d}": (f"{e['start']:%H:%M}", f"{e['end']:%H:%M}") for e in missing}
        name = festival_name(title, min(days))
        typed = Path(folder) / f"broadcast-{slug(name)}.json"
        if typed.exists() and all(d in json.loads(typed.read_text(encoding="utf-8"))["days"] for d in days):
            continue                                   # already typed in or read before
        read = None
        for url in pictures(missing[0]["url"]):
            try:
                read = read_picture(url, days)
            except Exception as ex:
                print(f"  couldn't read {url}: {ex}")
            if read:
                changed |= save(folder, title, f"WWOZ's schedule picture, {url}, read by the schedule robot",
                                read, True, dry)
                break
        if not read:
            needs.append(f"{name}: {missing[0]['url']}")
            print(f"  {name}: the schedule is only a picture the robot couldn't read reliably")
    return changed, needs


def jazz_fest(folder, dry):
    """Jazz Fest's official schedule for the coming festival, once posted (until it begins:
    the festival revises the PDF, and after it the saved one is kept as it was)."""
    now = datetime.now(wwoz.TZ)
    year = now.year if now.month < 5 or (now.month == 5 and now.day < 5) else now.year + 1
    out = Path(folder) / f"jazzfest{year}_sets.json"
    if out.exists() and now >= datetime(year, 4, 15, tzinfo=wwoz.TZ):
        return False                                   # the festival is near: keep what was saved
    r = wwoz.HTTP.get(JAZZ_FEST_PDF.format(year=year), headers=wwoz.UA, timeout=120)
    if r.status_code == 404 or not r.content.startswith(b"%PDF"):
        print(f"Jazz Fest {year}'s schedule isn't posted yet")
        return False
    r.raise_for_status()
    with tempfile.TemporaryDirectory() as tmp:
        pdf, made = Path(tmp) / "cubes.pdf", Path(tmp) / "sets.json"
        pdf.write_bytes(r.content)
        p = subprocess.run([sys.executable, str(HERE / "parse_jazzfest_schedule.py"), str(pdf), str(made)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        if p.returncode or not made.exists():
            raise RuntimeError(f"Jazz Fest {year}'s schedule PDF couldn't be read: {(p.stderr or p.stdout)[-400:]}")
        new = json.loads(made.read_text(encoding="utf-8"))
    if not new or not all(d.startswith(str(year)) for d in new):
        raise RuntimeError(f"Jazz Fest {year}'s schedule PDF gave days {sorted(new)[:3]}")
    old = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    if old == new:
        return False
    print(f"  {out.name}: Jazz Fest {year}'s official schedule, {len(new)} days, "
          f"{sum(len(v) for v in new.values())} sets")
    if not dry:
        out.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return True


def main():
    folder = Path(sys.argv[1])
    dry = "--dry-run" in sys.argv
    folder.mkdir(parents=True, exist_ok=True)
    changed, needs = wwoz_festivals(folder, dry)
    try:
        changed |= jazz_fest(folder, dry)
    except Exception as ex:
        print(f"Jazz Fest's schedule: {ex}")
    # GitHub stops running a daily job on a page with no changes for 60 days: note the
    # last look once a month, which counts as a change
    stamp = folder / "checked.txt"
    try:
        last = datetime.fromisoformat(stamp.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        last = None
    if not changed and (last is None or datetime.now(wwoz.TZ) - last > timedelta(days=30)) and not dry:
        stamp.write_text(f"{datetime.now(wwoz.TZ).isoformat(timespec='seconds')} the schedule robot last looked "
                         "at WWOZ's calendar (written monthly)\n", encoding="utf-8", newline="\n")
    print("changed" if changed else "nothing new")
    if needs:
        Path("needs-typing.txt").write_text("\n".join(needs) + "\n", encoding="utf-8")
        sys.exit(3)


if __name__ == "__main__":
    main()
