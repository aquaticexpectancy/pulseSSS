// pulseSSS relay + forbandelse.com - live heart rate for website visitors.
//
//   https://forbandelse.com/          the site: just the live pulse
//   wss://forbandelse.com/source      pulseSSS connects here (Authorization: Bearer PUSH_TOKEN)
//   wss://forbandelse.com/live        visitors' browsers connect here (read-only)
//   https://forbandelse.com/now       current value as JSON
//   https://forbandelse.com/embed.js  drop-in script for any page
//   https://forbandelse.com/widget    bare pulse page for iframes
//   POST https://forbandelse.com/push the pulseSSS Phone app (Authorization: Bearer <phone key>)
//
// Sources, in priority order:
//   1. pulseSSS on the PC (direct Bluetooth, no middleman)
//   2. the pulseSSS Phone app (the iPhone holds the Air and posts readings here)
//   3. Pulsoid (phone app connected to the Air) - used only while visitors are watching and
//      pulseSSS isn't live, e.g. when you're away from the PC. pulseSSS sends the Pulsoid token
//      (kept in this object's storage); the PULSOID_TOKEN secret is used if it never has.
//
// One Durable Object holds every connection with the Hibernation API. pulseSSS only streams while
// someone is watching; otherwise it sends "ping", which is auto-answered without waking the object,
// so an unwatched relay costs nothing.

import { DurableObject } from "cloudflare:workers";

interface Env {
  PULSE: DurableObjectNamespace<Pulse>;
  PUSH_TOKEN: string;
  PULSOID_TOKEN?: string;  // optional fallback; token with data:heart_rate:read
  PULSOID_URL?: string;    // override for local testing
}

type Reading = { bpm: number | null; t: number; online: boolean };
type Via = "pc" | "phone" | "pulsoid" | null;
type Snapshot = { type: "pulse"; bpm: number | null; online: boolean; age: number | null; via: Via };

const EMPTY: Reading = { bpm: null, t: 0, online: false };
const OFFLINE: Snapshot = { type: "pulse", bpm: null, online: false, age: null, via: null };
const STALE_MS = 20_000;          // a reading older than this counts as offline
const SOURCE_GRACE_MS = 3_000;    // give pulseSSS this long to answer a new viewer before falling back
const PULSOID_DEFAULT = "https://dev.pulsoid.net/api/v1/data/real_time";
const PHONE_STALE_MS = 40_000;    // the phone checks in every 15 s even when nobody is watching
const PHONE_SAVE_MS = 20_000;     // persist the phone's reading at most this often (storage writes cost)
const PHONE_EVERY_WATCHED = 2;    // seconds between phone posts while someone is watching
const PHONE_EVERY_IDLE = 15;

export type PushResult = { ok: boolean; viewers: number; every: number };

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
}

export class Pulse extends DurableObject<Env> {
  // Pulsoid fallback state. Only exists while the object is awake (an open outbound socket keeps it so).
  private pulsoid: WebSocket | null = null;
  private pulsoidStarting = false;
  private pulsoidBpm: number | null = null;
  private pulsoidT = 0;
  private pulsoidBackoff = 5_000;
  private token: string | undefined | null = null;  // null = not loaded from storage yet
  // Phone app state. Kept in memory, saved to storage now and then so it survives hibernation.
  private phone: Reading | null = null;              // null = not loaded from storage yet
  private phoneSaved = 0;
  private phoneKeyHash: string | undefined | null = null;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  // ---------- connections ----------
  private sources(except?: WebSocket) {
    return this.ctx.getWebSockets("source").filter(ws => ws !== except);
  }

  private viewers(except?: WebSocket) {
    return this.ctx.getWebSockets("viewer").filter(ws => ws !== except);
  }

  // The latest PC reading lives on the source socket's attachment, which survives hibernation.
  private reading(except?: WebSocket): Reading {
    const src = this.sources(except)[0];
    return src ? ((src.deserializeAttachment() as Reading | null) ?? EMPTY) : EMPTY;
  }

  private pcLive(except?: WebSocket): boolean {
    const r = this.reading(except);
    return r.online && r.bpm != null && Date.now() - r.t < STALE_MS;
  }

  private phoneLive(): boolean {
    const r = this.phone;
    return !!r && r.online && r.bpm != null && Date.now() - r.t < PHONE_STALE_MS;
  }

  private async loadPhone() {
    if (this.phone === null) this.phone = (await this.ctx.storage.get<Reading>("phone")) ?? EMPTY;
  }

