#!/usr/bin/env python3
"""Auto-detect and label the performances in a WWOZ festival recording.

How: Silero VAD finds where people are TALKING (DJ announcements, interviews)
vs. where music plays. Long talk regions separate performances. Whisper
transcribes the edges of each talk region locally, and the music between two
talk regions is named from what the DJs say around it:

  - the introduction just before the music ("right now from the Jazz Tent, the
    David Bode Big Band") and the back-announcement just after it ("you just
    listened to Fred Wesley and the New JBs"), matched against the official
    schedule of the WHOLE festival, with fuzzy spelling (Whisper writes
    "Boutté" as "Bouté", "Boote" or "Bootsay"), so a set the DJs replay from
    an earlier day ("recorded last Friday at the Jazz & Heritage Stage") is
    named and dated too;
  - the WWOZ Jazz Tent schedule when the DJs name the act on the air there,
    and as a last resort (marked "schedule guess");
  - Shazam for short gaps between sets: the records DJs play there fingerprint,
    live sets don't.

Results land in the recording's .tracklist.csv (show column = "autocut …")
plus a sidecar .autocut.json with the transcripts and the evidence for every
label, for review.
"""

import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

import wwoz

SR = 16000
CHUNK = 1200          # decode/VAD chunk seconds
DENS_WIN = 60         # rolling speech-density window (s)
DENS_ON, DENS_OFF = 0.5, 0.35
MIN_TALK = 150        # a talk region this long = set boundary (shorter = banter)
MERGE_GAP = 360       # merge talk regions separated by < this much music
MIN_SET = 360         # keep music spans at least this long (s)

INTRO_WINDOW = 300    # the introduction is looked for in the last 5 min before the music
OUTRO_WORDS = 200     # the back-announcement in the first ~200 words after it
SAME_ACT_GAP = 900    # one act on both sides of a talk shorter than this = one performance
SHORT_SPAN = 20 * 60  # unnamed spans up to this long are Shazam-checked for records
BROADCAST_END = 19    # WWOZ's festival broadcast runs until 7 PM even when the gates close early
JAZZ_TENT = "WWOZ Jazz Tent"
INTERVIEW_STAGE = "Allison Miner Music Heritage Stage"

# Words that never identify an act by themselves ("brass band", "big chief" …)
GENERIC = set("""
band bands brass jazz orchestra ensemble quartet quintet trio sextet project new orleans all
stars allstars allstar big little special guest guests tribute friends family blues soul funk
funky gospel zydeco cajun swamp live singers singer players revue sound sounds music choir
mass community church baptist high school college university academy youth kids children
childrens showcase social aid pleasure club clubs chief chiefs mardi gras indian indians
hunters black masking company group dance dancers steppers krewe nola louisiana jamaica
jamaican heritage festival fest stage tent hall interview interviewer celebrating life legacy
legend legends classics classic original reggae system dj mc jr sr ii iii men women boys girls
kings queens presents featuring feat ft tribe nation workshop demonstration parade
""".split())

# Ordinary words that happen to be in a band name: never enough on their own,
# fine as part of a longer match ("the Truth Brass Band", "Free Agents").
COMMON = set("""
absolute abundant acoustic africa african agents aging alive american anointed arrows audiences
ballads beans birds birth biscuits blind bliss boyfriend breakfast breed brother brothers burning
california callers campus cannon captain cattle centennial change chops choral chord chorus chosen
circle circus clouds collective council cowboy cowboys created crescent cultural culture dancehall
dancing desert difference dimensions disturbers dixie dollars downs dozen drumline duets early
earth eight electric evangelist experience express extension fabulous falls federation finest
flaming flamenco folklore forever forgotten friday friendship garden gentlemen golden great greater
green greens groove groovers grounds guardians guitar harmonizers haunted hearts heights hills
holiday holler honey honky honoring horns hounds international jumping junior karma ladies liberty
light lighthouse lining local loose masters masquerade melody members meters metropolitan midnite
mighty ministry missionary monster morning mouth movement museum native night notes panic pardners
people peppers present prince professor queen rebels rebirth reckless regional resistance revelers
revival rhythms roots roses ruler rumble samba showers silhouette singing situation skillet skinny
slick smart smoking smoove songwriters souls south spear spins spirit square squad steel stompers
stone storm storyteller street string stripe summer summers together traditional trail trouble
trucks trumpet truth twisters underground union unlimited vessels vibes walking white whites worker
world love supreme free blue time sweet smoke dragon river rivers lake dive rising sun suns power rose
keys magic lost star stars fire young groove garden double rich pocket land royal island piano
legacy spirit wild west wolf mountain city country home heart road water good soul rock roll
""".split())

# First names and everyday surnames: a lone "John" or "Harris" names nobody.
FIRST_NAMES = set("""
john james david dave peter robert rob kevin thomas tom michael mike steve steven stephen george
mark ivan chris eric kyle gerald donald don nicholas nick judith jake jimmy jim jack henry roger
glenn glen tony fred jason victor richard rick charles charlie craig bruce paul joseph joe louis
sam samuel andrew andy scott johnny william will billy bill larry kenny ken kim lisa maggie sally
helen samantha sierra terence terrance cyril derek amanda amelia andre arthur benny betty beverly
bradley cameron carlos carol catherine cedric claude cristina curtis daniel dan dayna debbie dianne
donna dwayne eleanor erica felicia frank franklin gregg harvey herbert herbie horace jacky jamil
javier jermaine jessica joanna johanna jonathan jonathon jordan kelli kelly kristi kristin kristy
lainey leyla lilli linda mason micah morgan norbert omari patrice pierre quiana raphael renee
ricardo robin roderick roland ronnie rosie sarah sharif susan tanner terrell tiago tyler tyron
tyronne vicki vincent wayne wendell woodrow yolanda yvette zachary shirley stevie teddy tommy
travis harper johnson jones smith williams wilson walker watson brooks campbell carter gabriel
griffin harris harrison lewis mitchell nelson parker taylor thomas turner young allen andrews davis
jackson miller moore robinson brown green white collins owens powell porter stevens riley gibson
""".split())

STOP = set("the and a an of his her their its with n y de la le les et du des el los las da in on at for to".split())

INTRO_CUES = set("welcome stage heres going next listen hear check throw lets ready got live "
                 "broadcasting recorded recording performance presenting introduce introducing".split())
