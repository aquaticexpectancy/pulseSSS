"""pulseSSS desktop app: an app window plus a tray icon, keeping the heart-rate connection alive.

Started by the pulseSSS shortcut (opens the window), by Windows at sign-in or by the OBS script
(--quiet: tray only). Closing the window keeps pulseSSS running in the tray; Quit stops it.
Only one copy runs; launching it again brings the existing window to the front.
"""

import argparse
import asyncio
import ctypes
import math
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

import pystray
import webview
from PIL import Image, ImageDraw

import pulse
import startup

APP = "pulseSSS"
ROOT = Path(__file__).resolve().parent
ICON_FILE = ROOT / "assets" / "pulsesss.ico"
WEBVIEW_DATA = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "pulseSSS" / "webview"

COLORS = {
    "connected": (255, 59, 92),
    "connecting": (245, 185, 66),
    "scanning": (245, 185, 66),
    "waiting": (245, 185, 66),
}
GREY = (139, 147, 163)


def heart_image(color, size=64) -> Image.Image:
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pts = []
    for i in range(240):
        t = 2 * math.pi * i / 240
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((big / 2 + x * big / 34, big / 2 - y * big / 34 + big * 0.03))
    ImageDraw.Draw(img).polygon(pts, fill=color + (255,))
    return img.resize((size, size), Image.LANCZOS)


