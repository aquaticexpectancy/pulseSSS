"""pulseSSS - live heart rate from a Bluetooth heart-rate device (Fitbit Air) to OBS.

    python pulse.py                  connect to a device whose name contains "Fitbit Air"
    python pulse.py --name ""        connect to any heart-rate device
    python pulse.py --port 9000      serve the overlay on another port

Open http://127.0.0.1:8765 for the dashboard, /design for the overlay designer, and add
http://127.0.0.1:8765/overlay to OBS as a Browser Source. Every reading is written to
logs/hr_<start time>.csv. Overlay designs are kept in designs.json.
"""

import argparse
import asyncio
import csv
import datetime as dt
import json
import os
import re
import secrets
import sys
import time
from collections import deque
from pathlib import Path

import aiohttp
from aiohttp import web
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakBluetoothNotAvailableError, BleakError

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
LOG_DIR = ROOT / "logs"
DESIGNS_FILE = ROOT / "designs.json"
PRESETS_FILE = WEB_DIR / "presets.json"
PULSOID_WS = "wss://dev.pulsoid.net/api/v1/data/real_time?response_mode=text_plain_only_heart_rate"
TOKEN_RE = re.compile(r"^[\w.-]{8,200}$")


def parse_bpm(data: bytearray) -> int:
    # Heart Rate Measurement: flag bit 0 says whether the value is 8 or 16 bits.
    if data[0] & 0x01:
        return int.from_bytes(data[1:3], "little")
    return data[1]


def stamp(t: float) -> str:
    return dt.datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


APP_LOG = LOG_DIR / "pulsesss.log"


def say(text: str):
    now = dt.datetime.now()
    line = f"[{now:%Y-%m-%d %H:%M:%S}] {text}"
    print(f"[{now:%H:%M:%S}] {text}", flush=True)  # no-op under pythonw (tray / OBS), so also keep a file
    try:
        LOG_DIR.mkdir(exist_ok=True)
        if APP_LOG.exists() and APP_LOG.stat().st_size > 1_000_000:
            APP_LOG.replace(APP_LOG.with_suffix(".old.log"))
        with open(APP_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


class SessionLog:
    """One CSV per day, appended to. Wall-clock timestamps line up with OBS recording file names."""

    def __init__(self):
        LOG_DIR.mkdir(exist_ok=True)
        self._day = None
        self._file = None
        self.path = None
        self._open_for(dt.date.today())

    def _open_for(self, day: dt.date):
        if self._file:
            self._file.close()
        self._day = day
        self.path = LOG_DIR / f"hr_{day:%Y-%m-%d}.csv"
        new = not self.path.exists() or self.path.stat().st_size == 0
        self._file = open(self.path, "a", newline="", encoding="utf-8")
        self._csv = csv.writer(self._file)
        if new:
            self._csv.writerow(["timestamp", "unix_ms", "bpm", "event"])
            self._file.flush()

    def write(self, t: float, bpm: int | None = None, event: str = ""):
        day = dt.date.fromtimestamp(t)
        if day != self._day:
            self._open_for(day)
        self._csv.writerow([stamp(t), int(t * 1000), "" if bpm is None else bpm, event])
        self._file.flush()

    def close(self):
        if self._file:
            self._file.close()
            self._file = None


class Designs:
    """Named overlay designs plus which one /overlay shows by default."""

    def __init__(self):
        if DESIGNS_FILE.exists():
            try:
                self.doc = self._clean(json.loads(DESIGNS_FILE.read_text(encoding="utf-8")))
                return
            except (ValueError, OSError) as e:
                say(f"designs.json unreadable ({e}); starting from presets.")
        presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        self.doc = {"active": next(iter(presets)), "designs": presets}
        self.save()

    @staticmethod
    def _clean(doc) -> dict:
        if not isinstance(doc, dict) or not isinstance(doc.get("designs"), dict) or not doc["designs"]:
            raise ValueError("expected {active, designs}")
        designs = {str(k)[:60]: v for k, v in doc["designs"].items() if isinstance(v, dict)}
        if not designs:
            raise ValueError("no designs")
        active = doc.get("active")
        if active not in designs:
            active = next(iter(designs))
        return {"active": active, "designs": designs}

    def replace(self, doc):
        self.doc = self._clean(doc)
        self.save()

    def save(self):
        tmp = DESIGNS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.doc, indent=2), encoding="utf-8")
        os.replace(tmp, DESIGNS_FILE)


