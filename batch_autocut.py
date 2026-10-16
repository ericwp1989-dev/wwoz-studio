"""Run autocut over every festival-day recording, one at a time, then (with
--split) split each into per-performance files.

Safe to re-run: a recording already scanned reuses its cached talk regions and
transcripts, so it is only relabeled (seconds, plus a minute or two of Shazam
checks); a new recording takes 20-40 minutes.

    py -3.12 batch_autocut.py            # (re)label every fest day
    py -3.12 batch_autocut.py --split    # … and re-split them
"""
import argparse
import sys
import traceback
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wwoz
import wwoz_autocut

AUDIO = {".mp3", ".mkv", ".m4a", ".ogg", ".opus", ".wav", ".flac"}


def fest_recordings():
    """Recordings in the recordings folder that cover part of a festival broadcast
    (10:45 AM - 7 PM on a festival day) for at least 20 minutes: by the time in the
    file name, or (no time in the name) because they were scanned before."""
    days = wwoz_autocut.load_guide()
    found = []
    for p in sorted(wwoz.RECORDINGS_DIR.iterdir()):
        if p.suffix.lower() not in AUDIO or not p.is_file():
            continue
        start = wwoz.start_time_from_name(p)
        if start is None:
            if Path(str(p.with_suffix("")) + ".autocut.json").exists():
                found.append(p)
            continue
        try:
            end = start + timedelta(seconds=wwoz.ffprobe_duration(p))
        except Exception as e:             # one unreadable file must not end the batch
            wwoz.out(f"  skipping {p.name}: {e}")
            continue
        for d in days:
            a = max(start, wwoz_autocut._clock(d, "10:45"))
            b = min(end, wwoz_autocut._clock(d, "19:00"))
            if (b - a).total_seconds() >= 1200:
                found.append(p)
                break
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", action="store_true", help="split each recording afterwards")
    ap.add_argument("--no-shazam", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    files = fest_recordings()
    print(f"{len(files)} festival-day recording(s)")
    for f in files:
        print(f"\n########## {f.name}", flush=True)
        try:
            wwoz_autocut.autocut(f, shazam=not args.no_shazam)
            if args.split:
                class A:  # the argparse namespace cmd_split expects
                    file = str(f); tracklist = None; out = None; lead_in = 15
                wwoz.cmd_split(A)
        except Exception:
            traceback.print_exc()
            print(f"FAILED: {f.name} — continuing with the next file")
    print("\nbatch done")


if __name__ == "__main__":
    main()
