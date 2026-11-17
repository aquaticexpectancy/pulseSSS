# pulseSSS

Live heart rate from a Fitbit Air (or any standard Bluetooth heart-rate device) straight into OBS.
No phone app or cloud service sits in between: the PC connects to the Air over Bluetooth.

First time only: `python -m pip install -r requirements.txt`, then
`powershell -ExecutionPolicy Bypass -File make_shortcuts.ps1` for the desktop and Start menu shortcuts.
Before streaming: on your phone, Google Health → Connections → **Share heart rate**.

## The app

Click the **pulseSSS** shortcut (desktop or Start menu). The pulseSSS window opens (its own app window,
not a browser tab) with three pages in the sidebar:

- **Live**: current BPM, stats, 5-minute chart, what's on stream right now, session info.
- **Design**: overlay presets, live preview and every visual setting.
- **Setup**: heart-rate source (Fitbit Air, phone app, Pulsoid or disconnected), phone pairing, website,
  OBS setup, Start with Windows, logs, Quit.

Closing the window keeps pulseSSS running in the tray; clicking the shortcut or the tray heart brings the
same window back. Quit from the Setup page or the tray menu.

## Staying connected

A heart sits in the system tray and the app stays connected to the Air in the background, whether OBS
is open or not. Every new connection
makes the Air "prepare to share" (Google Health says up to 30 seconds, and pings your phone),
so staying connected means you only wait once instead of every time you start a stream.

- **Heart colour:** red = live, amber = connecting / waiting for data, grey = not connected.
- **Hover:** current BPM.
- **Click:** opens the pulseSSS window.
- **Right-click menu:** Open pulseSSS, Design overlay, Reconnect now, Disconnect / Connect Fitbit Air,
  Use phone app, Use Pulsoid, Show on website, **Start with Windows**, Quit.

It reconnects by itself if the Air drops out. Only one copy runs. The OBS script and Start with Windows
launch it tray-only (`--quiet`), without opening the window.

## Disconnect and Pulsoid

The Air connects to only one device at a time. **Disconnect** (Setup page or tray) releases it so your
phone can take it; **Connect Fitbit Air** takes it back. pulseSSS remembers the choice across restarts.

**Use Pulsoid** reads your heart rate from the Pulsoid app on your phone instead (pulseSSS lets go of the
Air first). Overlay, logs and website keep working the same way. It needs a Pulsoid token from
pulsoid.net/ui/keys (a Pulsoid BRO feature; the trial works): paste it on the Setup page. pulseSSS also
hands the token to the forbandelse.com relay, which uses it to show Pulsoid while this PC is off.
The token stays in `settings.json`; Remove on the Setup page deletes it here and on the relay.

## pulseSSS Phone (iPhone app)

For when you're away from the PC: the phone holds the Air and sends your heart rate, in the background too.

```
Air → pulseSSS Phone (iPhone) → forbandelse.com relay ─┬→ website visitors
                                                       └→ pulseSSS on the PC (overlay + logs)
```

**Install (free Apple ID, refreshed automatically by AltStore):**
1. On the PC, install **AltServer** from altstore.io (it needs iTunes and iCloud from Apple's website, not the
   Microsoft Store versions) and use it to install **AltStore** on the iPhone.
2. iPhone: Settings → Privacy & Security → **Developer Mode** on (restarts the phone).
3. In AltStore: Sources → **+** → add
   `https://raw.githubusercontent.com/aquaticexpectancy/pulseSSS/main/altstore/source.json`,
   then install **pulseSSS Phone**. New versions show up there as updates.
4. Keep AltServer running on the PC; AltStore re-signs the app over Wi-Fi before its 7 days run out.

Without AltStore: download `pulseSSS-Phone.ipa` from the latest GitHub release and install it with Sideloadly
(re-install every 7 days).

**Pair and use:**
1. pulseSSS → Setup → **Pair phone**, scan the QR code with the iPhone camera, open it in pulseSSS Phone.
2. Press **Disconnect** (or **Use phone**) in pulseSSS so the PC lets go of the Air.
3. The app connects to the Air (allow Bluetooth when asked). It keeps sending with the screen locked;
   swiping it away in the app switcher stops it until you open it again.