OUTRO_PAIRS = {("that", "was"), ("just", "heard"), ("just", "listened"), ("just", "listening"),
               ("been", "listening"), ("were", "listening"), ("been", "hearing"), ("were", "hearing"),
               ("you", "heard"), ("we", "heard"), ("heard", "from"), ("listening", "to"),
               ("set", "from"), ("set", "by"), ("a", "set"),
               ("has", "been"),
               # the bandleader's own sign-off at the end of a set
               ("my", "name"), ("hand", "for"), ("hands", "for"), ("applause", "for"), ("up", "for")}
STAGE_WORDS = [(r"economy hall", "Economy Hall Tent"), (r"jazz (tent|10|ten)\b", "WWOZ Jazz Tent"),
               (r"blues (tent|10|ten)\b", "Blues Tent"), (r"gospel (tent|10|ten)\b", "Gospel Tent"),
               (r"jazz (and|n) heritage stage", "Jazz & Heritage Stage"), (r"congo square", "Congo Square Stage"),
               (r"\bfa[iy]s? do do", "Fais Do-Do Stage"), (r"festival stage", "Festival Stage"),
               (r"gentilly", "Shell Gentilly Stage"), (r"lagniappe|lanyap", "Lagniappe Stage"),
               (r"allison miner", INTERVIEW_STAGE), (r"cultural (exchange|pavilion)", "Jamaica Cultural Exchange Pavilion"),
               (r"rhythm ?pourium", "Rhythmpourium Tent"), (r"(kids|childrens) (tent|10)", "Ochsner Children's Tent")]
WEEKDAY_NUM = {d: i for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday",
                                           "saturday", "sunday"])}
OUTRO_WORDS_CUES = {"fabulous", "killer", "fantastic", "amazing"}
RECORDED_CUES = {"recorded", "recording", "recordings", "prerecorded", "yesterday"}
NUMBER_WORDS = set("one two three four five six seven eight nine ten eleven twelve noon".split())
LATER = {"tonight", "tomorrow", "later", "monday", "tuesday", "wednesday", "thursday", "friday",
         "saturday", "sunday"}


def short_name(name):
    """'Adonis Rose Jazz Orchestra featuring special guest Andromeda Turre' ->
    'Adonis Rose Jazz Orchestra': the guest list is in the schedule, not the file name."""
    return re.split(r"\s+(?:featuring|feat\.|ft\.|with special guests?)\s+", name, flags=re.I)[0].strip(" ,")


def hms(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02}:{s % 60:02}"


def words_of(text):
    """Lower-case ASCII words: 'Boutté' -> 'boutte', "Lil' Ed & the" -> lil ed and the."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "").replace("`", "")
    return re.findall(r"[a-z0-9]+", s)


def clean_name(artist):
    return re.sub(r"\s+", " ", (artist or "").replace("’", "'").replace("“", '"')
                  .replace("”", '"')).strip().rstrip("*").strip()


# ------------------------------------------------------------- listening ---

def decode(path, start, dur):
    ffmpeg = wwoz.find_tool("ffmpeg")
    r = subprocess.run([ffmpeg, "-v", "error", "-ss", str(start), "-i", str(path),
                        "-t", str(dur), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, creationflags=wwoz.NOWIN)
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def speech_mask(path, scan_start, scan_end):
    """1-second speech/no-speech mask across [scan_start, scan_end]."""
    from silero_vad import load_silero_vad, get_speech_timestamps
    model = load_silero_vad(onnx=True)
    total = int(scan_end - scan_start)
    mask = np.zeros(total + 2)
    pos = scan_start
    while pos < scan_end:
        dur = min(CHUNK + 30, scan_end - pos)  # 30s overlap
        audio = decode(path, pos, dur)
        if len(audio) < SR:
            break
        segs = get_speech_timestamps(audio, model, sampling_rate=SR,
                                     return_seconds=True,
                                     min_speech_duration_ms=400,
                                     min_silence_duration_ms=300)
        for s in segs:
            a = int(pos - scan_start + s["start"])
            b = int(pos - scan_start + s["end"]) + 1
            mask[max(0, a):min(total + 2, b)] = 1
        wwoz.out(f"    VAD {hms(pos)} -> {hms(min(pos + CHUNK, scan_end))}")
        pos += CHUNK
    return mask


def talk_regions(mask, scan_start):
    dens = np.convolve(mask, np.ones(DENS_WIN) / DENS_WIN, mode="same")
    blocks = []
    in_b = False
    for i, d in enumerate(dens):
        if d > DENS_ON and not in_b:
            in_b, b0 = True, i
        elif d <= DENS_OFF and in_b:
            in_b = False
            if i - b0 >= 40:
                blocks.append([b0, i])
    if in_b:
        blocks.append([b0, len(dens)])
    # big blocks only, then merge across short music interludes
    blocks = [b for b in blocks if b[1] - b[0] >= MIN_TALK]
    merged = []
    for b in blocks:
        if merged and b[0] - merged[-1][1] < MERGE_GAP:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    return [(scan_start + a, scan_start + b) for a, b in merged]


def name_hints(guide, day, limit=350):
    """The day's acts for Whisper to expect (it spells names it was told about)."""
    names = []
    days = sorted(guide)
    order = [(day, None)] + [(d, (JAZZ_TENT, "Economy Hall Tent")) for d in reversed(days) if d < day]
    for d, stages in order:
        for s in sorted(guide.get(d, []), key=lambda s: (not s.get("on_air") and s["stage"] != JAZZ_TENT, s["start"])):
            if stages and s["stage"] not in stages:
                continue
            core = re.split(r"\s+(?:featuring|feat\.|ft\.|presents|with special|celebrating)\s+|:",
                            clean_name(s["artist"]))[0].strip()
            if core and core not in names:
                names.append(core)
    out = ""
    for n in names:
        if len(out) + len(n) + 2 > limit:
            break
        out += (", " if out else "") + n
    return out


