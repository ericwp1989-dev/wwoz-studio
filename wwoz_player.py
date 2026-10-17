"""WWOZ Studio's sound: plays the recordings and the live stream, without VLC.

The ffmpeg program, which WWOZ Studio already uses to cut recordings, reads a file or
the stream and sends it down a pipe as plain 16-bit stereo; miniaudio plays that on the
computer's speakers through the system's own sound (WASAPI on Windows, Core Audio on a
Mac), on the default output, so a switch to headphones takes the sound along. It all
runs in a small helper process of its own: Python lets one thread of a program run
Python code at a time, so if the player lived inside the app, the app's own busy work
could make the sound skip.

The app uses Player: open(), open_stream(), play(), pause(), stop(), seek(), set_volume(),
and reads state, position and is_playing. Measured against VLC on 20-hour recordings and
the live stream in notes/2026-10-17 playback test results.md.

    python wwoz_player.py --serve FFMPEG FFPROBE    the helper process (the app starts it)
"""

import array
import collections
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

RATE = 48000                 # what the speakers get, whatever the file holds
FRAME_BYTES = 4              # 16-bit stereo
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
PIPE_BYTES = 1 << 20         # room in the pipe from ffmpeg: about five seconds of sound
READ_BYTES = 1 << 18         # taken from it at a time: up to about 1.4 seconds
# FFmpeg reconnecting a web stream that drops, and how long a silent connection may hang
# (in microseconds) before it gives up and connects again
LIVE_FLAGS = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
              "-reconnect_delay_max", "10", "-rw_timeout", "15000000"]
# A recording of the stream has no table of contents, and FFmpeg's usual way to find a
# time in one reads forward through the file: 10 seconds or more for a far jump in a
# 20-hour recording. "fastseek" goes straight to the same share of the file, as VLC
# does, which is exact at a steady bitrate.
FAST_FLAGS = ["-fflags", "+fastseek"]


# ------------------------------------------------------------------ helpers --

def big_pipe():
    """A pipe that holds about five seconds of sound. Windows' usual pipe holds 4 KB,
    21 ms of sound, which starved the reader whenever other threads were busy; with
    room for seconds, each turn collects everything ffmpeg has made meanwhile."""
    if os.name == "nt":
        import _winapi
        import msvcrt
        r, w = _winapi.CreatePipe(None, PIPE_BYTES)
        return msvcrt.open_osfhandle(r, os.O_RDONLY), msvcrt.open_osfhandle(w, 0)
    r, w = os.pipe()
    try:
        import fcntl
        fcntl.fcntl(w, fcntl.F_SETPIPE_SZ, PIPE_BYTES)                # Linux only
    except (AttributeError, OSError):
        pass
    return r, w


def is_vbr_mp3(path):
    """True when an mp3 says it is variable-bitrate: a Xing or VBRI header in its first
    frame, after any ID3 tag. Jumping the fast way in such a file lands seconds off (30
    seconds, 10 hours into a test recording, and VLC lands the same), so the exact way
    is used there: right to the millisecond, though a far jump 20 hours in takes about
    two seconds. WWOZ's archive and stream are a steady 128 kbps."""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            skip = 0
            if head[:3] == b"ID3":
                skip = 10 + ((head[6] & 0x7f) << 21 | (head[7] & 0x7f) << 14
                             | (head[8] & 0x7f) << 7 | (head[9] & 0x7f))
            f.seek(skip)
            frame = f.read(4096)
        return b"Xing" in frame or b"VBRI" in frame
    except OSError:
        return False


def drain(pipe, keep):
    """Read a program's messages as they come, keeping the last few. Left unread, the
    pipe (4 KB on Windows) fills after a few dozen error lines from a damaged file or a
    glitchy stream, ffmpeg stops to wait for room, and the sound freezes."""
    try:
        for line in iter(pipe.readline, b""):
            keep.append(line)
    except (OSError, ValueError):
        pass