class Hub:
    """Current state plus every open overlay/dashboard websocket."""

    def __init__(self, log: SessionLog, designs: Designs):
        self.log = log
        self.designs = designs
        self.status = "starting"
        self.device = None
        self.bpm = None
        self.last_t = None
        self.history = deque(maxlen=600)  # ~10 minutes at 1 reading/s
        self.clients: set[web.WebSocketResponse] = set()
        self.reconnect = asyncio.Event()  # set to drop the current connection and start over
        self.stop = asyncio.Event()       # set to shut the whole app down
        self.hooks = {}                   # optional UI callbacks from the app window, e.g. "show"
        self.web = None                   # webshare.WebShare, set in run()
        self.settings = None              # webshare.Settings, set in run()
        self.source_changed = asyncio.Event()  # set to restart with the current source

    # ---------- source: Fitbit Air over Bluetooth, Pulsoid, or off ----------
    @property
    def source(self) -> str:
        return self.settings.source if self.settings else "air"

    def source_info(self) -> dict:
        s = self.settings
        return {"source": self.source, "pulsoid": bool(s and s.pulsoid_token), "phone": bool(s and s.phone_key)}

    def phone_link(self) -> str:
        """What the pairing QR code holds: opens the pulseSSS Phone app with the relay address and key."""
        from urllib.parse import quote
        relay = self.settings.web["url"].replace("wss://", "https://").replace("ws://", "http://").rsplit("/", 1)[0]
        return f"pulsesss://pair?relay={quote(relay, safe='')}&key={quote(self.settings.phone_key, safe='')}"

    def pair_phone(self, unpair: bool = False):
        self.settings.data["phone"]["key"] = "" if unpair else secrets.token_urlsafe(24)
        self.settings.save()
        say("Phone unpaired." if unpair else "New phone pairing key made.")
        if self.web:
            self.web.keys_changed()
        if unpair and self.source == "phone":
            self.source_changed.set()
        asyncio.create_task(self.send({"type": "source", **self.source_info()}))

    def set_source(self, source: str):
        from webshare import SOURCES
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        if source == self.source:
            return
        self.settings.data["source"] = source
        self.settings.save()
        say({"air": "Using the Fitbit Air.", "pulsoid": "Using Pulsoid.", "phone": "Using the phone app.",
             "off": "Disconnected by you."}[source])
        if self.web:
            self.web.keys_changed()  # phone mode needs the relay connection; also stops pushing our own readings
        self.source_changed.set()
        asyncio.create_task(self.send({"type": "source", **self.source_info()}))

    def set_pulsoid_token(self, token: str):
        token = token.strip()
        if token and not TOKEN_RE.match(token):
            raise ValueError("that doesn't look like a Pulsoid token")
        self.settings.data["pulsoid"]["token"] = token
        self.settings.save()
        say("Pulsoid token " + ("saved." if token else "removed."))
        if self.web:
            self.web.keys_changed()
        if self.source == "pulsoid":
            self.source_changed.set()
        asyncio.create_task(self.send({"type": "source", **self.source_info()}))

    def request_reconnect(self):
        if self.source == "air":
            self.reconnect.set()
        else:
            self.source_changed.set()

    def snapshot(self) -> dict:
        return {
            "type": "hello",
            "status": self.status,
            "device": self.device,
            "bpm": self.bpm,
            "t": None if self.last_t is None else int(self.last_t * 1000),
            "history": list(self.history),
            "log": str(self.log.path) if self.log else "",
            "designs": self.designs.doc,
            "web": self.web.status() if self.web else None,
            **self.source_info(),
        }

    async def send(self, msg: dict):
        text = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_str(text)
            except Exception:
                self.clients.discard(ws)

    def set_status(self, status: str, device: str | None = None):
        if status == self.status and device == self.device:
            return
        self.status, self.device = status, device
        self.log.write(time.time(), event=status + (f" {device}" if device else ""))
        say(status + (f" - {device}" if device else ""))
        asyncio.create_task(self.send({"type": "status", "status": status, "device": device}))
        if self.web:
            self.web.on_status(status)

    def reading(self, bpm: int):
        now = time.time()
        ms = int(now * 1000)
        self.bpm, self.last_t = bpm, now
        self.history.append([ms, bpm])
        self.log.write(now, bpm)
        asyncio.create_task(self.send({"type": "hr", "bpm": bpm, "t": ms}))
        if self.web:
            self.web.on_reading(bpm)


