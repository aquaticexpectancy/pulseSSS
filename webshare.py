"""Share heart rate on a website through the pulseSSS relay (relay/ - a Cloudflare Worker).

pulseSSS keeps one WebSocket open to the relay. The relay says how many visitors are watching;
readings are only sent while someone is, otherwise a "ping" every 30 s keeps the connection alive
without waking the relay. Settings live in settings.json next to this file (the token stays local).
"""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / "settings.json"

DEFAULT_WEB = {
    "enabled": False,
    "url": "wss://pulse.forbandelse.com/source",
    "page": "https://pulse.forbandelse.com",
    "token": "",
}
PING_EVERY = 30


SOURCES = ("air", "pulsoid", "phone", "off")


class Settings:
    """settings.json: website sharing, which heart-rate source is in use, and the Pulsoid token."""

    def __init__(self):
        self.data = {"web": dict(DEFAULT_WEB), "source": "air", "pulsoid": {"token": ""}, "phone": {"key": ""}}
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            self.data["web"].update(saved.get("web", {}))
            if saved.get("source") in SOURCES:
                self.data["source"] = saved["source"]
            if isinstance(saved.get("pulsoid"), dict):
                self.data["pulsoid"].update(saved["pulsoid"])
            if isinstance(saved.get("phone"), dict):
                self.data["phone"].update(saved["phone"])
        except (OSError, ValueError):
            pass

    @property
    def web(self) -> dict:
        return self.data["web"]

    @property
    def source(self) -> str:
        return self.data["source"]

    @property
    def pulsoid_token(self) -> str:
        return self.data["pulsoid"].get("token", "")

    @property
    def phone_key(self) -> str:
        return self.data["phone"].get("key", "")

    def live_url(self) -> str:
        """Public read-only feed on the relay (the same one website visitors use)."""
        return self.web["url"].rsplit("/", 1)[0] + "/live"

    def save(self):
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)


class WebShare:
    def __init__(self, hub, say, settings: Settings | None = None):
        self.hub = hub
        self.say = say
        self.settings = settings or Settings()
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.viewers = 0
        self.state = "off"      # off | connecting | live | error
        self.error = ""
        self.changed = asyncio.Event()

    # ---------- status for the UI ----------
    def wanted(self) -> bool:
        s = self.settings
        return bool(s.web["token"]) and (bool(s.web["enabled"]) or bool(s.phone_key) or bool(s.pulsoid_token))

    def sharing(self) -> bool:
        """Send this PC's readings? Not while the phone is the source: its readings come *from* the relay."""
        return bool(self.settings.web["enabled"]) and self.hub.source != "phone"

    def status(self) -> dict:
        w = self.settings.web
        return {
            "enabled": bool(w["enabled"]),
            "configured": bool(w["token"]),
            "state": self.state,
            "viewers": self.viewers,
            "error": self.error,
            "page": w["page"],
            "embed": f'<span data-pulse></span>\n<script src="{w["page"]}/embed.js" defer></script>',
        }

    def _set(self, state: str, error: str = ""):
        if (state, error) == (self.state, self.error):
            return
        self.state, self.error = state, error
        if state == "error":
            self.say(f"Website sharing: {error}")
        self._announce()

    def _announce(self):
        asyncio.create_task(self.hub.send({"type": "web", **self.status()}))

    def set_enabled(self, on: bool):
        self.settings.web["enabled"] = bool(on)
        self.settings.save()
        self.say("Website sharing " + ("on." if on else "off."))
        self.changed.set()
        self._announce()

    # ---------- called by the hub ----------
    def _live_bpm(self):
        h = self.hub
        if h.status == "connected" and h.bpm and h.last_t and time.time() - h.last_t < 6:
            return h.bpm
        return None

    async def _send(self, obj):
        ws = self.ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_str(obj if isinstance(obj, str) else json.dumps(obj))
        except Exception:
            pass

    async def send_current(self):
        if self.viewers <= 0 or not self.sharing():
            return
        bpm = self._live_bpm()
        await self._send({"type": "hr", "bpm": bpm} if bpm else {"type": "offline"})

    async def send_pulsoid_token(self):
        """The relay uses this token to show Pulsoid on the website while this PC is off."""
        await self._send({"type": "pulsoid-token", "token": self.settings.pulsoid_token})

    async def send_phone_key(self):
        """Only a hash of the phone key goes to the relay; the key itself stays here and on the phone."""
        key = self.settings.phone_key
        await self._send({"type": "phone-key", "hash": hashlib.sha256(key.encode()).hexdigest() if key else ""})

    def keys_changed(self):
        self.changed.set()  # reconnect: connect if keys now need syncing, and re-send them

    def on_reading(self, bpm: int):
        if self.ws is not None and self.viewers > 0 and self.sharing():
            asyncio.create_task(self._send({"type": "hr", "bpm": bpm}))

    def on_status(self, status: str):
        if self.ws is not None and status != "connected" and self.sharing():
            asyncio.create_task(self._send({"type": "offline"}))

    # ---------- connection loop ----------
    async def _pinger(self, ws):
        while not ws.closed:
            await asyncio.sleep(PING_EVERY)
            await self._send("ping")

    async def _receiver(self, ws):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT or msg.data == "pong":
                continue
            try:
                m = json.loads(msg.data)
            except ValueError:
                continue
            if m.get("type") == "viewers":
                before, self.viewers = self.viewers, int(m.get("n", 0))
                if self.viewers != before:
                    self._announce()
                if self.viewers > 0 and before == 0:
                    await self.send_current()

    async def run(self):
        backoff = 5
        async with aiohttp.ClientSession() as session:
            while True:
                w = self.settings.web
                self.changed.clear()
                if not self.wanted():
                    self._set("off")
                    await self.changed.wait()
                    continue
                self._set("connecting")
                try:
                    async with session.ws_connect(
                        w["url"], headers={"Authorization": f"Bearer {w['token']}"},
                        heartbeat=None, timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=5),
                    ) as ws:
                        self.ws = ws
                        self._set("live")
                        await self.send_pulsoid_token()
                        await self.send_phone_key()
                        backoff = 5
                        tasks = [asyncio.create_task(self._receiver(ws)),
                                 asyncio.create_task(self._pinger(ws)),
                                 asyncio.create_task(self.changed.wait())]
                        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for t in pending:
                            t.cancel()
                        if self.changed.is_set():
                            continue  # settings changed: re-read them (closes this connection)
                        self._set("error", "connection to the relay dropped")
                except aiohttp.WSServerHandshakeError as e:
                    msg = "the relay refused the token" if e.status == 401 else f"relay answered {e.status}"
                    self._set("error", msg)
                    backoff = 60 if e.status == 401 else backoff
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
                    self._set("error", f"can't reach the relay ({type(e).__name__})")
                finally:
                    self.ws = None
                    if self.viewers:
                        self.viewers = 0
                        self._announce()
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)