def transcribe(path, region, model, hints=None):
    """Return {'head': text, 'tail': text, 'tail_words': [(abs_sec, word), …]}.
    The back-announce of the previous set lives at the START of a talk region;
    the intro of the next set in the last few minutes before the music resumes
    (often after a 'Livewire' club-calendar reading, hence the long tail).
    tail_words carry word-level timestamps so cuts can start exactly where the
    DJ begins introducing the next act."""
    a, b = region
    extra = {"hotwords": hints} if hints else {}

    def txt(audio, base=None):
        segments, _ = model.transcribe(audio, language="en", vad_filter=False,
                                       word_timestamps=base is not None, **extra)
        segs = list(segments)
        text = " ".join(s.text.strip() for s in segs)
        words = []
        if base is not None:
            for s in segs:
                for w in (s.words or []):
                    words.append((round(base + w.start, 1), w.word.strip()))
        return text, words

    if b - a <= 240:
        whole, words = txt(decode(path, a, b - a), base=a)
        return {"head": whole, "tail": whole, "tail_words": words}
    head_len = min(180, b - a)
    tail_len = min(360, b - a - 60)
    head, _ = txt(decode(path, a, head_len))
    tail, words = txt(decode(path, b - tail_len, tail_len), base=b - tail_len)
    return {"head": head, "tail": tail, "tail_words": words}


# --------------------------------------------------------------- lineup ---

def load_guide():
    """Jazz Fest's official schedule and every festival day's broadcast list (the sets
    marked on_air: what WWOZ aired, see wwoz.festival_guide)."""
    return wwoz.festival_guide()


def on_air_sets(day_sets):
    """The day's schedule of what was on the air: WWOZ's broadcast list when there is one,
    else Jazz Fest's Jazz Tent, which WWOZ mostly carries."""
    return [s for s in day_sets if s.get("on_air")] or [s for s in day_sets if s["stage"] == JAZZ_TENT]


def _clock(day, hhmm):
    y, mo, d = map(int, day.split("-"))
    h, m = map(int, hhmm.split(":"))
    return datetime(y, mo, d, h, m, tzinfo=wwoz.TZ)


class Lineup:
    """Every act of the festival, indexed so a transcript can be searched for
    their names with Whisper's misspellings tolerated."""

    def __init__(self, guide):
        self.acts = {}                      # key -> {"name", "tokens", "sets": [(day, set)]}
        for day, sets in guide.items():
            for s in sets:
                name = clean_name(s["artist"])
                toks = [t for t in words_of(name) if t not in STOP]
                if not toks:
                    continue
                act = self.acts.setdefault(" ".join(toks), {"name": name, "tokens": toks, "sets": []})
                act["sets"].append((day, s))
        self.df = Counter(t for a in self.acts.values() for t in set(a["tokens"]))
        self.by_token = defaultdict(list)   # name token -> [(key, index)]
        for key, a in self.acts.items():
            for i, t in enumerate(a["tokens"]):
                self.by_token[t].append((key, i))
        self.by_first = defaultdict(list)
        for t in self.by_token:
            self.by_first[t[0]].append(t)
        self._similar = {}

    @staticmethod
    def sim(w, t, loose=False):
        """How likely the transcript word w is the name word t (0 = not at all).
        loose: the word right after an exactly-heard part of the name ("Charlie Musleway")."""
        if w == t:
            return 1.0
        if {w, t} == {"lewis", "louis"}:     # the one everyday-word homophone that matters
            return 0.95
        if len(t) < 4 or len(w) < 3 or w[0] != t[0]:
            return 0
        if w in everyday_words() and soundex(w) != soundex(t):
            return 0                         # "doing" is not a misspelt "Downing"
        if w.rstrip("s") == t.rstrip("s"):
            return 0.95
        if abs(len(w) - len(t)) > (3 if loose else 2):
            return 0
        r = SequenceMatcher(None, w, t).ratio()
        sw, st = soundex(w), soundex(t)
        if sw == st and len(w) >= 4 and abs(len(w) - len(t)) <= 1 and r >= 0.6:
            r = max(r, 0.82)                 # sound-alikes: Marsha/Marcia
        if loose and sw[:3] == st[:3] and r >= 0.5:
            r = max(r, 0.6)                  # "Charlie Musleway" for Musselwhite
        if w in everyday_words() and r < 0.9:
            return 0
        return r if r >= (0.6 if loose and len(w) >= 5 else 0.8) else 0

    def similar(self, w):
        hit = self._similar.get(w)
        if hit is None:
            hit = [(t, s) for t in self.by_first.get(w[:1], ()) if (s := self.sim(w, t))]
            self._similar[w] = hit
        return hit

    def mentions(self, words):
        """Every place in a word list where an act is named:
        [(i, j, keys, matched)] for words[i:j], matched = [(name word, similarity, said)].
        One run can fit several acts ("Kermit Ruffins" is two of them); the caller
        picks between them by date and time."""
        n, runs = len(words), []
        covered = {}                        # key -> index the last run for it reached
        for i in range(n):
            starts = []
            for t, s in self.similar(words[i]):
                starts += [(k, idx, 1, s, words[i]) for k, idx in self.by_token[t]]
            if i + 1 < n:                    # "sonic wonder" for "Sonicwonder"
                both = words[i] + words[i + 1]
                for t, s in self.similar(both):
                    if len(t) >= 7:
                        starts += [(k, idx, 2, s, both) for k, idx in self.by_token[t]]
            best = {}
            for key, idx, used, s, said in starts:
                if covered.get(key, -1) > i:
                    continue
                toks = self.acts[key]["tokens"]
                j, k, got = i + used, idx + 1, [(toks[idx], s, said)]
                while k < len(toks) and j < n:
                    for skip in range(3):    # tolerate two words Whisper slipped in
                        jj = j + skip
                        loose = skip == 0 and got[-1][1] == 1.0 and got[-1][0] not in GENERIC
                        s2 = self.sim(words[jj], toks[k], loose) if jj < n else 0
                        if s2:
                            got.append((toks[k], s2, words[jj]))
                            j, k = jj + 1, k + 1
                            break
                    else:
                        break
                if len(got) > len(best.get(key, (0, []))[1]):
                    best[key] = (j, got)
            if not best:
                continue
            top = max(len(g) for _, g in best.values())
            groups = defaultdict(list)       # identical matches across acts -> one run
            for key, (j, got) in best.items():
                if len(got) == top:
                    groups[(j, tuple(t for t, _, _ in got))].append((key, got))
            for (j, _), members in groups.items():
                keys = [k for k, _ in members]
                runs.append((i, j, keys, members[0][1]))
                for k in keys:
                    covered[k] = j
        return runs

    def strength(self, got):
        """2 = this names the act on its own, 1 = a hint, 0 = nothing."""
        content = [(t, s, w) for t, s, w in got if t not in GENERIC]
        if not content:
            return 0
        if len(got) >= 2 and (len(content) >= 2 or content[0][0] not in FIRST_NAMES):
            return 2
        t, s, w = content[0]
        if (len(got) == 1 and len(t) >= 5 and self.df[t] == 1 and t not in COMMON
                and t not in FIRST_NAMES and (s == 1.0 or (s >= 0.9 and len(w) >= 5))):
            return 2
        return 1 if (len(t) >= 4 and t not in FIRST_NAMES and t not in COMMON) else 0


