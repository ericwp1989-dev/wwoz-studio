"""Turn the festival's official day-by-day schedule PDF ("cubes") into the
jazzfest<YEAR>_sets.json the toolkit reads.

    py -3.12 parse_jazzfest_schedule.py 2027-nojhf-8day-cubes.pdf

(from https://www.nojazzfest.com/downloads/ — fetch it again after the festival:
it is revised when days are cut short, as Apr 30 and May 1 2026 were for weather.)

Every set is a white, stroked rectangle and every stage header a black one across
the top; text is read only from inside each box, so a neighbouring column can't
bleed into a name (the first 2026 parse produced 'by Big Chief Hollywood & s Dat
Mighty'). Needs pdfplumber: py -3.12 -m pip install pdfplumber
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pdfplumber

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if len(sys.argv) < 2:
    sys.exit(__doc__)
PDF = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else None   # default: jazzfest<year>_sets.json beside this script

STAGE_NAMES = {   # header text -> name used in the toolkit (matches the old file)
    "festival stage": "Festival Stage",
    "shell gentilly stage": "Shell Gentilly Stage",
    "congo square stage": "Congo Square Stage",
    "wwoz jazz tent": "WWOZ Jazz Tent",
    "blues tent": "Blues Tent",
    "economy hall tent": "Economy Hall Tent",
    "sheraton new orleans fais do-do stage": "Fais Do-Do Stage",
    "jazz & heritage stage": "Jazz & Heritage Stage",
    "gospel tent presented by morris bart": "Gospel Tent",
    "lagniappe stage": "Lagniappe Stage",
    "sandals resorts jamaica cultural exchange pavilion": "Jamaica Cultural Exchange Pavilion",
    "rhythmpourium tent": "Rhythmpourium Tent",
    "ochsner children's tent": "Ochsner Children's Tent",
    "allison miner music heritage stage": "Allison Miner Music Heritage Stage",
}

TIME = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?", re.I)


def hhmm(h, m, ampm):
    h, m = int(h), int(m)
    ampm = (ampm or "").lower().replace(".", "")
    if ampm == "pm" and h != 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    elif not ampm and h < 9:        # festival hours: a bare 1:30 is the afternoon
        h += 12
    return f"{h:02}:{m:02}"


def norm_quotes(s):
    return (s.replace("’", "'").replace("‘", "'")
             .replace("“", '"').replace("”", '"'))


def join_lines(lines):
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and not out.endswith(" -"):
            out += ln                      # hyphen at a line break: "Downs-" + "Dorsey"
        else:
            out += (" " if out else "") + ln
    return re.sub(r"\s+", " ", out).strip()


def parse_box(page, r):
    crop = page.within_bbox((r["x0"] - 0.5, r["top"] - 0.5, r["x1"] + 0.5, r["bottom"] + 0.5))
    text = crop.extract_text() or ""
    lines = [norm_quotes(l).strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None
    times = []
    rest = []
    for ln in lines:
        # "3:35 - 3:50 pm DJ Raj Smoove" / "11:15 - 11:45 am New Orleans Council ..."
        m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)?\s*[-–]\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*(.*)$", ln, re.I)
        if m:
            a1 = m.group(3) or m.group(6)
            if not m.group(3) and int(m.group(1)) == 11 and int(m.group(4)) == 12:
                a1 = "am"
            times = [hhmm(m.group(1), m.group(2), a1), hhmm(m.group(4), m.group(5), m.group(6))]
            if m.group(7):
                rest.append(m.group(7))
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(am|pm)?", ln, re.I)
        if m:
            times.append(hhmm(*m.groups()))
            continue
        rest.append(ln)
    if not times:
        return {"raw": lines}
    interviewer = None
    for i, ln in enumerate(rest):
        if ln.lower().startswith("interviewer"):
            interviewer = join_lines([ln[len("interviewer"):].lstrip(" :")] + rest[i + 1:])
            rest = rest[:i]
            break
    artist = join_lines(rest)
    return {"start": times[0], "end": times[-1] if len(times) > 1 else None,
            "artist": artist, "interviewer": interviewer}


def main():
    pdf = pdfplumber.open(PDF)
    out = {}
    for page in pdf.pages:
        title = (page.extract_text() or "").split("\n")[0]
        day = datetime.strptime(re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", title).group(1), "%B %d, %Y").strftime("%Y-%m-%d")
        rects = []
        seen = set()
        for r in page.rects:
            key = (round(r["x0"]), round(r["top"]), round(r["x1"]), round(r["bottom"]))
            if key in seen:
                continue
            seen.add(key)
            rects.append(r)
        headers = [r for r in rects if r["top"] < 120 and r.get("non_stroking_color") in ((0.0, 0.0, 0.0), (0, 0, 0), [0, 0, 0])
                   and 40 < r["x1"] - r["x0"] < 70]
        stages = []
        for h in sorted(headers, key=lambda r: r["x0"]):
            t = page.within_bbox((h["x0"], h["top"], h["x1"], h["bottom"])).extract_text() or ""
            name = re.sub(r"\s+", " ", norm_quotes(t)).strip().lower()
            stages.append((h, STAGE_NAMES.get(name, name)))
        boxes = [r for r in rects if r["top"] > 110 and r.get("stroke") and 40 < r["x1"] - r["x0"] < 70
                 and r.get("non_stroking_color") in ((1.0, 1.0, 1.0), (1, 1, 1), [1, 1, 1])]
        sets = []
        for b in boxes:
            cx = (b["x0"] + b["x1"]) / 2
            stage = min(stages, key=lambda s: abs((s[0]["x0"] + s[0]["x1"]) / 2 - cx))[1]
            p = parse_box(page, b)
            if not p or "raw" in p:
                print(f"  ?? {day} {stage}: {p}")
                continue
            entry = {"start": p["start"], "end": p["end"], "artist": p["artist"].rstrip("*").strip(),
                     "stage": stage}
            if p["interviewer"]:
                entry["interviewer"] = p["interviewer"]
            sets.append(entry)
        sets.sort(key=lambda s: (s["stage"], s["start"]))
        # boxes printed with a start time only (Allison Miner interviews, a late
        # addition or two): they run until the stage's next set, at most an hour
        for i, s in enumerate(sets):
            if s["end"]:
                continue
            h, m = map(int, s["start"].split(":"))
            cap = h * 60 + m + 60
            nxt = [t for t in sets[i + 1:] if t["stage"] == s["stage"]]
            if nxt:
                h2, m2 = map(int, nxt[0]["start"].split(":"))
                cap = min(cap, h2 * 60 + m2 - 15)
            s["end"] = f"{cap // 60:02}:{cap % 60:02}"
            s["end_estimated"] = True
        out[day] = sets
        print(f"{day}: {len(stages)} stages, {len(sets)} sets")
    dest = Path(OUT) if OUT else Path(__file__).resolve().parent / f"jazzfest{min(out)[:4]}_sets.json"
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"wrote {dest}")


main()