  private pulsoidLive(): boolean {
    return this.pulsoidBpm != null && Date.now() - this.pulsoidT < STALE_MS;
  }

  async snapshot(): Promise<Snapshot> {
    await this.loadPhone();
    return this.now();
  }

  now(except?: WebSocket): Snapshot {
    if (this.pcLive(except)) {
      const r = this.reading(except);
      return { type: "pulse", bpm: r.bpm, online: true, age: Date.now() - r.t, via: "pc" };
    }
    if (this.phoneLive()) {
      const r = this.phone!;
      return { type: "pulse", bpm: r.bpm, online: true, age: Date.now() - r.t, via: "phone" };
    }
    if (this.pulsoidLive()) {
      return { type: "pulse", bpm: this.pulsoidBpm, online: true, age: Date.now() - this.pulsoidT, via: "pulsoid" };
    }
    return OFFLINE;
  }

  private broadcast(msg: Snapshot, except?: WebSocket) {
    const text = JSON.stringify(msg);
    for (const ws of this.viewers(except)) {
      try { ws.send(text); } catch { /* closing */ }
    }
  }

  private tellSource(except?: WebSocket) {
    const msg = JSON.stringify({ type: "viewers", n: this.viewers(except).length });
    for (const ws of this.sources(except)) {
      try { ws.send(msg); } catch { /* closing */ }
    }
  }

  // ---------- Pulsoid fallback ----------
  private async pulsoidToken(): Promise<string | undefined> {
    if (this.token === null) {
      const saved = await this.ctx.storage.get<string>("pulsoidToken");
      this.token = saved === undefined ? this.env.PULSOID_TOKEN : saved || undefined;
    }
    return this.token;
  }

  private async setPulsoidToken(token: string) {
    const clean = /^[\w.-]{8,200}$/.test(token) ? token : "";
    await this.ctx.storage.put("pulsoidToken", clean);  // "" = explicitly none
    if ((clean || undefined) === this.token) return;
    this.token = clean || undefined;
    const wasShowing = this.pulsoidLive();
    this.stopPulsoid();
    if (wasShowing) this.broadcast(this.now());
    this.pulsoidBackoff = 5_000;
    await this.checkFallback();
  }

  private async checkFallback(except?: WebSocket, allowGrace = false) {
    await this.loadPhone();
    const wanted = this.viewers(except).length > 0 && !!(await this.pulsoidToken())
      && !this.pcLive(except) && !this.phoneLive();
    if (!wanted) return this.stopPulsoid();
    if (this.pulsoid || this.pulsoidStarting) return;
    if (allowGrace && this.sources(except).length > 0) {
      // pulseSSS is connected but idle (nobody was watching); it normally answers within a moment.
      await this.ctx.storage.setAlarm(Date.now() + SOURCE_GRACE_MS);
      return;
    }
    await this.startPulsoid();
  }

  private async startPulsoid() {
    this.pulsoidStarting = true;
    try {
      const token = await this.pulsoidToken();
      if (!token) return;
      const base = this.env.PULSOID_URL || PULSOID_DEFAULT;
      const url = `${base}?access_token=${encodeURIComponent(token)}&response_mode=text_plain_only_heart_rate`;
      const resp = await fetch(url, { headers: { Upgrade: "websocket" } });
      const ws = resp.webSocket;
      if (!ws) {
        console.log(`Pulsoid refused the connection: ${resp.status}`);
        return this.retryPulsoid(resp.status === 401 || resp.status === 403 ? 10 * 60_000 : undefined);
      }
      ws.accept();
      this.pulsoid = ws;
      this.pulsoidBackoff = 5_000;
      ws.addEventListener("message", e => this.onPulsoid(e.data));
      const closed = () => {
        if (this.pulsoid !== ws) return;
        this.pulsoid = null;
        this.pulsoidBpm = null;
        if (!this.pcLive() && !this.phoneLive()) this.broadcast(OFFLINE);
        this.retryPulsoid();
      };
      ws.addEventListener("close", closed);
      ws.addEventListener("error", closed);
    } catch (err) {
      console.log(`Pulsoid connection failed: ${err}`);
      this.retryPulsoid();
    } finally {
      this.pulsoidStarting = false;
    }
  }

