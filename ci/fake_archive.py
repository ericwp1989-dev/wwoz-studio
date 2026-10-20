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


def serve(build, audio_dir):
    """Serve the archive page `build(audio_url)` makes, and the mp3s in audio_dir.
    Returns (server, the archive page's URL); server.requests lists the paths asked for."""
    audio_dir = Path(audio_dir)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            srv.requests.append(self.path)
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
    srv.requests = []
    base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"{base}/listen/archive/"


def tone(ffmpeg, path, seconds=60):
    """A short mp3 to stand in for a broadcast."""
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-b:a", "64k", "-y", str(path)], check=True)
    return path