_SOUNDEX = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
            "l": "4", "m": "5", "n": "5", "r": "6"}


def soundex(w):
    """The classic sound-alike key: Marsha = Marcia = M620."""
    out, last = w[:1], _SOUNDEX.get(w[:1], "")
    for ch in w[1:]:
        c = _SOUNDEX.get(ch, "")
        if c and c != last:
            out += c
        if ch not in "hw":
            last = c
    return (out + "000")[:4]


_EVERYDAY = None


def everyday_words():
    """Whole English words, from Whisper's own vocabulary (common words and common
    names are single tokens there; a misspelt band name like 'Bowdie' is not)."""
    global _EVERYDAY
    if _EVERYDAY is None:
        _EVERYDAY = set()
        try:
            from faster_whisper.utils import download_model
            vocab = Path(download_model("base", local_files_only=True)) / "vocabulary.txt"
            for line in vocab.read_text(encoding="utf-8").split("\n"):
                if line.startswith("Ġ") and line[1:].isalpha():
                    _EVERYDAY.add(line[1:].lower())
        except Exception:
            pass                             # no model on disk yet: fuzzy matching unfiltered
    return _EVERYDAY


# ------------------------------------------------------------ transcripts ---

def clean_stream(words):
    """Indexes of words to IGNORE: the Livewire club calendar (dozens of band names
    playing clubs tonight), the simulcast station list and the sponsor credits."""
    skip = set()
    n = len(words)
    i = 0

    def is_lw(k):
        return "livewire" in words[k] or (words[k] == "live" and k + 1 < n and words[k + 1] in ("wire", "ride"))

    found_start = False
    while i < n:
        if is_lw(i) and any(w in ("calendar", "sponsored") for w in words[i + 1:i + 10]):
            found_start = True
            end = None
            for k in range(i + 30, n):
                if is_lw(k) or words[k:k + 3] == ["live", "music", "events"]:
                    end = k
                    break
            if end is None:
                end = n
            else:                        # through "… support live local music"
                for k in range(end, min(n, end + 45)):
                    if words[k:k + 3] == ["live", "local", "music"]:
                        end = k + 3
                        break
            skip.update(range(i, end))
            i = end
            continue
        i += 1
    if not found_start:                  # began mid-reading: skip to its sign-off
        for k in range(n - 2):
            if words[k:k + 3] == ["live", "music", "events"] and k < 400:
                end = k + 3
                for q in range(end, min(n, end + 45)):
                    if words[q:q + 3] == ["live", "local", "music"]:
                        end = q + 3
                        break
                skip.update(range(0, end))
                break
    for k in range(n):
        w = words[k]
        if w in ("simulcast", "simul") or words[k:k + 2] == ["major", "funding"]:
            stop = "hawaii" if w.startswith("simul") else "financial"
            end = next((q + 1 for q in range(k, min(n, k + 160)) if words[q] == stop), min(n, k + 120))
            skip.update(range(k, end))
    return skip


def stream(text=None, timed=None):
    """[(word, time-or-None)] with the ignorable stretches removed."""
    if timed is not None:
        pairs = [(w, t) for t, raw in timed for w in words_of(raw)]
    else:
        pairs = [(w, None) for w in words_of(text)]
    skip = clean_stream([w for w, _ in pairs])
    return [p for k, p in enumerate(pairs) if k not in skip]


# --------------------------------------------------------------- autocut ---