  private onPulsoid(data: string | ArrayBuffer) {
    const text = typeof data === "string" ? data : new TextDecoder().decode(data);
    let bpm = Number.parseInt(text, 10);
    if (Number.isNaN(bpm)) {
      try { bpm = JSON.parse(text)?.data?.heart_rate; } catch { return; }
    }
    if (!(bpm > 20 && bpm < 260)) return;
    this.pulsoidBpm = Math.round(bpm);
    this.pulsoidT = Date.now();
    if (!this.pcLive() && !this.phoneLive())
      this.broadcast({ type: "pulse", bpm: this.pulsoidBpm, online: true, age: 0, via: "pulsoid" });
  }

  private stopPulsoid() {
    const ws = this.pulsoid;
    this.pulsoid = null;
    this.pulsoidBpm = null;
    if (ws) try { ws.close(1000, "not needed"); } catch { /* already closed */ }
  }

  private retryPulsoid(ms?: number) {
    const delay = ms ?? this.pulsoidBackoff;
    this.pulsoidBackoff = Math.min(this.pulsoidBackoff * 2, 120_000);
    this.ctx.waitUntil(this.ctx.storage.setAlarm(Date.now() + delay));
  }

  // ---------- phone app ----------
  private async phoneKey(): Promise<string | undefined> {
    if (this.phoneKeyHash === null) this.phoneKeyHash = (await this.ctx.storage.get<string>("phoneKeyHash")) || undefined;
    return this.phoneKeyHash;
  }

  private async setPhoneKeyHash(hash: string) {
    const clean = /^[0-9a-f]{64}$/.test(hash) ? hash : "";
    if ((clean || undefined) === (await this.phoneKey())) return;
    this.phoneKeyHash = clean || undefined;
    await this.ctx.storage.put("phoneKeyHash", clean);
    // A new or removed key: whatever the old phone sent no longer counts.
    await this.loadPhone();
    if (this.phone?.online) {
      const wasLive = this.phoneLive();
      this.phone = { ...this.phone, online: false };
      await this.ctx.storage.put("phone", this.phone);
      if (wasLive && !this.pcLive()) this.broadcast(this.now());
      await this.checkFallback();
    }
  }

  /** A reading (or null = stopped) from the phone app. `keyHash` is sha256 of its bearer key. */
  async push(keyHash: string, bpm: number | null): Promise<PushResult | null> {
    const want = await this.phoneKey();
    const enc = new TextEncoder();
    const a = enc.encode(keyHash), b = enc.encode(want ?? "");
    if (!want || a.byteLength !== b.byteLength || !crypto.subtle.timingSafeEqual(a, b)) return null;

    await this.loadPhone();
    const wasLive = this.phoneLive();
    const now = Date.now();
    if (bpm != null && bpm > 20 && bpm < 260) {
      this.phone = { bpm: Math.round(bpm), t: now, online: true };
      this.stopPulsoid();
      if (!this.pcLive()) this.broadcast({ type: "pulse", bpm: this.phone.bpm, online: true, age: 0, via: "phone" });
      if (!wasLive) await this.ctx.storage.setAlarm(now + PHONE_STALE_MS + 1_000);  // notices the phone going quiet
    } else {
      this.phone = { ...(this.phone ?? EMPTY), online: false };
      if (wasLive && !this.pcLive()) this.broadcast(this.now());
      await this.checkFallback();
    }
    if (now - this.phoneSaved > PHONE_SAVE_MS || !this.phone.online || !wasLive) {
      this.phoneSaved = now;
      await this.ctx.storage.put("phone", this.phone);
    }
    const viewers = this.viewers().length;
    return { ok: true, viewers, every: viewers > 0 ? PHONE_EVERY_WATCHED : PHONE_EVERY_IDLE };
  }

  async alarm() {
    await this.loadPhone();
    if (this.phone?.online) {
      if (this.phoneLive()) {
        // Still posting: check again once its latest reading would go stale.
        await this.ctx.storage.setAlarm(this.phone.t + PHONE_STALE_MS + 1_000);
      } else {
        // The phone went quiet (app killed, out of signal): tell visitors.
        this.phone = { ...this.phone, online: false };
        await this.ctx.storage.put("phone", this.phone);
        if (!this.pcLive()) this.broadcast(this.now());
      }
    }
    await this.checkFallback();
  }