def open_device(miniaudio, period_ms):
    """The speakers, through miniaudio, which makes a "context" (with a thread of its
    own) for every device and never frees it: each retry while the speakers were
    unplugged left a thread behind. So the context is kept here and freed with the
    device, or at once when the device won't open."""
    made = []

    class Device(miniaudio.PlaybackDevice):
        def _make_context(self, *args, **kw):
            made.append(super()._make_context(*args, **kw))
            return made[-1]
    try:
        return Device(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2, sample_rate=RATE,
                      buffersize_msec=period_ms, app_name="WWOZ Studio"), made[0]
    except Exception:
        for ctx in made:
            miniaudio.lib.ma_context_uninit(ctx)
        raise


# ------------------------------------------------------------------- engine --

class Engine:
    """The player itself, inside the helper process.

    A reader thread keeps about two seconds of sound waiting, each piece labeled with
    the time in the recording it came from; miniaudio takes the next 50 ms whenever
    the speakers need it, so the clock is the label on the sound just played and stays
    right after a jump."""

    def __init__(self, ffmpeg, ffprobe, period_ms=50, buffer_sec=2.0):
        import miniaudio
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.volume = 1.0           # 0 to 1
        self.state = "stopped"      # stopped, playing, paused, ended, error
        self.error = ""
        self.duration = 0.0
        self.live = False
        self.gaps = 0               # times the speakers wanted sound that wasn't ready
        self._lock = threading.Lock()
        self._room = threading.Condition(self._lock)   # the reader sleeps on it while the line is full
        self._chunks = collections.deque()             # [time in the recording, bytes]
        self._bytes = 0
        self._limit = int(buffer_sec * RATE) * FRAME_BYTES
        self._prebuffer = 0
        self._filling = False       # just opened or jumped: waiting for sound, not short of it
        self._pos = 0.0
        self._eof = False
        self._gen = 0               # bumped by every open and stop, so an old reader quits
        self._seek = None
        self._vbr = False
        self._got_at = 0.0          # when ffmpeg last sent sound (check_live watches it)
        self._heard = False         # whether this stream has sent any sound yet
        self._live_proc = None
        self._last_err = ""         # ffmpeg's last word on a stream connection that ended
        self._lib = miniaudio.lib
        self.device, self._context = open_device(miniaudio, period_ms)
        self.backend = self.device.backend
        self._feed = self._feeder()
        next(self._feed)
        # The speakers run only while something plays (see sync_device): a running
        # output keeps the computer from going to sleep, even when all it plays is silence.
        self._dev_lock = threading.Lock()

    def sync_device(self):
        """Start the speakers when there is something to play and stop them when there
        isn't (paused, stopped, at the end, or an error)."""
        with self._dev_lock:
            want = self.state == "playing"
            if want and not self.device.running:
                try:
                    self.device.start(self._feed)
                except Exception as e:
                    self.error = f"the speakers or headphones wouldn't start ({e})"
                    self.state = "error"
            elif not want and self.device.running:
                try:
                    self.device.stop()
                except Exception:
                    pass

    def open(self, source, live=False, start=0.0):
        """Play a file (from `start` seconds) or a web stream from the top."""
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._reset(0.0 if live else start, prebuffer=1.0 if live else 0.25)
        self.live, self.error, self.duration = live, "", 0.0
        self._got_at, self._heard, self._last_err = time.time(), False, ""
        self.state = "playing"
        threading.Thread(target=self._reader, args=(gen, str(source), live, start), daemon=True).start()

    def seek(self, seconds):
        if self.live or self.state in ("stopped", "error"):
            return
        if self.duration:
            seconds = min(seconds, self.duration - 0.5)
        seconds = max(0.0, seconds)
        with self._lock:
            self._seek = seconds
            self._reset(seconds, prebuffer=0.25)
        if self.state == "ended":
            self.state = "playing"

    def pause(self):
        if self.state == "playing":
            self.state = "paused"

    def play(self):
        if self.state == "paused":
            self.state = "playing"
        elif self.state == "ended":
            self.seek(0)

    def stop(self):
        with self._lock:
            self._gen += 1
            self._reset(0.0, prebuffer=0)
        self.state = "stopped"

    def check_live(self):
        """Give up on a live stream that has sent nothing for a while, and say so: with
        no internet, ffmpeg retries quietly for 10 to 40 seconds a time, and the app
        would show "playing" over silence forever. 20 seconds before any sound arrives;
        45 once it has played, to ride out a short drop."""
        if not (self.live and self.state == "playing"):
            return
        if time.time() - self._got_at < (45 if self._heard else 20):
            return
        with self._lock:
            self._gen += 1                       # the reader quits when its ffmpeg ends
            self._reset(0.0, prebuffer=0)
        proc = self._live_proc
        if proc:
            proc.kill()
        self.error = ("WWOZ's live stream couldn't be reached. Check that this computer is online, "
                      "then click Listen live again."
                      + (f" (ffmpeg said: {self._last_err})" if self._last_err else ""))
        self.state = "error"

    def close(self):
        self.stop()
        self.device.close()
        self._lib.ma_context_uninit(self._context)

    @property
    def position(self):
        """Seconds into the recording of the sound just handed to the speakers."""
        return self._pos

    def _reset(self, pos, prebuffer):
        """Empty the waiting sound (call with the lock held)."""
        self._chunks.clear()
        self._bytes = 0
        self._eof = False
        self._pos = pos
        self._filling = True
        self._prebuffer = int(prebuffer * RATE) * FRAME_BYTES
        self._room.notify_all()                  # a reader waiting for room: look again

    def _feeder(self):
        """miniaudio's side: hand over the next `frames` of sound, or silence."""
        frames = yield b""
        while True:
            out = self._take(frames * FRAME_BYTES) if self.state == "playing" else b""
            frames = yield out

    def _take(self, want):
        parts, got = [], 0
        with self._lock:
            if self._filling and self._bytes < self._prebuffer and not self._eof:
                return b""                       # still gathering sound after open or a jump
            while got < want and self._chunks:
                start, data = self._chunks[0]
                n = min(len(data), want - got)
                parts.append(data[:n])
                got += n
                if n == len(data):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = [start + n / FRAME_BYTES / RATE, data[n:]]
                self._pos = start + n / FRAME_BYTES / RATE
                self._bytes -= n
            filling, eof = self._filling, self._eof
            if got:
                self._filling = False
            if self._bytes < self._limit // 2:
                self._room.notify()              # wake the reader once half the line has played
        if got < want:
            if eof and not self._chunks:
                if self.state == "playing":
                    self.state = "ended"
            elif not filling:
                self.gaps += 1
        data = b"".join(parts)
        v = self.volume
        if v >= 0.999 or not data:
            return data
        if v <= 0.001:
            return bytes(len(data))
        gain = int(v * v * 32768)                # squared: halfway on the slider sounds halfway
        samples = array.array("h", data)
        return array.array("h", [s * gain >> 15 for s in samples]).tobytes()

    def _put(self, gen, t0, data):
        """Add sound to the waiting line, sleeping while it is full. False when an open,
        stop or jump has made this sound stale."""
        with self._lock:
            while True:
                if gen != self._gen or self._seek is not None:
                    return False
                if self._bytes < self._limit:
                    self._chunks.append([t0, data])
                    self._bytes += len(data)
                    return True
                self._room.wait(0.5)

    def _command(self, source, live, start):
        cmd = [self.ffmpeg, "-nostdin", "-v", "error"]
        if live:
            cmd += LIVE_FLAGS
        else:
            if source.lower().endswith(".mp3") and not self._vbr:
                cmd += FAST_FLAGS
            if start:
                cmd += ["-ss", f"{start:.3f}"]
        return cmd + ["-i", source, "-vn", "-sn", "-f", "s16le", "-ac", "2", "-ar", str(RATE), "pipe:1"]

    def _probe(self, gen, source):
        try:
            r = subprocess.run([self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", source], capture_output=True, text=True,
                               timeout=60, creationflags=NOWIN)
            if gen == self._gen:
                self.duration = float(r.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass

    def _reader(self, gen, source, live, start):
        self._vbr = not live and source.lower().endswith(".mp3") and is_vbr_mp3(source)
        if not live:
            threading.Thread(target=self._probe, args=(gen, source), daemon=True).start()
        pos = 0.0 if live else (start or 0.0)
        while gen == self._gen:
            r, w = big_pipe()
            try:
                proc = subprocess.Popen(self._command(source, live, pos), stdout=w,
                                        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                        creationflags=NOWIN)
            except OSError as e:
                os.close(r)
                self.error, self.state = f"couldn't start ffmpeg: {e}", "error"
                return
            finally:
                os.close(w)                       # ffmpeg holds its own copy; EOF comes when it ends
            errs = collections.deque(maxlen=20)
            talk = threading.Thread(target=drain, args=(proc.stderr, errs), daemon=True)
            talk.start()
            if live:
                self._live_proc = proc
            t, got, jump, rest = pos, 0, None, b""
            try:
                while gen == self._gen:
                    with self._lock:
                        jump, self._seek = self._seek, None
                    if jump is not None:
                        break
                    data = os.read(r, READ_BYTES)
                    if not data:
                        break
                    data, rest = rest + data, b""
                    whole = len(data) - len(data) % FRAME_BYTES
                    data, rest = data[:whole], data[whole:]
                    got += len(data)
                    self._got_at, self._heard = time.time(), True
                    if not self._put(gen, t, data):
                        continue                          # a jump came in: handled at the top
                    t += len(data) / FRAME_BYTES / RATE
            finally:
                proc.kill()
                proc.wait()
                talk.join(timeout=2)
                err = b"".join(errs).decode(errors="replace").strip()
                os.close(r)
            if gen != self._gen:
                return
            if jump is not None:
                pos = jump
                continue
            if live:                                      # the stream dropped: connect again
                if err:                                   # (check_live gives up if it stays down)
                    self._last_err = err.splitlines()[-1][:200]
                time.sleep(1)
                continue
            if not got and err:
                self.error, self.state = err.splitlines()[-1][:300], "error"
                return
            with self._lock:                              # the end of the file
                if gen == self._gen and self._seek is None:
                    self._eof = True
            while gen == self._gen:                       # a jump back after the end reopens it
                with self._lock:
                    jump, self._seek = self._seek, None
                if jump is not None:
                    pos = jump
                    break
                time.sleep(0.05)


def serve(ffmpeg, ffprobe):
    """The helper process: commands arrive on stdin and the state goes out on stdout,
    one JSON line each. It ends when the app closes its stdin, so it never outlives it.

    If the sound output can't be opened (speakers asleep, unplugged or busy with
    another program), it tries again every two seconds and whenever a recording is
    played, and writes why to stderr, which the app sends to its error log."""
    out_lock = threading.Lock()
    eng_lock = threading.Lock()
    status = {"ack": 0}
    have = {"eng": None, "problem": "", "next_try": 0.0, "volume": 1.0, "said": ""}

    def engine(now=False):
        """The engine, opening the sound output first if it isn't open yet."""
        with eng_lock:
            if have["eng"] is None and (now or time.time() >= have["next_try"]):
                try:
                    have["eng"] = Engine(ffmpeg, ffprobe)
                    have["eng"].volume = have["volume"]
                    have["problem"] = ""
                    if have["said"]:
                        print(f"{time.ctime()}  sound player: the sound output opened after all",
                              file=sys.stderr, flush=True)
                except ImportError:
                    setup = "Setup (Mac).command" if sys.platform == "darwin" else "Setup.cmd"
                    have["problem"] = (f"the miniaudio package that plays the sound isn't installed. "
                                       f"Double-click {setup} in the program's folder to add it.")
                    have["next_try"] = float("inf")
                except Exception as e:
                    have["problem"] = ("the computer's speakers or headphones wouldn't open. Check that "
                                       "they're on and connected; WWOZ Studio keeps trying.")
                    have["detail"] = repr(e)
                    have["next_try"] = time.time() + 2
                if have["problem"] and have["problem"] != have["said"]:
                    have["said"] = have["problem"]                # say each new problem once
                    print(f"{time.ctime()}  sound player: {have['problem']} ({have.get('detail', '')})",
                          file=sys.stderr, flush=True)
            return have["eng"]

    def report():
        eng = engine()
        if eng:
            status.update(state=eng.state, pos=round(eng.position, 3), dur=eng.duration,
                          error=eng.error, backend=eng.backend, gaps=eng.gaps)
        else:
            status.update(state="error", error=have["problem"], backend="")
        line = json.dumps(status) + "\n"
        with out_lock:
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except (OSError, ValueError):
                os._exit(0)                               # the app has gone

    def ticker():
        while True:
            eng = have["eng"]
            if eng:
                eng.check_live()
                eng.sync_device()                         # speakers off once a show ends or fails
            report()
            time.sleep(0.1 if eng and eng.state == "playing" else 0.5)
    threading.Thread(target=ticker, daemon=True).start()

    for line in sys.stdin:
        try:
            cmd = json.loads(line)
        except ValueError:
            continue
        do = cmd.get("do")
        if do == "quit":
            break
        if do == "volume":
            have["volume"] = max(0.0, min(1.0, float(cmd["value"])))
        eng = engine(now=do in ("open", "play"))         # pressing play tries the speakers again
        if eng:
            if do == "open":
                eng.open(cmd["source"], live=cmd.get("live", False), start=cmd.get("start", 0.0))
            elif do == "seek":
                eng.seek(float(cmd["to"]))
            elif do == "pause":
                eng.pause()
            elif do == "play":
                eng.play()
            elif do == "stop":
                eng.stop()
            elif do == "volume":
                eng.volume = have["volume"]
            eng.sync_device()                             # speakers on to play, off when not
        status["ack"] = cmd.get("id", status["ack"])
        report()
    if have["eng"]:
        have["eng"].close()


# -------------------------------------------------------------- the app's side --

class Player:
    """What the app talks to: starts the helper process and passes commands to it. It
    shows a command's result at once (a jump moves the clock straight away) and takes
    the helper's word once the helper has caught up."""

    def __init__(self, ffmpeg, ffprobe, errors=None):
        self._args = [sys.executable, str(Path(__file__).resolve()), "--serve", ffmpeg, ffprobe]
        self._errors = errors           # where the helper's own errors go (the app's error log)
        self.state = "stopped"
        self.position = 0.0
        self.duration = 0.0
        self.error = ""
        self.backend = ""
        self.gaps = 0
        self._id = 0
        self._hold = 0                  # the helper's word counts again from this command on
        self._lock = threading.RLock()
        self._closing = False
        self._proc = None
        self._volume = None             # sent again to a helper started after one stopped
        self._start()

    @property
    def is_playing(self):
        return self.state == "playing"

    def open(self, path, start=0.0):
        self._do("open", position=start, state="playing", source=str(path), start=float(start))

    def open_stream(self, url):
        self._do("open", position=0.0, state="playing", source=url, live=True)

    def play(self):
        self._do("play", state="playing")

    def pause(self):
        self._do("pause", state="paused")

    def stop(self):
        self._do("stop", state="stopped")

    def seek(self, seconds):
        self._do("seek", position=max(0.0, seconds), to=max(0.0, float(seconds)))

    def set_volume(self, fraction):
        self._volume = float(fraction)
        self._send({"do": "volume", "value": self._volume})

    def close(self):
        self._closing = True
        try:
            self._send({"do": "quit"})
            self._proc.wait(timeout=2)
        except Exception:
            pass
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def _start(self):
        self._proc = subprocess.Popen(self._args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=self._errors or subprocess.DEVNULL, creationflags=NOWIN,
                                      text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._listen, args=(self._proc,), daemon=True).start()

    def _do(self, what, position=None, state=None, **fields):
        with self._lock:
            n = self._send(dict(fields, do=what))
            self._hold = n
            if position is not None:
                self.position = position
            if state is not None:
                self.state = state
                self.error = ""

    def _send(self, cmd):
        with self._lock:
            if self._proc.poll() is not None and not self._closing:
                self._start()                     # it stopped: start a new one
                if self._volume is not None and cmd.get("do") != "volume":
                    try:                          # at the volume the slider shows, not full
                        self._proc.stdin.write(json.dumps({"do": "volume", "value": self._volume}) + "\n")
                    except (OSError, ValueError):
                        pass
            self._id += 1
            cmd["id"] = self._id
            try:
                self._proc.stdin.write(json.dumps(cmd) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                pass
            return self._id

    def _listen(self, proc):
        for line in proc.stdout:
            try:
                st = json.loads(line)
            except ValueError:
                continue
            self.backend = st.get("backend", self.backend)
            self.gaps = st.get("gaps", self.gaps)
            if st.get("ack", 0) >= self._hold:
                self.state = st.get("state", self.state)
                self.position = st.get("pos", self.position)
                self.duration = st.get("dur") or self.duration
                self.error = st.get("error", "")
        if not self._closing and proc is self._proc:
            self.state, self.error = "error", "the sound helper stopped (see the error log)"


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--serve":
        serve(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