def autocut(path: Path, start_off=None, end_off=None, transcribe_regions=True, shazam=True):
    duration = wwoz.ffprobe_duration(path)
    file_start = wwoz.recording_start(path, duration)
    if wwoz.start_time_from_name(path) is None:
        wwoz.out(f"  (start time from file mtime: {file_start:%Y-%m-%d %I:%M %p})")
    guide = load_guide()

    day = None
    # the file's own date first — only fall forward for pre-midnight starts
    for cand in [file_start.strftime("%Y-%m-%d"),
                 (file_start + timedelta(hours=12)).strftime("%Y-%m-%d")]:
        if cand in guide:
            day = cand
            break

    # scan window: the festival broadcast hours if known, else the whole file
    if start_off is None or end_off is None:
        a, b = 0, duration
        if day:
            # through the evening too (to ~midnight), so post-fest segments get
            # labeled with the WWOZ show that was on the air
            # (Jazz Fest's gates open at 11, whatever WWOZ airs first)
            first = min([s["start"] for s in on_air_sets(guide[day])]
                        + (["11:00"] if day in wwoz.load_fest_schedule() else []), default="11:00")
            a = max(0, (_clock(day, first) - timedelta(minutes=15) - file_start).total_seconds())
            b = min(duration, (_clock(day, "23:59") - file_start).total_seconds())
            if b - a < 600:
                a, b = 0, duration
        start_off = a if start_off is None else start_off
        end_off = b if end_off is None else end_off
    start_off, end_off = int(start_off), int(end_off)
    wwoz.out(f"  scanning {path.name} {hms(start_off)} -> {hms(end_off)} "
             f"({day or 'no schedule for this day'})")

    # 1) where is the talking? (reuse a previous scan of the same window)
    side = Path(str(path.with_suffix("")) + ".autocut.json")
    regions, cached_texts, old = None, {}, {}
    if side.exists():
        try:
            old = json.loads(side.read_text(encoding="utf-8"))
            if old.get("scanned") == [hms(start_off), hms(end_off)]:
                regions = [(r["start_s"], r["end_s"]) for r in old["talk_regions"] if "start_s" in r]
                for r in old["talk_regions"]:
                    if "head_text" in r and "tail_words" in r:
                        cached_texts[(r["start_s"], r["end_s"])] = \
                            {"head": r["head_text"], "tail": r["tail_text"],
                             "tail_words": [tuple(x) for x in r["tail_words"]]}
                wwoz.out("  (reusing talk regions from previous scan)")
        except Exception:
            regions, old = None, {}
    if not regions:
        mask = speech_mask(path, start_off, end_off)
        regions = talk_regions(mask, start_off)
    regions = [(int(a), int(b)) for a, b in regions]
    wwoz.out(f"  {len(regions)} talk regions (announcements/interviews):")
    for a, b in regions:
        wwoz.out(f"    {hms(a)} -> {hms(b)}  ({int(b - a)}s talk)")

    # 2) music spans between talk regions = performances
    spans = []
    cursor = start_off
    for a, b in regions:
        if a - cursor >= MIN_SET:
            spans.append((cursor, a))
        cursor = b
    if end_off - cursor >= MIN_SET:
        spans.append((cursor, end_off))

    # 3) transcribe the talk
    transcripts = []
    if transcribe_regions and regions:
        wm = None
        if not all(r in cached_texts for r in regions):
            from faster_whisper import WhisperModel
            wwoz.out("  transcribing announcements (local Whisper)…")
            wm = WhisperModel("base", device="cpu", compute_type="int8")
        else:
            wwoz.out("  (reusing cached transcripts)")
        hints = name_hints(guide, day) if day else None
        for r in regions:
            parts = cached_texts.get(r) or transcribe(path, r, wm, hints)
            transcripts.append({"start": hms(r[0]), "end": hms(r[1]),
                                "start_s": r[0], "end_s": r[1],
                                "head_text": parts["head"], "tail_text": parts["tail"],
                                "tail_words": [list(x) for x in parts.get("tail_words", [])]})

    # 4) name each span
    rows = wwoz.read_tracklist(path)
    spins = [r for r in rows if is_spin(r) and not wwoz.is_placeholder_spin(r)]
    labeled = label_spans(spans, regions, transcripts, guide, day, file_start, spins)
    if shazam and day:
        shazam_short_spans(path, labeled, old.get("shazam_checks", {}))
    labeled = merge_same_act(labeled)

    # 5) write into the tracklist + sidecar json
    write_rows(path, rows, labeled, start_off, end_off, file_start, day)
    checks = {str(s["start"]): s["shazam"] for s in labeled if "shazam" in s}
    for k, v in old.get("shazam_checks", {}).items():
        checks.setdefault(k, v)
    side.write_text(json.dumps({
        "version": 5, "scanned": [hms(start_off), hms(end_off)], "day": day,
        "talk_regions": transcripts or [{"start": hms(a), "end": hms(b), "start_s": a, "end_s": b}
                                        for a, b in regions],
        "performances": [{"start": hms(s["start"]), "end": hms(s["end"]), "cut": hms(s["cut"]),
                          "artist": s["artist"], "song": s.get("song", ""), "stage": s["stage"],
                          "how": s["how"], "evidence": s.get("evidence", "")} for s in labeled],
        "shazam_checks": checks,
    }, indent=1, ensure_ascii=False), encoding="utf-8")

    wwoz.out("  performances detected:")
    for s in labeled:
        wwoz.out(f"    {hms(s['start'])} -> {hms(s['end'])}  {s['artist']}"
                 + (f" - {s['song']}" if s.get("song") else "") + f"  ({s['how']})")
    wwoz.out(f"  tracklist updated ({wwoz.tracklist_csv(path).name}); details in {side.name}")
    return labeled