  // ---------- WebSocket lifecycle ----------
  async fetch(request: Request): Promise<Response> {
    const role = new URL(request.url).pathname === "/source" ? "source" : "viewer";
    const [client, server] = Object.values(new WebSocketPair());
    if (role === "source") {
      for (const old of this.sources()) old.close(4000, "replaced by a newer connection");
      server.serializeAttachment(EMPTY);
    }
    this.ctx.acceptWebSocket(server, [role]);
    await this.loadPhone();
    if (role === "viewer") server.send(JSON.stringify(this.now()));
    this.tellSource();
    if (role === "viewer") await this.checkFallback(undefined, true);
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer) {
    if (!this.ctx.getTags(ws).includes("source") || typeof message !== "string") return;
    let m: { type?: string; bpm?: number; token?: string; hash?: string };
    try { m = JSON.parse(message); } catch { return; }

    if (m.type === "hr" && typeof m.bpm === "number" && m.bpm > 20 && m.bpm < 260) {
      const bpm = Math.round(m.bpm);
      ws.serializeAttachment({ bpm, t: Date.now(), online: true } satisfies Reading);
      this.stopPulsoid();  // the PC is live again, it wins
      this.broadcast({ type: "pulse", bpm, online: true, age: 0, via: "pc" });
    } else if (m.type === "pulsoid-token" && typeof m.token === "string") {
      await this.setPulsoidToken(m.token);
    } else if (m.type === "phone-key" && typeof m.hash === "string") {
      await this.setPhoneKeyHash(m.hash);
    } else if (m.type === "offline") {
      const prev = (ws.deserializeAttachment() as Reading | null) ?? EMPTY;
      ws.serializeAttachment({ ...prev, online: false });
      await this.loadPhone();
      this.broadcast(this.now());
      await this.checkFallback();
    }
  }

  async webSocketClose(ws: WebSocket, code: number, reason: string) {
    const wasSource = this.ctx.getTags(ws).includes("source");
    try { ws.close(code, reason); } catch { /* already closed */ }
    if (wasSource) {
      await this.loadPhone();
      if (this.sources(ws).length === 0) this.broadcast(this.now(ws), ws);
    } else {
      this.tellSource(ws);
    }
    await this.checkFallback(ws);
  }

  async webSocketError(ws: WebSocket) {
    await this.webSocketClose(ws, 1011, "error");
  }
}

async function tokenOk(header: string | null, token: string | undefined): Promise<boolean> {
  if (!token || !header) return false;
  const enc = new TextEncoder();
  const a = enc.encode(header), b = enc.encode(`Bearer ${token}`);
  return a.byteLength === b.byteLength && crypto.subtle.timingSafeEqual(a, b);
}

const CORS = { "Access-Control-Allow-Origin": "*" };
const html = (body: string) =>
  new Response(body, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=300" } });

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const stub = env.PULSE.getByName("main");
    const upgrade = request.headers.get("Upgrade")?.toLowerCase() === "websocket";

    switch (url.pathname) {
      case "/":
        return html(SITE_HTML);

      case "/widget":
        return html(WIDGET_HTML);

      case "/source":
        if (!upgrade) return new Response("WebSocket required", { status: 426 });
        if (!(await tokenOk(request.headers.get("Authorization"), env.PUSH_TOKEN)))
          return new Response("Unauthorized", { status: 401 });
        return stub.fetch(request);

      case "/live":
        if (!upgrade) return new Response("WebSocket required", { status: 426 });
        return stub.fetch(request);

      case "/now":
        return Response.json(await stub.snapshot(), { headers: { ...CORS, "Cache-Control": "no-store" } });

      case "/push": {
        if (request.method !== "POST") return new Response("POST required", { status: 405 });
        const auth = request.headers.get("Authorization") ?? "";
        if (!auth.startsWith("Bearer ") || auth.length > 300) return new Response("Unauthorized", { status: 401 });
        let body: { bpm?: unknown };
        try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }
        const bpm = typeof body.bpm === "number" ? body.bpm : null;
        const result = await stub.push(await sha256(auth.slice(7)), bpm);
        if (!result) return new Response("Unauthorized", { status: 401 });
        return Response.json(result, { headers: { "Cache-Control": "no-store" } });
      }

      case "/embed.js":
        return new Response(EMBED_JS, {
          headers: { ...CORS, "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "public, max-age=300" },
        });

      default:
        return new Response("Not found", { status: 404 });
    }
  },
} satisfies ExportedHandler<Env>;

