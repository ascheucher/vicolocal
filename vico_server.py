"""Vicolocal dashboard server: live camera feeds over the LAN, no Vicohome app.

The browser does WebRTC directly with the cameras (hardware H.264 decode, media straight
over the LAN); this server only serves the UI, lists cameras + thumbnails from the cloud,
and bridges signaling (standard WebRTC <-> the camera's compressed A4x protocol) over a
websocket per open stream (see CameraLink in vico_signaling.py).

    .venv/bin/python vico_server.py [--host 0.0.0.0] [--port 8080]
"""
import argparse
import asyncio
import html
import json
import os
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from vico_api import VicoCloud
from vico_archive import archive_file, list_archive
from vico_signaling import CameraError, CameraLink

__version__ = "0.2.1"

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
CACHE_TTL = 1800  # 30 min: device list + thumbnails

# Everything a deployer can tune via environment (for the container).
ACCOUNT = os.environ.get("VICO_ACCOUNT", "2")
CONFIG = {
    "brand": os.environ.get("VICO_TITLE", "Vicolocal"),
    "tagline": os.environ.get("VICO_TAGLINE", "LAN camera feeds"),
    "theme": os.environ.get("VICO_THEME", "dark"),            # default theme
    "accent": os.environ.get("VICO_ACCENT") or None,          # override accent color
    "accent2": os.environ.get("VICO_ACCENT2") or None,        # override secondary accent
    "simple": os.environ.get("VICO_SIMPLE", "") in ("1", "true", "yes", "on"),  # kid/kiosk mode
    "batteryWarn": os.environ.get("VICO_BATTERY_WARN", "1") not in ("0", "false", "no", "off"),
    # live-view camera controls: pan/tilt, white light, talk (MagicPix is viewer-side, always on)
    "controls": os.environ.get("VICO_CONTROLS", "1") not in ("0", "false", "no", "off"),
    # local archive of downloaded clips (see vico_archive.py / vico_pull.py); off unless a directory is set
    "archive": bool(os.environ.get("VICO_ARCHIVE_DIR")),
}
ARCHIVE_DIR = Path(os.environ["VICO_ARCHIVE_DIR"]).resolve() if CONFIG["archive"] else None

# Cache-busting token for CSS/JS/icons; changes each container start unless pinned via env
# (so a browser like the Fire's doesn't keep serving a stale title/favicon after a redeploy).
ASSET_V = os.environ.get("VICO_ASSET_V") or str(int(time.time()))


class Catalog:
    """Caches the device list and cloud thumbnails so we don't hammer the cloud."""
    def __init__(self):
        self._devices = []
        self._thumbs = {}      # serial -> {url, time}
        self._at = 0
        self._lock = asyncio.Lock()

    async def get(self, force=False):
        async with self._lock:
            if force or time.time() - self._at > CACHE_TTL or not self._devices:
                await asyncio.get_running_loop().run_in_executor(None, self._refresh)
            return self._devices

    def _refresh(self):
        vc = VicoCloud.from_env_file(account=ACCOUNT)
        self._devices = vc.devices()
        try:
            for t in vc.cover_images():
                self._thumbs[t.get("serialNumber")] = {
                    "url": t.get("lastPushImageUrl"), "time": t.get("lastPushTime")}
        except Exception:  # noqa: BLE001
            pass
        self._at = time.time()

    def accel(self, serial):
        """Whether the camera takes the compressed (RTC connect acceleration) offer; older
        firmware doesn't list it and only answers a plain one. Unknown cameras get compressed."""
        d = next((d for d in self._devices if d.get("serialNumber") == serial), None)
        if d is None:
            return True
        return ((d.get("deviceSupport") or {}).get("supportRtcConnectAcceleration") or 0) >= 1

    def view(self):
        """Public shape for the UI (no tokens/secrets)."""
        out = []
        for d in self._devices:
            sn = d.get("serialNumber")
            th = self._thumbs.get(sn, {})
            # isCharging: 0 = running on battery (streaming drains it), 1/2 = charging/plugged in.
            charging = d.get("isCharging") not in (0, None)
            support = d.get("deviceSupport") or {}
            out.append({
                "serial": sn,
                "name": d.get("deviceName") or sn,
                "model": d.get("modelNo"),
                "online": bool(d.get("online")),
                "battery": d.get("batteryLevel"),
                "charging": charging,
                "onBattery": not charging,
                "thumbUrl": th.get("url"),
                "thumbTime": th.get("time"),
                "canRotate": bool(support.get("canRotate")),          # pan/tilt motor
                "whiteLight": bool(support.get("supportWhiteLight")),  # switchable spotlight
            })
        return out


catalog = Catalog()


async def api_config(request):
    return web.json_response(CONFIG)


async def api_cameras(request):
    await catalog.get(force=request.query.get("refresh") == "1")
    return web.json_response(catalog.view())


def _fetch_clips(serial, days):
    now = int(time.time())
    vc = VicoCloud.from_env_file(account=ACCOUNT)
    evs = vc.library(serials=[serial] if serial else None,
                     start=now - days * 86400, end=now, offset=0, limit=60)
    out = []
    for e in evs:
        if serial and e.get("serialNumber") != serial:   # endpoint doesn't always filter
            continue
        out.append({
            "trace": e.get("traceId"),
            "serial": e.get("serialNumber"),
            "name": e.get("deviceName"),
            "time": e.get("timestamp"),
            "duration": round(e.get("period") or 0, 1),
            "size": e.get("fileSize"),
            "thumb": e.get("imageUrl"),
            "video": e.get("videoUrl"),       # HLS .m3u8 (CORS * -> browser plays directly)
            "tags": [t for t in (e.get("tags") or "").split(",") if t],  # actual detections
        })
    return out