def label_spans(spans, regions, transcripts, guide, day, file_start, spins):
    """Name every music span. Returns dicts: start, end, cut, artist, stage, how, evidence."""
    lineup = Lineup(guide)
    day_sets = guide.get(day, []) if day else []
    on_air = on_air_sets(day_sets)
    texts = {(t["start_s"], t["end_s"]): t for t in transcripts}

    def at(sec):
        return file_start + timedelta(seconds=sec)

    def off(dt):
        return (dt - file_start).total_seconds()

    if day_sets:
        fest_open = min(_clock(day, s["start"]) for s in day_sets)
        fest_close = max(_clock(day, s["end"]) for s in day_sets)
        broadcast_end = max(fest_close, _clock(day, f"{BROADCAST_END}:00"))
    else:
        fest_open = fest_close = broadcast_end = None

    def resolve(keys, a, b):
        """Which set of these acts fits a span at file offsets [a, b]?
        -> (key, day, set, kind) with kind 'live' / 'earlier' / 'replay', or None."""
        best = None
        for key in keys:
            for d, s in lineup.acts[key]["sets"]:
                s0, s1 = off(_clock(d, s["start"])), off(_clock(d, s["end"]))
                overlap = min(b, s1 + 600) - max(a, s0 - 600)    # sets run a little late
                if d == day and overlap >= min(600, (b - a) / 3):
                    rank = (0, abs(s0 - a))      # on stage now: the closest slot
                    kind = "live"
                elif d == day and s1 <= a + 600:
                    rank, kind = (1, -s0), "earlier"
                elif day and d < day:
                    rank, kind = (2, -s0), "replay"   # the most recent earlier day
                else:
                    continue                     # later today / a later day: not this
                if best is None or rank < best[0]:
                    best = (rank, (key, d, s, kind))
        return None if best is None else best[1]

    def replay_hint(ws, a):
        """'a recorded performance of … that was recorded Thursday from Economy Hall'
        -> ({'2026-04-23'}, 'Economy Hall Tent'): which day and stage a replay is from."""
        words = [w for w, t in ws if t is not None and t >= a - 90]
        if not RECORDED_CUES & set(words):
            return None, None
        text = " ".join(words)
        stage = next((st for pat, st in STAGE_WORDS if re.search(pat, text)), None)
        days = set()
        today = datetime.strptime(day, "%Y-%m-%d").date() if day else None
        for w in words:
            if today and w == "yesterday":
                days.add(str(today - timedelta(days=1)))
            elif today and w in WEEKDAY_NUM:
                back = (today.weekday() - WEEKDAY_NUM[w]) % 7 or 7
                days.add(str(today - timedelta(days=back)))
        return (days or None), stage

    def fits_hint(r, hint):
        days, stage = hint
        return (not days or r[1] in days) and (not stage or r[2]["stage"] == stage)

    def sounds_like(ws, a, hint):
        """The DJ named a replay we could not spell-match ("a warre Nila" for Aurora
        Nealand): compare letters against just the sets the day and stage allow."""
        days, stage = hint
        if not days:
            return None
        letters = "".join(w for w, t in ws if t is not None and t >= a - 90)
        scored = []
        for d in days:
            for s in guide.get(d, []):
                if stage and s["stage"] != stage:
                    continue
                toks = [t for t in words_of(short_name(clean_name(s["artist"]))) if t not in STOP]
                best = 0
                for core in ("".join(toks[:2]), "".join(toks)[:16]):   # "Aurora Nealand('s Royal Roses)"
                    if len(core) >= 6:
                        best = max([best] + [SequenceMatcher(None, core, letters[k:k + len(core)]).ratio()
                                             for k in range(0, max(1, len(letters) - len(core) + 1))])
                key = " ".join(t for t in words_of(clean_name(s["artist"])) if t not in STOP)
                scored.append((best, key, d, s))
        scored.sort(key=lambda x: -x[0])
        if scored and scored[0][0] >= 0.55 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.06):
            sc, key, d, s = scored[0]
            return key, d, s, ("earlier" if d == day else "replay"), sc
        return None

    def rundown(words, j):
        """'… Kermit Ruffins at 2:10', 'Rod Stewart is on the Festival Stage' — a
        listing of what is on elsewhere or later, not an introduction."""
        nxt = words[j:j + 6]
        for q, w in enumerate(nxt[:-1]):
            if w == "at" and (nxt[q + 1].isdigit() or nxt[q + 1] in NUMBER_WORDS):
                return True
        if nxt[:2] in (["is", "at"], ["is", "on"], ["are", "at"], ["are", "on"], ["will", "be"],
                       ["is", "playing"], ["are", "playing"], ["plays", "at"], ["coming", "up"]):
            return True
        return bool(LATER & set(nxt[:3]))

    def found(ws, lo_t=None, hi_t=None, first_words=None):
        """Mentions in a word stream, each with the words around it."""
        out = []
        words = [w for w, _ in ws]
        for i, j, keys, got in lineup.mentions(words):
            t = ws[i][1]
            if lo_t is not None and t is not None and not (lo_t <= t <= hi_t):
                continue
            if first_words is not None and i > first_words:
                continue
            st = lineup.strength(got)
            if st:
                before = words[max(0, i - 12):i]
                out.append({"i": i, "j": j, "t": t, "keys": keys, "got": got, "st": st,
                            "intro_cue": bool(set(before[-10:]) & INTRO_CUES or set(words[j:j + 6]) & INTRO_CUES),
                            "outro_cue": bool(set(zip(before, before[1:])) & OUTRO_PAIRS
                                              or set(before) & OUTRO_WORDS_CUES
                                              or before[-1:] == ["im"]),      # "I'm Aurora Nealand, thank you"
                            "recorded": bool(RECORDED_CUES & set(words[max(0, i - 20):j + 20])),
                            "rundown": rundown(words, j),
                            "said": " ".join(words[max(0, i - 8):j + 4])})
        return out

    def act_label(key, d, s, kind):
        name = s.get("label") or short_name(lineup.acts[key]["name"])
        if s["stage"] == INTERVIEW_STAGE:
            name += " (interview)"
        if s.get("on_air"):
            pass                   # WWOZ's list already says when it was recorded, if it was
        elif kind == "replay":
            name += f" (recorded {_clock(d, s['start']):%b} {_clock(d, s['start']).day})"
        elif kind == "earlier":
            name += " (recorded earlier today)"
        return name, s["stage"]

    labeled = []
    for a, b in spans:
        ri = next((r for r in regions if abs(r[1] - a) < 5), None)   # talk before the span
        rj = next((r for r in regions if abs(r[0] - b) < 5), None)   # talk after it
        ti, tj = texts.get(ri), texts.get(rj)
        clock_a, clock_b = at(a), at(b)

        intro_ws = stream(timed=ti["tail_words"]) if ti else []
        intro = found(intro_ws, a - INTRO_WINDOW, a + 10) if ti else []
        outro_ws = []
        if tj:
            if tj["end_s"] - tj["start_s"] <= 240 and tj.get("tail_words"):
                outro_ws = stream(timed=tj["tail_words"])          # transcribed whole
            else:
                outro_ws = stream(text=tj["head_text"])
                if tj["end_s"] - tj["start_s"] <= 540 and tj.get("tail_words"):
                    outro_ws += stream(timed=tj["tail_words"])     # head and tail cover it all
        outro = found(outro_ws, first_words=OUTRO_WORDS) if tj else []
        last_words = {w for w, t in intro_ws if t is not None and t >= a - 90}
        recorded_cue = bool({"recorded", "prerecorded", "recording", "recordings"} & last_words)
        after_hours = bool(broadcast_end) and clock_a >= broadcast_end - timedelta(minutes=2)
        in_broadcast = bool(fest_open) and not after_hours and clock_b > fest_open - timedelta(minutes=5)

        # the introduction: the last act named before the music (not a listing of
        # later sets). A set from earlier in the festival only when the DJ introduces
        # it as a recording ("a pre-recorded performance of …", "up next … recorded
        # yesterday at the Jazz Tent"), from the day and stage the DJ gives.
        hint = replay_hint(intro_ws, a) if ti else (None, None)
        I = None
        strong = sorted((m for m in intro if m["st"] >= 2 and not m["rundown"]),
                        key=lambda m: m["t"] or 0, reverse=True)
        for m in strong:
            if after_hours:
                break
            r = resolve(m["keys"], a, b)
            if not r:
                continue
            if r[3] == "live":
                ok = m["intro_cue"] or (m["t"] or 0) >= a - 90
            else:
                ok = ((m["t"] or 0) >= a - 240 and m["recorded"] and m["intro_cue"]
                      and fits_hint(r, hint))
            if ok:
                I = (m, r)
                break
        if not I and recorded_cue and not after_hours:
            g = sounds_like(intro_ws, a, hint)
            if g:
                I = ({"t": None, "said": f"(sounds like) {lineup.acts[g[0]]['name']}", "st": 2}, g[:4])
        # the back-announcement: the first act named after it ("that was …",
        # "you just listened to …", the bandleader's "my name is …")
        O = None
        for m in sorted(outro, key=lambda m: m["i"]):
            if m["st"] < 2 or after_hours:
                continue
            r = resolve(m["keys"], a, b)
            if r and (m["outro_cue"] or (m["i"] < 15 and (r[3] == "live" or m["recorded"]))):
                O = (m, r)
                break

        # the Jazz Tent set on stage during most of this span, if any
        S = None
        for s in on_air:
            s0, s1 = off(_clock(day, s["start"])), off(_clock(day, s["end"]))
            inter = min(b, s1) - max(a, s0)
            if inter > 0.5 * min(b - a, max(1, s1 - s0)):
                S = (s, inter / (b - a))
                break
        S_key = " ".join(t for t in words_of(clean_name(S[0]["artist"])) if t not in STOP) if S else None
        S_near = S_any = None
        if S and not after_hours:
            S_near = next((m for m in intro + outro if S_key in m["keys"]), None)
            S_any = S_near or next((m for m in found(intro_ws) + found(outro_ws, first_words=OUTRO_WORDS * 2)
                                    if S_key in m["keys"]), None)

        # weigh the evidence: the back-announcement says what was just heard (4 with
        # "that was …", else 2), the introduction what was about to play (3); the
        # Jazz Tent act scores 2 for being named near the music (1 anywhere around
        # it) plus 2 for being on stage then. "A little Dirty Dozen while the stage is
        # set up for Astral Project" thus still files the hour as Astral Project,
        # and "amazing set by Mark Brooks, a recorded session" beats the schedule.
        votes = {}

        def vote(r, pts, why, said, t=None):
            v = votes.setdefault(r[0], {"r": r, "pts": 0, "why": set(), "said": [], "t": None})
            v["pts"] += pts
            v["why"].add(why)
            v["said"].append(said)
            if t is not None and v["t"] is None:
                v["t"] = t
        if I:
            sure = I[0].get("intro_cue") and (I[0]["t"] or 0) >= a - 90
            vote(I[1], 4 if sure else 3, "intro", f"before: …{I[0]['said']}…", I[0]["t"])
        if O:
            vote(O[1], 4 if O[0]["outro_cue"] else 2, "outro", f"after: …{O[0]['said']}…")
        if S and (S_near or S_any):
            sr = (S_key, day, S[0], "live")
            m = S_near or S_any
            vote(sr, 2 if S_near else 1, "named", f"said: …{m['said']}…",
                 m["t"] if m["t"] is not None and m["t"] < a else None)
            vote(sr, 2, "scheduled", "")

        artist = stage = how = evidence = ""
        cut_t = None
        if votes:
            v = max(votes.values(), key=lambda v: (v["pts"], "outro" in v["why"], "intro" in v["why"]))
            artist, stage = act_label(*v["r"])
            why = v["why"]
            how = ("announced + scheduled" if "scheduled" in why
                   else "announced" if {"intro", "outro"} <= why
                   else "heard in intro" if "intro" in why else "heard in outro")
            cut_t = v["t"]
            evidence = " | ".join(s for s in v["said"] if s)
        elif (S and clock_a < fest_close and (not recorded_cue or S[0].get("on_air")) and b - a >= 15 * 60
              and not after_hours):
            artist = S[0].get("label") or short_name(clean_name(S[0]["artist"]))
            stage, how = S[0]["stage"], "schedule guess"
            evidence = ("nobody named it on the air; this was on WWOZ's broadcast list" if S[0].get("on_air")
                        else "nobody named it on the air; this was on the Jazz Tent schedule")

        # studio programming: songs the DJ logged inside a span outside the broadcast
        # (during it the log is unreliable: songs are logged that never aired)
        inside = [r for r in spins if a + 60 <= r["offset_seconds"] <= b - 60]
        if not artist and not in_broadcast and len(inside) >= 2 and inside[0].get("show"):
            artist, stage, how = inside[0]["show"], "", "WWOZ show (songs logged)"
            evidence = f"{len(inside)} songs logged on Spinitron inside it"
        if not artist and file_start and fest_open and (clock_b <= fest_open - timedelta(minutes=5)
                                                        or not in_broadcast):
            try:
                # the show on the air when the music starts (program-change markers
                # split the span if a new show begins partway through)
                when = at(a + min(120, (b - a) / 2))
                evs = wwoz.spinitron_events(when - timedelta(minutes=1), when + timedelta(minutes=1),
                                            include_unlogged=True)
                evs = [e for e in evs if e["start"] <= when < e["end"]] or evs
                if evs:
                    artist = evs[0]["title"]
                    how = ("WWOZ show (before the broadcast)" if clock_b <= fest_open
                           else "WWOZ show (after the broadcast)")
            except Exception:
                pass
            if not artist:
                artist, how = "(outside festival broadcast)", "outside fest hours"
        if not artist:
            if in_broadcast and recorded_cue:
                fest = next((s["festival"] for s in on_air if s.get("festival")), "Jazz Fest")
                artist, how = f"{fest} recording (not identified)", "unlabeled"
            elif b - a <= 12 * 60:
                artist, how = "Between sets", "unlabeled"
            else:
                artist, how = "Unknown performance", "unlabeled"
            evidence = evidence or "nothing recognisable was said around it"

        # cut: where the DJ starts introducing it (from word timestamps), else a
        # short lead into the talk before the music
        lo = ri[0] if ri else a
        if cut_t is not None and cut_t < a:
            cut = max(cut_t - 12, lo)
        else:
            cut = max(lo, a - 30)
        labeled.append({"start": a, "end": b, "cut": int(cut), "artist": artist, "stage": stage,
                        "how": how, "evidence": evidence, "in_broadcast": in_broadcast,
                        "context": " ".join(w for w, _ in intro_ws + outro_ws[:300])})
    return labeled