async def find_device(args):
    if args.address:
        return await BleakScanner.find_device_by_address(args.address, timeout=10)
    wanted = args.name.lower()

    def match(device, adv):
        name = (adv.local_name or device.name or "").lower()
        has_hr = HR_SERVICE in [u.lower() for u in adv.service_uuids]
        return has_hr and wanted in name

    return await BleakScanner.find_device_by_filter(match, timeout=10)


async def ble_loop(hub: Hub, args):
    retry = 2
    hinted = False
    while True:
        hub.set_status("scanning")
        try:
            device = await find_device(args)
            if device is None:
                if not hinted:
                    say("Not found yet. Turn on Share heart rate in Google Health and keep the Air nearby.")
                    hinted = True
                await asyncio.sleep(retry)
                retry = min(retry * 2, 10)
                continue
            hinted = False
            name = device.name or device.address
            hub.set_status("connecting", name)

            loop = asyncio.get_running_loop()
            dropped = asyncio.Event()

            async with BleakClient(
                device,
                disconnected_callback=lambda _: loop.call_soon_threadsafe(dropped.set),
                timeout=40,  # the Air "prepares to share" on each connect, up to 30 s per Google Health
            ) as client:
                await client.start_notify(HR_MEASUREMENT, lambda _, data: hub.reading(parse_bpm(data)))
                hub.set_status("connected", name)
                retry = 2
                started = time.time()
                hub.reconnect.clear()
                while not dropped.is_set():
                    try:
                        await asyncio.wait_for(dropped.wait(), timeout=2)
                    except TimeoutError:
                        pass
                    if hub.reconnect.is_set():
                        say("Reconnect requested.")
                        break
                    last = hub.last_t if hub.last_t and hub.last_t > started else started
                    if time.time() - last > args.stale:
                        say(f"No reading for {args.stale}s, reconnecting.")
                        break
        except BleakBluetoothNotAvailableError:
            say("Bluetooth is off on this PC. Turn it on in Windows settings.")
            retry = 5
        except (BleakError, OSError, TimeoutError) as e:
            say(f"Bluetooth error: {str(e) or type(e).__name__}")
        hub.set_status("disconnected")
        if hub.reconnect.is_set():
            hub.reconnect.clear()
            continue
        await asyncio.sleep(retry)


def parse_pulsoid(text: str) -> int | None:
    try:
        bpm = int(text.strip())
    except ValueError:
        try:
            bpm = int(json.loads(text)["data"]["heart_rate"])
        except (ValueError, KeyError, TypeError):
            return None
    return bpm if 20 < bpm < 260 else None


