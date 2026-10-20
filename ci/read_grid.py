"""Reads a festival schedule posted only as a picture: a grid with a column per day and a
box per set, top to bottom in airing order (WWOZ's "cubes", as for Festing in Place
2026). The schedule robot (find_schedules.py) uses it; it needs Tesseract (the free
text-recognition program: apt-get install tesseract-ocr), Pillow and pypdfium2.

    read(picture_bytes, {"2026-10-22": ("11:00", "19:00"), ...}) -> {day: [set]} or None

The boxes are found by their white insides on the coloured grid, each box's text read by
Tesseract, and the days matched by the column headings (else in order). Only the first
start (the broadcast's) and any start printed in a box ("Starts 5:50") are known; the
others are placed between them by where the boxes sit, as WWOZ draws them. None comes back
when the result doesn't hold together (too few sets, low confidence, odd names), so a
guess is never saved.

    python ci/read_grid.py PICTURE START END DAY [DAY ...]   prints what it reads
"""
import io
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

MONTHS = "january february march april may june july august september october november december".split()


def _image(data):
    from PIL import Image
    if data[:4] == b"%PDF":
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(data)[0]
        img = page.render(scale=1950 / page.get_width()).to_pil()
    else:
        img = Image.open(io.BytesIO(data))
        if img.width < 1500:                  # small pictures read badly
            img = img.resize((img.width * 1950 // img.width, img.height * 1950 // img.width), Image.LANCZOS)
    return img.convert("RGB")


def _white(p):
    return min(p) > 235


def _runs(values, keep):
    """(start, end) of each run of True in a sequence, at least `keep` long."""
    out, start = [], None
    for i, v in enumerate(list(values) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= keep:
                out.append((start, i))
            start = None
    return out


def _columns(img, want):
    """The day columns: [(left, right)], from the first row across the grid with `want` (or
    the most) wide white runs of about the same width."""
    px, (w, h) = img.load(), img.size
    best = []
    for y in range(int(h * 0.05), int(h * 0.5), 2):
        runs = _runs((_white(px[x, y]) for x in range(w)), int(w * 0.08))
        if len(runs) >= 2 and max(b - a for a, b in runs) < 1.3 * min(b - a for a, b in runs):
            if want and len(runs) == want:
                return runs
            if len(runs) > len(best):
                best = runs
    return best


def _boxes(img, left, right):
    """The boxes down a column, [(top, bottom)], from the white insides along both edges."""
    px, h = img.load(), img.height
    found = []
    for x in (left + 4, right - 5):
        runs = _runs((_white(px[x, y]) for y in range(h)), max(20, int(h * 0.012)))
        found.append([(a, b) for a, b in runs if a > h * 0.04])
    boxes = max(found, key=len)
    # the white page above and below the grid is no box: a box has the grid's coloured
    # gutter beside it, and the boxes are about evenly sized
    boxes = [(a, b) for a, b in boxes if not _white(px[max(0, left - 6), (a + b) // 2])]
    if len(boxes) > 3:
        typical = statistics.median(b - a for a, b in boxes)
        boxes = [(a, b) for a, b in boxes if 0.4 * typical <= b - a <= 2.5 * typical]
    return boxes


def _ocr(img, box):
    """Tesseract's words in a part of the picture: [(line key, text, confidence)]."""
    crop = img.crop(box)
    crop = crop.resize((crop.width * 2, crop.height * 2))
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "box.png"
        crop.save(p)
        r = subprocess.run(["tesseract", str(p), "stdout", "--psm", "6", "tsv"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    words = []
    for line in r.stdout.splitlines()[1:]:
        f = line.split("\t")
        if len(f) == 12 and f[0] == "5" and f[11].strip():
            words.append(((int(f[2]), int(f[3]), int(f[4])), f[11].strip(), float(f[10])))
    return words


def _lines(words):
    lines = {}
    for key, text, _ in words:
        lines.setdefault(key, []).append(text)
    return [" ".join(v) for _, v in sorted(lines.items())]


def _clock(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _hhmm(minutes):
    return f"{minutes // 60:02}:{minutes % 60:02}"


def read(data, days):
    img = _image(data)
    want = sorted(days)
    cols = _columns(img, len(want))
    if not cols:
        return None
    # which day each column is, by its heading ("Thursday October 22"), else in order
    order = []
    for left, right in cols:
        boxes = _boxes(img, left, right)
        top = boxes[0][0] if boxes else int(img.height * 0.2)
        head = " ".join(_lines(_ocr(img, (left, max(0, top - int(img.height * 0.07)), right, max(1, top - 2)))))
        m = re.search(r"(" + "|".join(MONTHS) + r")\s*(\d{1,2})", head, re.I)
        order.append((m and f"{MONTHS.index(m.group(1).lower()) + 1:02}-{int(m.group(2)):02}", boxes))
    found, confs = {}, []
    for k, (mmdd, boxes) in enumerate(order):
        day = next((d for d in want if mmdd and d[5:] == mmdd), want[k] if k < len(want) else None)
        if not day or len(boxes) < 5:
            continue
        start, end = map(_clock, days[day])
        sets = []
        for top, bottom in boxes:
            words = _ocr(img, (cols[k][0] + 6, top + 3, cols[k][1] - 6, bottom - 3))
            confs += [c for _, _, c in words if c >= 0]
            text = re.sub(r"\s+", " ", " ".join(_lines(words))).strip()
            at = re.search(r"\bstarts?\s*(?:at\s*)?(\d{1,2})[:.](\d{2})\s*(am|pm)?", text, re.I)
            explicit = None
            if at:
                h, mi = int(at.group(1)), int(at.group(2))
                h += 12 if (at.group(3) or "").lower() == "pm" or (not at.group(3) and h < 9) else 0
                explicit = h * 60 + mi
                text = (text[:at.start()] + text[at.end():]).strip()
            text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
            par = re.search(r"\s*\(([^()]*)\)\s*$", text)
            artist, src = (text[:par.start()], par.group(1).strip()) if par else (text, "")
            sets.append({"y": top, "explicit": explicit, "artist": artist.strip(" -,"), "from": src})
        # starts: the broadcast's start at the first box, any printed start, and between them
        # by position (a printed start in the last box, a footer, follows the box before it
        # one spacing on)
        ys = [s["y"] for s in sets]
        step = statistics.median(b - a for a, b in zip(ys, ys[1:]))
        anchors = [(ys[0], start)]
        for i, s in enumerate(sets[1:], 1):
            if s["explicit"]:
                anchors.append((ys[i - 1] + step if i == len(sets) - 1 else ys[i], s["explicit"]))
        if len(anchors) == 1:
            anchors.append((ys[-1] + step, end))
        for i, s in enumerate(sets):
            if s["explicit"]:
                s["start"] = s["explicit"]
                continue
            y = ys[i]
            (y0, t0), (y1, t1) = next(((a, b) for a, b in zip(anchors, anchors[1:]) if y <= b[0]),
                                      (anchors[-2], anchors[-1]))
            s["start"] = round(t0 + (y - y0) * (t1 - t0) / (y1 - y0)) if y1 != y0 else t0
        out = []
        for i, s in enumerate(sets):
            stop = sets[i + 1]["start"] if i + 1 < len(sets) else end
            out.append({"start": _hhmm(s["start"]), "end": _hhmm(stop), "artist": s["artist"],
                        **({"from": s["from"]} if s["from"] else {})})
        found[day] = out
    # does it hold together?
    if set(found) != set(want) or not confs or statistics.mean(confs) < 75:
        return None
    for sets in found.values():
        starts = [_clock(s["start"]) for s in sets]
        if not (4 <= len(sets) <= 40 and starts == sorted(set(starts))
                and all(len(re.findall(r"[A-Za-z]{2,}", s["artist"])) >= 1 and len(s["artist"]) <= 120 for s in sets)):
            return None
    return found


if __name__ == "__main__":
    pic, a, b, *ds = sys.argv[1:]
    got = read(Path(pic).read_bytes(), {d: (a, b) for d in ds})
    if got is None:
        sys.exit("couldn't read it reliably")
    for d, sets in got.items():
        print(d)
        for s in sets:
            print(f"  {s['start']}-{s['end']}  {s['artist']}" + (f"  ({s['from']})" if s.get("from") else ""))