def write_icon_file():
    if ICON_FILE.exists():
        return
    ICON_FILE.parent.mkdir(exist_ok=True)
    heart_image(COLORS["connected"], 256).save(ICON_FILE, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ask_running_copy_to_show(port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/show", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


class DesktopApp:
    def __init__(self, args):
        self.args = args
        self.base = f"http://127.0.0.1:{args.port}"
        self.hub = None
        self.loop = None
        self.ready = threading.Event()
        self.result = None
        self.quitting = False
        self.color = None
        self.status_line = "Starting…"
        self.notified_connected = False
        self.told_about_tray = False
        self.started = time.time()
        self.hinted = False

        self.window = webview.create_window(
            "pulseSSS", self.base, width=1280, height=840, min_size=(720, 520),
            hidden=args.quiet, background_color="#0f1115")
        self.window.events.closing += self.on_closing
        self.window.events.shown += self.style_window
        self.icon = pystray.Icon(APP, heart_image(GREY), "pulseSSS - starting", self.menu())

    # ---------- window ----------
    def style_window(self):
        """Dark title bar to match the app (Windows 10 20H1+ / 11)."""
        try:
            hwnd = self.window.native.Handle.ToInt32()
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def show_window(self, path=None):
        if path:
            self.window.load_url(self.base + path)
        self.window.show()
        self.window.restore()
        self.style_window()
        # Nudge it to the front even if another app has focus.
        self.window.on_top = True
        self.window.on_top = False

    def on_closing(self):
        if self.quitting:
            return True
        self.window.hide()
        if not self.told_about_tray:
            self.told_about_tray = True
            self.notify("pulseSSS is still running in the tray and keeps your heart rate live. "
                        "Right-click the heart to quit.")
        return False  # cancel the close; we only hid the window

    # ---------- tray ----------
    def menu(self):
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: self.status_line, None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Open pulseSSS", lambda: self.show_window(), default=True),
            item("Design overlay", lambda: self.show_window("/design")),
            item("Reconnect now", self.reconnect, enabled=lambda _: self.hub is not None and self.source() != "off"),
            item(lambda _: "Disconnect Fitbit Air" if self.source() == "air" else "Connect Fitbit Air",
                 lambda: self.set_source("off" if self.source() == "air" else "air"),
                 enabled=lambda _: self.hub is not None),
            item("Use phone app", lambda: self.set_source("off" if self.source() == "phone" else "phone"),
                 checked=lambda _: self.source() == "phone",
                 enabled=lambda _: bool(self.hub and self.hub.settings and
                                        (self.hub.settings.phone_key or self.source() == "phone"))),
            item("Use Pulsoid", lambda: self.set_source("off" if self.source() == "pulsoid" else "pulsoid"),
                 checked=lambda _: self.source() == "pulsoid",
                 enabled=lambda _: bool(self.hub and self.hub.settings and
                                        (self.hub.settings.pulsoid_token or self.source() == "pulsoid"))),
            item("Show on website", self.toggle_web, checked=lambda _: self.web_enabled(),
                 enabled=lambda _: bool(self.hub and self.hub.web and self.hub.web.settings.web["token"])),
            pystray.Menu.SEPARATOR,
            item("Start with Windows", lambda: startup.set_enabled(not startup.enabled()),
                 checked=lambda _: startup.enabled()),
            item("Quit pulseSSS", self.quit),
        )

    def notify(self, text):
        try:
            self.icon.notify(text, APP)
        except Exception:
            pass

    def web_enabled(self) -> bool:
        return bool(self.hub and self.hub.web and self.hub.web.settings.web["enabled"])

    def toggle_web(self):
        if self.loop and self.hub and self.hub.web:
            self.loop.call_soon_threadsafe(self.hub.web.set_enabled, not self.web_enabled())

    def reconnect(self):
        if self.loop and self.hub:
            self.loop.call_soon_threadsafe(self.hub.request_reconnect)

    def source(self) -> str:
        return self.hub.source if self.hub else "air"

    def set_source(self, source: str):
        if self.loop and self.hub:
            self.loop.call_soon_threadsafe(self.hub.set_source, source)

    def quit(self):
        self.quitting = True
        if self.loop and self.hub:
            self.loop.call_soon_threadsafe(self.hub.stop.set)
        else:
            self.finish()
        threading.Timer(6, lambda: os._exit(0)).start()  # safety net if Bluetooth shutdown hangs

    def finish(self):
        """Server stopped (Quit, OBS closed, or crash): close everything."""
        self.quitting = True
        try:
            self.icon.stop()
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass

    # ---------- background threads ----------
    def server_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop

        def on_ready(hub):
            self.hub = hub
            self.ready.set()

        try:
            self.result = loop.run_until_complete(
                pulse.run(self.args, ready=on_ready, hooks={"show": lambda: self.show_window()}))
        except Exception:
            pulse.say("Server crashed:\n" + traceback.format_exc())
        finally:
            self.ready.set()
            self.finish()

    def status_thread(self):
        while not self.quitting:
            time.sleep(1)
            hub = self.hub
            if hub is None:
                continue
            fresh = hub.last_t and time.time() - hub.last_t < 6
            st = hub.status
            device = (hub.device or "").replace("Google ", "")
            if st == "connected" and fresh:
                line, tip, title = f"♥ {hub.bpm} bpm · {device}", f"pulseSSS - {hub.bpm} bpm", f"pulseSSS · {hub.bpm} bpm"
            elif st == "connected":
                line, tip, title = f"Connected to {device}, waiting for data", "pulseSSS - waiting for data", "pulseSSS"
            elif st == "paused":
                line, tip, title = "Disconnected (Air is free for your phone)", "pulseSSS - disconnected", "pulseSSS"
            elif st == "waiting":
                who = "Phone app" if device == "Phone" else "Pulsoid"
                line, tip, title = f"{who}: waiting for your phone…", f"pulseSSS - waiting for {who}", "pulseSSS"
            elif st == "no-phone":
                line, tip, title = "Phone not paired (Setup page)", "pulseSSS - phone not paired", "pulseSSS"
            elif st in ("no-token", "bad-token"):
                line = "Pulsoid token needed" if st == "no-token" else "Pulsoid refused the token"
                tip, title = "pulseSSS - " + line, "pulseSSS"
            elif st == "disconnected" and device in ("Pulsoid", "Phone"):
                line, tip, title = f"Reconnecting to {device}…", f"pulseSSS - reconnecting to {device}", "pulseSSS"
            elif st in ("scanning", "disconnected"):
                line, tip, title = "Looking for Fitbit Air…", "pulseSSS - looking for Fitbit Air", "pulseSSS"
            elif st == "connecting":
                line, tip, title = f"Connecting to {device}…", "pulseSSS - connecting", "pulseSSS"
            else:
                line, tip, title = "Starting…", "pulseSSS - starting", "pulseSSS"

            color = COLORS.get(st, GREY) if (st != "connected" or fresh) else COLORS["connecting"]
            try:
                if color != self.color:
                    self.color = color
                    self.icon.icon = heart_image(color)
                if tip != self.icon.title:
                    self.icon.title = tip
                if line != self.status_line:
                    self.status_line = line
                    self.icon.update_menu()
                if title != self.window.title:
                    self.window.set_title(title)
            except Exception:
                pass

            if st == "connected" and fresh and not self.notified_connected:
                self.notified_connected = True
                self.notify(f"Connected to {device}. Heart rate is live.")
            if not self.hinted and not self.notified_connected and st in ("scanning", "disconnected") \
                    and device not in ("Pulsoid", "Phone") and time.time() - self.started > 45:
                self.hinted = True
                self.notify("Can't find the Fitbit Air yet. Turn on Share heart rate in Google Health.")

    def run(self):
        threading.Thread(target=self.server_thread, daemon=True).start()
        self.ready.wait(10)
        if self.result == "busy" or self.hub is None:
            return

        def tray_setup(icon):
            icon.visible = True
        threading.Thread(target=self.icon.run, kwargs={"setup": tray_setup}, daemon=True).start()
        threading.Thread(target=self.status_thread, daemon=True).start()

        WEBVIEW_DATA.mkdir(parents=True, exist_ok=True)
        webview.start(icon=str(ICON_FILE), private_mode=False, storage_path=str(WEBVIEW_DATA))
        # Window destroyed: make sure the server is stopped too.
        if self.loop and self.hub and not self.hub.stop.is_set():
            self.loop.call_soon_threadsafe(self.hub.stop.set)
            time.sleep(1.5)


def main():
    parser = argparse.ArgumentParser(description="pulseSSS desktop app")
    pulse.add_common_args(parser)
    parser.add_argument("--quiet", action="store_true", help="start in the tray without opening the window")
    args = parser.parse_args()
    try:
        write_icon_file()
        # Own taskbar identity (and icon) instead of grouping under Python.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pulseSSS.app")
        except Exception:
            pass
        if port_in_use(args.port):
            if not args.quiet and not ask_running_copy_to_show(args.port):
                webbrowser.open(f"http://127.0.0.1:{args.port}")  # console mode has no window
            return
        pulse.say("App starting" + (" in the tray." if args.quiet else "."))
        DesktopApp(args).run()
    except Exception:
        pulse.say("App crashed:\n" + traceback.format_exc())
    finally:
        # Bluetooth (WinRT) worker threads can keep the process alive; everything is closed by now.
        os._exit(0)


if __name__ == "__main__":
    main()