def shazam_short_spans(path, labeled, cache):
    """Gaps between sets are often records (the DJs play an act's album before its
    set, or a classic). Records fingerprint; live sets don't. Sample the short spans
    nobody named, and spans named only by the schedule, and label the records found."""
    todo = [s for s in labeled if s.get("in_broadcast") and s["end"] - s["start"] <= SHORT_SPAN
            and s["how"] in ("unlabeled", "schedule guess")]
    if not todo:
        return
    import asyncio
    # entries are [first offset, artist, title, samples]; older 3-field ones are re-checked
    cache = {k: v for k, v in cache.items() if all(len(h) == 4 for h in v)}
    pending = [s for s in todo if str(s["start"]) not in cache]
    if pending:
        wwoz.out(f"  Shazam-checking {len(pending)} short gap(s) for records…")
        try:
            found = asyncio.run(_shazam_spans(path, pending))
        except Exception as e:
            wwoz.out(f"    (Shazam check skipped: {e})")
            found = {}
        cache = dict(cache, **found)
    for s in todo:
        hits = cache.get(str(s["start"]))
        if hits is None:
            continue
        s["shazam"] = hits
        # one 12-second match can be a fluke; a record playing is matched again a
        # minute later, or is one the DJs named around it ("the Hot Fives … West End Blues")
        heard = s.get("context", "")
        sure = [h for h in hits if h[3] >= 2 or named_around(h, heard)]
        if sure:
            best = max(sure, key=lambda h: h[3])
            s.update(artist=best[1], song=best[2], stage="", how="record (Shazam)",
                     evidence="Shazam: " + "; ".join(f"{h[1]} - {h[2]} ×{h[3]}" for h in hits))