async def pulsoid_loop(hub: Hub, args):
    """Heart rate from Pulsoid: the phone app talks to the Air, pulseSSS reads Pulsoid's live feed."""
    backoff = 5
    async with aiohttp.ClientSession() as session:
        while True:
            token = hub.settings.pulsoid_token
            if not token:
                hub.set_status("no-token", "Pulsoid")
                await asyncio.Event().wait()  # restarted by set_pulsoid_token / set_source
            hub.set_status("connecting", "Pulsoid")
            try:
                async with session.ws_connect(PULSOID_WS, headers={"Authorization": f"Bearer {token}"},
                                              heartbeat=30) as ws:
                    backoff = 5
                    hub.set_status("waiting", "Pulsoid")
                    while True:
                        try:
                            msg = await ws.receive(timeout=args.stale)
                        except TimeoutError:
                            if hub.status == "connected":
                                hub.set_status("waiting", "Pulsoid")
                            continue
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            bpm = parse_pulsoid(msg.data)
                            if bpm:
                                hub.reading(bpm)
                                if hub.status != "connected":
                                    hub.set_status("connected", "Pulsoid")
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                            say("Pulsoid closed the connection.")
                            break
            except aiohttp.WSServerHandshakeError as e:
                if e.status in (401, 403):
                    say("Pulsoid refused the token. Make a new one at pulsoid.net/ui/keys.")
                    hub.set_status("bad-token", "Pulsoid")
                    await asyncio.Event().wait()
                say(f"Pulsoid answered {e.status}.")
            except (aiohttp.ClientError, OSError, TimeoutError) as e:
                say(f"Can't reach Pulsoid ({type(e).__name__}).")
            hub.set_status("disconnected", "Pulsoid")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def phone_loop(hub: Hub, args):
    """Heart rate from the pulseSSS Phone app, which posts to the relay; read back from its public feed."""
    backoff = 5
    async with aiohttp.ClientSession() as session:
        while True:
            if not hub.settings.phone_key:
                hub.set_status("no-phone", "Phone")
                await asyncio.Event().wait()  # restarted by pair_phone / set_source
            hub.set_status("connecting", "Phone")
            try:
                async with session.ws_connect(hub.settings.live_url(), heartbeat=30) as ws:
                    backoff = 5
                    hub.set_status("waiting", "Phone")
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            m = json.loads(msg.data)
                        except ValueError:
                            continue
                        if m.get("type") != "pulse":
                            continue
                        via = "Pulsoid" if m.get("via") == "pulsoid" else "Phone"
                        if m.get("online") and m.get("via") in ("phone", "pulsoid") and isinstance(m.get("bpm"), int):
                            hub.reading(m["bpm"])
                            if hub.status != "connected" or hub.device != via:
                                hub.set_status("connected", via)
                        elif hub.status == "connected":
                            hub.set_status("waiting", "Phone")
                    say("Relay feed closed.")
            except (aiohttp.ClientError, OSError, TimeoutError) as e:
                say(f"Can't reach the relay feed ({type(e).__name__}).")
            hub.set_status("disconnected", "Phone")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def source_loop(hub: Hub, args):
    """Run the selected source. Switching (or Disconnect) cancels the old one, which releases the Air."""
    while True:
        hub.source_changed.clear()
        source = hub.source
        if source == "air":
            job = ble_loop(hub, args)
        elif source == "pulsoid":
            job = pulsoid_loop(hub, args)
        elif source == "phone":
            job = phone_loop(hub, args)
        else:
            hub.set_status("paused")
            job = asyncio.Event().wait()
        task = asyncio.ensure_future(job)
        changed = asyncio.ensure_future(hub.source_changed.wait())
        try:
            done, _ = await asyncio.wait({task, changed}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (task, changed):
                t.cancel()
            await asyncio.gather(task, changed, return_exceptions=True)
        if task in done:
            task.result()


async def watch_pid(pid: int):
    """Return when process `pid` exits (used to stop together with OBS)."""
    if sys.platform != "win32":
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            await asyncio.sleep(2)
    import ctypes
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        say(f"Can't watch process {pid}; will keep running.")
        await asyncio.Event().wait()
    try:
        while k32.WaitForSingleObject(handle, 0) != 0:  # 0 = process has exited
            await asyncio.sleep(2)
    finally:
        k32.CloseHandle(handle)
    say("OBS closed, stopping.")


def build_app(hub: Hub, port: int) -> web.Application:
    allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def same_origin(request) -> bool:
        # Blocks other websites in your browser from talking to this local server.
        origin = request.headers.get("Origin")
        return origin is None or origin in allowed

    async def ws_handler(request):
        if not same_origin(request):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        hub.clients.add(ws)
        try:
            await ws.send_str(json.dumps(hub.snapshot()))
            async for _ in ws:
                pass
        finally:
            hub.clients.discard(ws)
        return ws

    async def get_designs(_):
        return web.json_response(hub.designs.doc)

    async def put_designs(request):
        if not same_origin(request):
            raise web.HTTPForbidden()
        try:
            hub.designs.replace(await request.json())
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))
        await hub.send({"type": "designs", "designs": hub.designs.doc})
        return web.json_response(hub.designs.doc)

    def page(name):
        async def handler(_):
            return web.FileResponse(WEB_DIR / name, headers={"Cache-Control": "no-store"})
        return handler

    @web.middleware
    async def no_cache(request, handler):
        resp = await handler(request)
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(middlewares=[no_cache])
    def local_post(fn):
        async def handler(request):
            if not same_origin(request):
                raise web.HTTPForbidden()
            return await fn(request)
        return handler

    async def show_window(_):
        show = hub.hooks.get("show")
        if not show:
            raise web.HTTPNotFound(text="no app window in this mode")
        show()
        return web.json_response({"ok": True})

    async def reconnect(_):
        hub.request_reconnect()
        return web.json_response({"ok": True})

    async def pair_phone(request):
        body = await request.json() if request.can_read_body else {}
        hub.pair_phone(unpair=bool(body.get("unpair")))
        return web.json_response(hub.source_info())

    async def phone_qr(_):
        if not hub.settings.phone_key:
            raise web.HTTPNotFound(text="not paired")
        import io
        import segno
        buf = io.BytesIO()
        segno.make(hub.phone_link(), error="m").save(buf, kind="svg", scale=6, border=2,
                                                      dark="#111111", light="#ffffff")
        return web.Response(body=buf.getvalue(), content_type="image/svg+xml",
                            headers={"Cache-Control": "no-store"})

    async def set_source(request):
        try:
            hub.set_source((await request.json()).get("source"))
        except (ValueError, AttributeError) as e:
            raise web.HTTPBadRequest(text=str(e))
        return web.json_response(hub.source_info())

    async def get_settings(_):
        import startup
        return web.json_response({
            "startup": startup.enabled(),
            "logDir": str(LOG_DIR),
            "appWindow": "show" in hub.hooks,
            "web": hub.web.status() if hub.web else None,
            **hub.source_info(),
        })

    async def put_settings(request):
        import startup
        body = await request.json()
        if "startup" in body:
            startup.set_enabled(bool(body["startup"]))
        if isinstance(body.get("web"), dict) and "enabled" in body["web"] and hub.web:
            hub.web.set_enabled(bool(body["web"]["enabled"]))
        if isinstance(body.get("pulsoidToken"), str):
            try:
                hub.set_pulsoid_token(body["pulsoidToken"])
            except ValueError as e:
                raise web.HTTPBadRequest(text=str(e))
        return await get_settings(request)

    async def open_page(_):
        import webbrowser
        if hub.web:
            webbrowser.open(hub.web.settings.web["page"])
        return web.json_response({"ok": True})

    async def open_url(request):
        import webbrowser
        pages = {"pulsoid-keys": "https://pulsoid.net/ui/keys"}
        url = pages.get((await request.json()).get("url"))
        if not url:
            raise web.HTTPBadRequest()
        webbrowser.open(url)
        return web.json_response({"ok": True})

    async def open_logs(_):
        os.startfile(LOG_DIR)
        return web.json_response({"ok": True})

    async def quit_app(_):
        asyncio.get_running_loop().call_later(0.2, hub.stop.set)
        return web.json_response({"ok": True})

    app.router.add_get("/", page("index.html"))
    app.router.add_get("/overlay", page("overlay.html"))
    app.router.add_get("/design", page("design.html"))
    app.router.add_get("/setup", page("setup.html"))
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/designs", get_designs)
    app.router.add_put("/api/designs", put_designs)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", local_post(put_settings))
    app.router.add_post("/api/show", local_post(show_window))
    app.router.add_post("/api/reconnect", local_post(reconnect))
    app.router.add_post("/api/source", local_post(set_source))
    app.router.add_post("/api/phone-pair", local_post(pair_phone))
    app.router.add_get("/api/phone-qr.svg", phone_qr)
    app.router.add_post("/api/open-logs", local_post(open_logs))
    app.router.add_post("/api/quit", local_post(quit_app))
    app.router.add_post("/api/open-page", local_post(open_page))
    app.router.add_post("/api/open-url", local_post(open_url))
    app.router.add_static("/static", WEB_DIR)
    return app