// ---------------------------------------------------------------------------------------------
// embed.js - <span data-pulse></span><script src="https://forbandelse.com/embed.js" defer></script>
// Renders: <span class="pulse is-live"><span class="pulse-heart">♥</span><span class="pulse-bpm">76</span>
//          <span class="pulse-unit">bpm</span></span>. Default styles use :where() so your CSS wins.
// Options on the element: data-offline="away" (offline text), data-unit="bpm".
// Each element also fires a "pulse" event: el.addEventListener("pulse", e => e.detail.bpm / .online / .via).
const EMBED_JS = `(() => {
  const me = document.currentScript;
  const origin = me ? new URL(me.src).origin : "https://forbandelse.com";
  if (!document.getElementById("pulse-style")) {
    const s = document.createElement("style");
    s.id = "pulse-style";
    s.textContent = \`
:where(.pulse) { display: inline-flex; align-items: baseline; gap: .3em; font-variant-numeric: tabular-nums; white-space: nowrap; }
:where(.pulse-heart) { color: #ff3b5c; display: inline-block; transform-origin: 50% 60%; }
:where(.pulse.is-live .pulse-heart) { animation: pulse-beat var(--pulse-period, 1s) ease-out infinite; }
:where(.pulse-unit) { opacity: .65; font-size: .75em; }
:where(.pulse.is-offline) { opacity: .5; }
:where(.pulse.is-offline .pulse-heart) { filter: grayscale(1); }
@keyframes pulse-beat { 0% { transform: scale(1) } 12% { transform: scale(1.25) } 24% { transform: scale(1) }
  36% { transform: scale(1.1) } 55% { transform: scale(1) } }
@media (prefers-reduced-motion: reduce) { .pulse-heart { animation: none !important; } }\`;
    document.head.appendChild(s);
  }

  const els = [...document.querySelectorAll("[data-pulse]")].filter(el => !el.dataset.pulseReady);
  if (!els.length) return;
  for (const el of els) {
    el.dataset.pulseReady = "1";
    el.classList.add("pulse", "is-offline");
    el.innerHTML = '<span class="pulse-heart" aria-hidden="true">\\u2665</span><span class="pulse-bpm"></span><span class="pulse-unit"></span>';
    show(el, null, false, null);
  }

  function show(el, bpm, online, via) {
    const live = online && bpm != null;
    el.classList.toggle("is-live", live);
    el.classList.toggle("is-offline", !live);
    el.querySelector(".pulse-bpm").textContent = live ? bpm : (el.dataset.offline || "offline");
    el.querySelector(".pulse-unit").textContent = live ? (el.dataset.unit ?? "bpm") : "";
    if (live) el.style.setProperty("--pulse-period", (60 / Math.max(bpm, 30)).toFixed(3) + "s");
    el.setAttribute("title", live ? bpm + " bpm, live" : "heart rate offline");
    el.dispatchEvent(new CustomEvent("pulse", { detail: { bpm: live ? bpm : null, online: live, via: live ? via : null } }));
  }
  const render = (bpm, online, via) => els.forEach(el => show(el, bpm, online, via));

  let delay = 1000, stale = 0;
  function connect() {
    const ws = new WebSocket(origin.replace(/^http/, "ws") + "/live");
    ws.onopen = () => { delay = 1000; };
    ws.onmessage = e => {
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.type !== "pulse") return;
      const fresh = m.online && m.bpm != null && (m.age == null || m.age < 20000);
      render(m.bpm, fresh, m.via);
      clearTimeout(stale);
      if (fresh) stale = setTimeout(() => render(null, false, null), 20000);
    };
    ws.onclose = () => { render(null, false, null); setTimeout(connect, delay); delay = Math.min(delay * 2, 30000); };
  }
  connect();
})();
`;

const HEART_ICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23ff3b5c' d='M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z'/%3E%3C/svg%3E";