def named_around(hit, context):
    """Did the DJs say this record's artist or title ("… would record West End Blues")?"""
    padded = f" {context} "
    for part in (hit[1], hit[2]):
        toks = [t for t in words_of(part) if t not in STOP]
        if len(toks) >= 2 and (f" {' '.join(toks[:2])} " in padded or f" {' '.join(toks)} " in padded):
            return True
        if len(toks) == 1 and len(toks[0]) >= 5 and toks[0] not in everyday_words()                 and f" {toks[0]} " in padded:
            return True
    return False


async def _shazam_spans(path, spans, step=60, per_span=8):
    import asyncio
    import tempfile
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    from shazamio import Shazam
    ffmpeg = wwoz.find_tool("ffmpeg")
    shazam = Shazam()
    out, quiet = {}, 0
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "clip.wav"
        for s in spans:
            hits = []
            offs = list(range(s["start"] + 20, s["end"] - 15, step))[:per_span]
            for o in offs:
                subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss", str(o), "-i", str(path), "-t", "12",
                                "-vn", "-ac", "1", "-ar", "44100", "-y", str(wav)],
                               capture_output=True, stdin=subprocess.DEVNULL, creationflags=wwoz.NOWIN)
                try:
                    res = await asyncio.wait_for(shazam.recognize(str(wav)), timeout=wwoz.SHAZAM_TIMEOUT)
                    quiet = 0
                except Exception:
                    quiet += 1
                    if quiet >= 3:
                        return out                   # Shazam went quiet; keep what we have
                    continue
                tr = res.get("track") or {}
                if tr.get("title"):
                    same = next((h for h in hits if h[2] == tr["title"]), None)
                    if same:
                        same[3] += 1
                    else:
                        hits.append([o, tr.get("subtitle", ""), tr["title"], 1])
                await asyncio.sleep(wwoz.SHAZAM_PAUSE)
            out[str(s["start"])] = hits
    return out


HOW_RANK = ["announced", "announced + scheduled", "heard in intro", "heard in outro",
            "record (Shazam)", "schedule guess"]


def merge_same_act(labeled):
    """One act on both sides of a short talk (an interview, a station ID mid-set)
    is one performance: keep the first marker so the split file holds the whole set."""
    out = []
    for s in labeled:
        p = out[-1] if out else None
        if (p and p["artist"] == s["artist"] and not s.get("song") and not p.get("song")
                and p["how"] != "unlabeled" and s["start"] - p["end"] <= SAME_ACT_GAP):
            p["end"] = s["end"]
            rank = {h: i for i, h in enumerate(HOW_RANK)}
            if rank.get(s["how"], 99) < rank.get(p["how"], 99):
                p["how"], p["evidence"] = s["how"], s.get("evidence", "")
            continue
        out.append(dict(s))
    return out


# ------------------------------------------------------------- tracklist ---

def is_spin(r):
    """A song from the DJs' own log (Spinitron), as opposed to our markers."""
    show = str(r.get("show", ""))
    return bool(show) and not show.startswith(("autocut", "set @")) and show not in (
        "manual marker", "WWOZ program", "shazam")


def is_show_marker(r):
    """A marker for a regular WWOZ show (not a festival act): one show stays one file."""
    show = str(r.get("show", ""))
    return show == "WWOZ program" or show.startswith("autocut WWOZ show")


def write_rows(path, rows, labeled, start_off, end_off, file_start, day):
    """Replace earlier set/autocut markers inside the scanned window with the new
    labels; drop the Spinitron 'LIVE BROADCAST' placeholders (they are not songs)
    and songs logged in the middle of a live set (they were not aired — checked by
    fingerprinting: the audio there is the band on stage)."""
    live = [s for s in labeled if s.get("in_broadcast") and s["stage"]]
    keep = []
    for r in rows:
        o = int(r["offset_seconds"])
        show = str(r.get("show", ""))
        if start_off <= o <= end_off and show.startswith(("set @", "autocut")):
            continue
        if wwoz.is_placeholder_spin(r):
            continue
        if is_spin(r) and any(s["cut"] + 90 < o < s["end"] - 90 for s in live):
            continue
        keep.append(r)
    for s in labeled:
        o = s["cut"]
        keep.append({"offset": hms(o),
                     "clock_time": (file_start + timedelta(seconds=o)).strftime("%Y-%m-%d %I:%M %p")
                     if file_start else "",
                     "artist": s["artist"], "song": s.get("song", ""), "release": "",
                     "show": f"autocut {s['how']}" + (f" @ {s['stage']}" if s["stage"] else ""),
                     "offset_seconds": o})
    keep.sort(key=lambda r: int(r["offset_seconds"]))
    # the same show marked twice in a row (its 7:00 program change, then the music
    # after the station ID at 7:03) would split off a three-minute file
    out = []
    for r in keep:
        p = out[-1] if out else None
        if (p and r["artist"] == p["artist"] and not r.get("song") and not p.get("song")
                and wwoz.is_marker(r) and wwoz.is_marker(p)
                and (int(r["offset_seconds"]) - int(p["offset_seconds"]) < 900
                     or (is_show_marker(r) and is_show_marker(p)))):
            continue
        out.append(r)
    wwoz.write_tracklist(path, out)