async def run(args, stop: asyncio.Event | None = None, ready=None, hooks: dict | None = None) -> str:
    """Serve the overlay and keep the heart-rate device connected until told to stop.

    Returns "busy" if another copy already owns the port, "obs-closed" when the watched
    process exited, or "stopped" when `stop` was set (or Quit was pressed in the app).
    `ready(hub)` is called once serving; `hooks` are UI callbacks such as {"show": fn}.
    """
    hub = Hub(None, Designs())
    if stop is not None:
        hub.stop = stop
    hub.hooks = hooks or {}
    runner = web.AppRunner(build_app(hub, args.port), shutdown_timeout=1.0)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", args.port).start()
    except OSError:
        await runner.cleanup()
        say(f"Port {args.port} is busy - pulseSSS is probably already running.")
        return "busy"

    hub.log = SessionLog()
    from webshare import Settings, WebShare
    hub.settings = Settings()
    hub.web = WebShare(hub, say, hub.settings)
    web_task = asyncio.create_task(hub.web.run())
    result = "stopped"
    try:
        base = f"http://127.0.0.1:{args.port}"
        say(f"Serving {base} (overlay {base}/overlay), logging to {hub.log.path}")
        if ready:
            ready(hub)
        tasks = [asyncio.create_task(source_loop(hub, args))]
        watcher = asyncio.create_task(watch_pid(args.watch_pid)) if args.watch_pid else None
        stopper = asyncio.create_task(hub.stop.wait())
        tasks += [t for t in (watcher, stopper) if t]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            result = "obs-closed"
        for t in pending:
            t.cancel()
        for t in done:
            t.result()
    finally:
        web_task.cancel()
        hub.log.write(time.time(), event="stopped")
        hub.log.close()
        # Open overlays would otherwise hold the server open until they disconnect.
        for ws in list(hub.clients):
            await ws.close()
        await runner.cleanup()
    return result


def add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument("--name", default="Fitbit Air", help='device name to match ("" = any heart-rate device)')
    parser.add_argument("--address", help="connect to this exact Bluetooth address instead of matching by name")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--stale", type=int, default=10, help="reconnect after this many seconds without a reading")
    parser.add_argument("--watch-pid", type=int, help="exit when this process exits (e.g. OBS)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream Bluetooth heart rate to OBS (console mode).")
    add_common_args(parser)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard:  {base}")
    print(f"Designer:   {base}/design")
    print(f"OBS source: {base}/overlay", flush=True)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.")
    # Bluetooth (WinRT) worker threads can keep the process alive after the loop ends;
    # everything is saved and closed by now, so leave immediately.
    os._exit(0)