// ---------------------------------------------------------------------------------------------
// forbandelse.com - the whole site: one live heartbeat.
const SITE_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>forbandelse</title>
<meta name="description" content="a heartbeat, live.">
<meta name="theme-color" content="#111111">
<link rel="icon" href="${HEART_ICON}">
<style>
  :root { --bg: #111; --num: #aaa; --dim: #777; --faint: #3a3a3a; color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    min-height: 100vh; display: grid; grid-template-rows: 1fr auto;
    background: var(--bg); color: var(--num);
    font: 13px/1.6 monospace;
  }
  main { display: grid; place-items: center; align-content: center; padding: 32px 16px; text-align: center; }
  .beat { font-size: clamp(64px, 16vw, 128px); font-weight: 400; letter-spacing: normal; line-height: 1; gap: .15em; }
  .beat .pulse-heart { display: none; }
  .beat .pulse-bpm { color: var(--num); }
  .beat .pulse-unit { font-size: 13px; letter-spacing: normal; color: var(--dim); opacity: 1; }
  .beat.is-offline { opacity: 1; }
  .beat.is-offline .pulse-bpm { font-size: 13px; color: var(--dim); }
  footer { padding: 22px 16px; text-align: center; color: var(--faint); font-size: 11px; }
  .how { font: inherit; color: var(--dim); background: none; border: 0; padding: 4px 6px; margin-left: 6px; cursor: pointer; }
  .how:hover, .how:focus-visible { color: var(--num); outline: none; text-decoration: underline; }
  dialog { width: min(440px, calc(100vw - 32px)); max-height: calc(100vh - 48px); overflow: auto; padding: 24px 24px 18px;
    background: var(--bg); color: var(--num); border: 1px solid var(--faint); font: 13px/1.7 monospace; }
  dialog::backdrop { background: rgba(0, 0, 0, .7); }
  dialog h2 { margin: 0 0 8px; font: inherit; color: var(--dim); }
  dialog h2 + p, dialog p + h2 { margin-top: 0; }
  dialog p { margin: 0 0 14px; }
  dialog a { color: var(--num); }
  dialog .close { display: block; margin: 6px 0 0 auto; padding: 4px 0 4px 8px; }
</style>
</head>
<body>
<main>
  <span class="beat" data-pulse data-offline="away"></span>
</main>
<footer>forbandelse<button class="how" id="how" type="button" aria-haspopup="dialog">how?</button></footer>
<dialog id="howto" aria-labelledby="howto-title">
  <h2 id="howto-title">how it works</h2>
  <p>a fitbit air on my wrist broadcasts my heart rate over bluetooth.</p>
  <p>at my pc, a small app reads it straight from the air. when i'm away, my phone does it instead and keeps
    sending in the background.</p>
  <p>either one sends it to a tiny cloudflare worker, which pushes it to this page live, about every 2 seconds
    while someone's watching. no pulsoid, no fitbit cloud, nothing in between.</p>
  <p>"away" means neither is connected right now.</p>
  <h2>make your own</h2>
  <p>it's all open source: <a href="https://github.com/aquaticexpectancy/pulseSSS" target="_blank" rel="noopener">github.com/aquaticexpectancy/pulseSSS</a>.
    works with most bluetooth heart-rate straps and watches that can broadcast heart rate. the readme walks
    through the desktop app, your own relay and the phone app.</p>
  <button class="how close" id="howclose" type="button">close</button>
</dialog>
<script>
  const dlg = document.getElementById("howto");
  document.getElementById("how").onclick = () => dlg.showModal();
  document.getElementById("howclose").onclick = () => dlg.close();
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); });
</script>
<script src="/embed.js" defer></script>
</body>
</html>
`;

// ---------------------------------------------------------------------------------------------
// Bare pulse for iframes. Options: ?bg=transparent|dark|light  ?color=ff3b5c  ?size=1
const WIDGET_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pulse</title>
<style>
  :root { --bg: #0f1115; --fg: #e8eaef; --s: 1; color-scheme: dark; }
  html, body { margin: 0; height: 100%; }
  body { display: grid; place-items: center; background: var(--bg); color: var(--fg);
    font: 700 calc(64px * var(--s)) / 1 "Segoe UI", system-ui, sans-serif; }
  .pulse-unit { font-weight: 600; letter-spacing: .08em; }
</style>
</head>
<body>
<span data-pulse></span>
<script>
  const q = new URLSearchParams(location.search), r = document.documentElement.style;
  const bg = q.get("bg");
  if (bg === "transparent") r.setProperty("--bg", "transparent");
  if (bg === "light") { r.setProperty("--bg", "#ffffff"); r.setProperty("--fg", "#16181d"); r.colorScheme = "light"; }
  if (+q.get("size") > 0) r.setProperty("--s", +q.get("size"));
  if (/^[0-9a-f]{6}$/i.test(q.get("color") || "")) {
    const st = document.createElement("style");
    st.textContent = ".pulse-heart { color: #" + q.get("color") + " }";
    document.head.appendChild(st);
  }
</script>
<script src="/embed.js" defer></script>
</body>
</html>
`;