4. Back at the PC: **release the air** in the app, then **Connect Fitbit Air** in pulseSSS.

Readings go to `POST /push` on the relay with the phone key; the relay only stores a hash of that key.
**Unpair** on the Setup page makes the old key useless. The relay asks the app to post every 2 s while
someone is watching (the website or pulseSSS in phone mode) and every 15 s otherwise, which keeps it
within Cloudflare's free plan.

The app is built by `.github/workflows/phone.yml` on GitHub's Mac machines from `ios/` (XcodeGen spec +
Swift), published as a GitHub release, and listed in `altstore/source.json`.

## OBS

1. OBS → **Tools → Scripts** → **+** → pick `obs\pulsesss.lua` in this folder.
2. Click **Add overlay to current scene**.

The script starts the tray app with OBS if it isn't already running and leaves it running when OBS
closes. Tick **Quit pulseSSS when OBS closes** if you'd rather it didn't stay in the background
(you'll wait for a reconnect each time). **Reload overlays** refreshes every heart-rate source if
one ever shows blank.

To edit designs without leaving OBS: **Docks → Custom Browser Docks**, name it "Heart rate",
URL `http://127.0.0.1:8765/design`.

Without the script: add a **Browser** source with URL `http://127.0.0.1:8765/overlay`,
width 420, height 200.

## Website: forbandelse.com

forbandelse.com shows your live heart rate and nothing else. It's served by the Cloudflare Worker in
`relay/` (Worker name `forbandelse-pulse`, route `forbandelse.com/*`), which also relays the data:

1. **At your PC:** pulseSSS sends readings straight to the Worker (Setup → Website, or the tray's
   **Show on website**). Readings only go out while someone is on the page; otherwise pulseSSS just keeps a
   free keep-alive open.
2. **Away from your PC (optional):** the Worker falls back to Pulsoid, fed by the Pulsoid app on your phone,
   but only while visitors are watching and pulseSSS isn't live.
3. **Neither:** visitors see "away".

Other sites can embed it too:
`<span data-pulse></span><script src="https://forbandelse.com/embed.js" defer></script>`
(bare iframe version: `https://forbandelse.com/widget?bg=transparent`, JSON: `https://forbandelse.com/now`).

The push token lives in `settings.json` (local) and as the Worker secret `PUSH_TOKEN`. The Pulsoid
fallback uses the token pasted in pulseSSS (see above); the `PULSOID_TOKEN` Worker secret is only used
if pulseSSS has never sent one. Redeploy after editing the Worker: `cd relay`
then `npx wrangler deploy`.

The old Dota Radar Worker (`dotaradar-offline`) is still in the Cloudflare account, just without the route.

## Pages

| URL | What it is |
| --- | --- |
| `http://127.0.0.1:8765` | Live dashboard: status, current BPM, stats, chart |
| `http://127.0.0.1:8765/design` | Designer: presets, live preview, every visual setting |
| `http://127.0.0.1:8765/overlay` | The overlay, showing the **default** design |
| `http://127.0.0.1:8765/overlay?design=Neon` | The overlay with a specific design |

Design changes save automatically to `designs.json` and show up in OBS immediately.
Use different `?design=` URLs for different scenes. `/overlay?demo=1` shows simulated heart rate.

Designer presets: Classic, Neon, Card, ECG Monitor, Minimal, Pill, Big Number, Retro, Zones.

## Logs

- `logs/hr_<date>.csv`: one row per reading (`timestamp, unix_ms, bpm, event`) plus connects and
  dropouts, one file per day. OBS names recordings by start time, so you can match them up.
- `logs/pulsesss.log`: what the app is doing (handy when the tray icon stays grey).

## Console mode

`start.bat` or `python pulse.py` runs without the tray, printing status to the console.

```
python pulse.py --name ""            any heart-rate device, not just "Fitbit Air"
python pulse.py --address 66:6F:...  one exact device
python pulse.py --port 9000          different port (also set it in the OBS script)
python pulse.py --stale 15           reconnect after 15 s without data (default 10)
python pulse.py --watch-pid 1234     exit when process 1234 exits
```

The same options work on `pulseSSS.pyw`, plus `--quiet` (don't open the dashboard if already running).