async def api_clips(request):
    serial = request.query.get("serial")
    days = max(1, min(30, int(request.query.get("days", "3"))))
    try:
        clips = await asyncio.get_running_loop().run_in_executor(None, _fetch_clips, serial, days)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response(clips)


async def api_clip_download(request):
    """Remux the HLS clip to mp4 with ffmpeg and hand it back as a download."""
    import shutil
    import tempfile
    from urllib.parse import urlparse
    url = request.query.get("url", "")
    name = (request.query.get("name") or "clip").replace('"', "") + ".mp4"
    if urlparse(url).hostname not in ("api-us.vicohome.io",):
        return web.Response(status=400, text="invalid url")
    if not shutil.which("ffmpeg"):
        return web.Response(status=501, text="ffmpeg not installed")
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", url, "-c", "copy", "-movflags", "+faststart", tmp.name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=120)
        if proc.returncode != 0 or not os.path.getsize(tmp.name):
            return web.Response(status=502, text="remux failed")
        data = Path(tmp.name).read_bytes()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return web.Response(body=data, content_type="video/mp4",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def api_archive(request):
    """Archived clips on disk, newest first; no cloud call. [] unless VICO_ARCHIVE_DIR is set."""
    files = await asyncio.get_running_loop().run_in_executor(None, list_archive, ARCHIVE_DIR)
    return web.json_response(files)


async def api_archive_file(request):
    """One archived clip or thumbnail (`path` as listed by /api/archive); ranges work, so the
    browser can seek. Anything outside the archive directory is a 404."""
    path = archive_file(ARCHIVE_DIR, request.query.get("path", ""))
    if path is None:
        return web.Response(status=404, text="no such clip")
    return web.FileResponse(path)


async def ws_stream(request):
    """Bridge one browser stream to a camera."""
    serial = request.match_info["serial"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    try:
        await catalog.get()
    except Exception as e:  # noqa: BLE001
        print(f"vico: camera list failed: {e}", flush=True)
    link = CameraLink(serial, account=ACCOUNT, on_log=lambda m: print(m, flush=True),
                      accel=catalog.accel(serial))

    async def to_browser(obj):
        if not ws.closed:
            await ws.send_str(json.dumps(obj))

    async def send_log(msg):
        await to_browser({"type": "log", "msg": msg})

    link._log = lambda m: asyncio.ensure_future(send_log(m)) or print(m, flush=True)
    link.on_camera_candidate = lambda c: asyncio.ensure_future(
        to_browser({"type": "candidate", **c}))
    link.on_closed = lambda reason: asyncio.ensure_future(
        to_browser({"type": "closed", "reason": reason}))

    try:
        await link.connect()
        await to_browser({"type": "ready"})   # camera joined; browser may send its offer
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            t = data.get("type")
            if t == "offer":
                answer = await link.negotiate(data["sdp"])
                await to_browser({"type": "answer", "sdp": answer, "scp": link.scp})
            elif t == "candidate":
                await link.add_browser_candidate(
                    data["candidate"], data.get("sdpMid"), data.get("sdpMLineIndex"))
            elif t == "bye":
                break
    except CameraError as e:
        await to_browser({"type": "error", "msg": str(e)})
    except Exception as e:  # noqa: BLE001
        await to_browser({"type": "error", "msg": f"bridge error: {e}"})
    finally:
        await link.close()
        if not ws.closed:
            await ws.close()
    return ws


_index_html = None


def render_index():
    """index.html with the configured brand as the <title> and a cache-bust token,
    so bookmarks / home-screen shortcuts pick up the real name (e.g. "Cat Cams")."""
    global _index_html
    if _index_html is None:
        tpl = (STATIC / "index.html").read_text(encoding="utf-8")
        _index_html = tpl.replace("__BRAND__", html.escape(CONFIG["brand"])).replace("__V__", ASSET_V)
    return _index_html


async def index(request):
    return web.Response(text=render_index(), content_type="text/html")


async def manifest(request):
    """Web app manifest so Android/Fire "Add to Home Screen" uses the brand name + icon."""
    return web.json_response({
        "name": CONFIG["brand"],
        "short_name": CONFIG["brand"],
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0b0f14",
        "theme_color": "#0b0f14",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }, content_type="application/manifest+json")


async def favicon(request):
    return web.FileResponse(STATIC / "favicon.ico")


async def apple_touch_icon(request):
    return web.FileResponse(STATIC / "apple-touch-icon.png")


def make_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/manifest.webmanifest", manifest)
    app.router.add_get("/favicon.ico", favicon)              # Fire/Silk request these at the root
    app.router.add_get("/apple-touch-icon.png", apple_touch_icon)
    app.router.add_get("/apple-touch-icon-precomposed.png", apple_touch_icon)
    app.router.add_get("/api/config", api_config)
    app.router.add_get("/api/cameras", api_cameras)
    app.router.add_get("/api/clips", api_clips)
    app.router.add_get("/api/clip/download", api_clip_download)
    app.router.add_get("/api/archive", api_archive)
    app.router.add_get("/api/archive/file", api_archive_file)
    app.router.add_get("/ws/{serial}", ws_stream)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("VICO_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("VICO_PORT", "8080")))
    a = ap.parse_args()
    print(f"vicolocal dashboard on http://{a.host}:{a.port}", flush=True)
    web.run_app(make_app(), host=a.host, port=a.port, print=None)
