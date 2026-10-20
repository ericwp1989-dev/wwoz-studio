"""A stand-in for WWOZ's two-week archive, for trying the downloads without wwoz.org: an
archive page listing made-up shows (or a saved copy of the real page), and their mp3s,
served from this computer. wwoz.py reads it when WWOZ_ARCHIVE_URL points at it.

    srv, url = serve(page(shows), audio_dir)     # shows: show(...) dicts
    ... run wwoz.py or the app with WWOZ_ARCHIVE_URL=url ...
    srv.shutdown()
"""
import html
import http.server
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote


def show(start, title, slug):
    """One archive entry: start is when it aired (a Central datetime), slug the end of the
    archive's file name ('soul-power-with-soul-sister')."""
    return {"start": start, "title": title, "filename": f"{start:%Y%m%d%H%M%S}-1-100-{slug}.mp3"}


def page(shows):
    """An archive page listing `shows`, laid out the way wwoz.org lays out its own (the
    parts fetch_archive_shows reads), padded to the 20 entries it expects at least."""
    def build(audio_url):
        items = []
        for s in shows:
            label = s["start"].strftime("%I%p").lstrip("0")
            items.append(f'<li class="list-group-item"><div class="show-title">{html.escape(s["title"])}</div>'
                         f'<a href="/show.php?date={s["start"]:%Y-%m-%d}&amp;time={label}">listen</a>'
                         f'<button data-stream="{audio_url}/{s["filename"]}"></button></li>')
        items += ['<li class="list-group-item"><div>(no show)</div></li>'] * max(0, 22 - len(items))
        return "<html><body><ul>" + "\n".join(items) + "</ul></body></html>"
    return build


def saved_page(text):
    """A saved copy of wwoz.org's archive page, its mp3 links pointed at this computer."""
    return lambda audio_url: re.sub(r'data-stream="[^"]*/([^"/]+)"', lambda m: f'data-stream="{audio_url}/{m.group(1)}"',
                                    text)


def live_page(events):
    """WWOZ's Live Event Broadcasts calendar listing `events` ({'title', 'start', 'end',
    'place'}, Central datetimes), laid out the way wwoz.org lays out its own."""
    def clock(t):
        return t.strftime("%I:%M%p").lstrip("0").lower()
    blocks = []
    for n, e in enumerate(events):
        when = f"{e['start']:%A, %B} {e['start'].day}, {e['start'].year} - "
        when += (f'<span  class="date-display-range"><span  class="date-display-start">{clock(e["start"])}</span>'
                 f' to <span  class="date-display-end">{clock(e["end"])}</span></span>' if e.get("end")
                 else clock(e["start"]))
        blocks.append(f'<div id="node-{900000 + n}"\n     class="node node-event clearfix">\n<h2>\n'
                      f'  <a href="/events/{900000 + n}">{html.escape(e["title"])}</a>\n</h2>\n'
                      f'<div class="field field-name-field-event-date field-type-datestamp field-label-hidden">'
                      f'<div class="field-items"><div class="field-item even"><span  class="date-display-single">'
                      f'{when}</span></div></div></div><div class="field field-name-field-venue"><div id="node-1182" '
                      f'class="node node-organization clearfix">\n<h2>\n <a href="/organizations/x">'
                      f'{html.escape(e.get("place", ""))}</a>\n</h2></div></div></div>')
    return ('<html><body><h2 class="page-title"> Live Event Broadcasts </h2><ul class="nav nav-tabs">'
            '<li><a href="/calendar/live-broadcast">Upcoming</a></li></ul>' + "\n".join(blocks) + "</body></html>")


def serve(build, audio_dir, live=None, stream=None):
    """Serve the archive page `build(audio_url)` makes, and the mp3s in audio_dir; with
    `live`, a live broadcast calendar (a function returning the page, so a test can change
    it); with `stream`, an mp3 played as the live stream. Returns (server, the archive
    page's URL); server.requests lists the paths asked for, and the calendar is at
    server.live_url, the stream at server.stream_url."""
    audio_dir = Path(audio_dir)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            srv.requests.append(self.path)
            if self.path.startswith("/calendar/live-broadcast") and live:
                body = live().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/stream") and stream:
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.end_headers()
                data = Path(stream).read_bytes()
                try:
                    for _ in range(100):                # a live stream: it keeps coming, at its pace
                        for k in range(0, len(data), 1 << 14):
                            self.wfile.write(data[k:k + (1 << 14)])
                            time.sleep(0.05)
                except OSError:                         # the recorder hung up
                    pass
                return
            if self.path.startswith("/listen/archive"):
                body = build(f"{base}/audio").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            f = audio_dir / unquote(self.path.rsplit("/", 1)[-1])
            if not self.path.startswith("/audio/") or not f.is_file():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(f.stat().st_size))
            self.end_headers()
            with open(f, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    self.wfile.write(chunk)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    srv.requests = []
    base = f"http://127.0.0.1:{srv.server_port}"
    srv.live_url, srv.stream_url = f"{base}/calendar/live-broadcast", f"{base}/stream"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"{base}/listen/archive/"


def tone(ffmpeg, path, seconds=60):
    """A short mp3 to stand in for a broadcast."""
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-b:a", "64k", "-y", str(path)], check=True)
    return path
