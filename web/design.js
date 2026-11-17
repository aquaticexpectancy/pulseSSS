// Shared overlay renderer: the OBS overlay and the designer preview both use mount().

export const FONTS = [
  { name: "Segoe UI" }, { name: "Bahnschrift" }, { name: "Consolas" }, { name: "Arial Black" },
  { name: "Impact" }, { name: "Georgia" },
  { name: "Inter", google: "wght@400;600;700;800;900" },
  { name: "Montserrat", google: "wght@400;600;700;800;900" },
  { name: "Orbitron", google: "wght@400;600;700;800;900" },
  { name: "Rajdhani", google: "wght@400;600;700" },
  { name: "Oswald", google: "wght@400;600;700" },
  { name: "Bebas Neue", google: true },
  { name: "Press Start 2P", google: true },
  { name: "Share Tech Mono", google: true },
];

export const DEFAULTS = {
  layout: "row",          // row | stack
  align: "left",          // left | center | right
  font: "Segoe UI",
  weight: 700,
  size: 1,
  text: "#ffffff",
  unit: "BPM",            // empty string hides the label
  shadow: true,
  accent: "#ff3b5c",
  icon: "heart",          // heart | heart-outline | pulse | none
  iconAnim: "beat",       // beat | glow | none
  glow: true,
  graph: "line",          // none | line | area | bars | ecg
  graphPos: "below",      // below | right
  graphWindow: 60,        // seconds (line, area, bars)
  graphWidth: 15,         // em
  bg: "none",             // none | card | pill
  bgColor: "#000000",
  bgOpacity: 0.55,
  radius: 14,
  border: false,
  zones: false,
  zone1: 120, zone1Color: "#ffb020",
  zone2: 150, zone2Color: "#ff2d2d",
};

export const normalize = d => ({ ...DEFAULTS, ...(d || {}) });

const HEART = "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z";
const ICONS = {
  heart: `<svg viewBox="0 0 24 24"><path fill="currentColor" d="${HEART}"/></svg>`,
  "heart-outline": `<svg viewBox="-1 -1 26 26"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round" d="${HEART}"/></svg>`,
  pulse: `<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" d="M2 12h4l2.5-6 4 12 3-8 1.5 2H22"/></svg>`,
  none: "",
};

const STALE_MS = 6000;
const loadedFonts = new Set();

function loadFont(name) {
  const f = FONTS.find(x => x.name === name);
  if (!f || !f.google || loadedFonts.has(name)) return;
  loadedFonts.add(name);
  const fam = name.replace(/ /g, "+") + (typeof f.google === "string" ? ":" + f.google : "");
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `https://fonts.googleapis.com/css2?family=${fam}&display=swap`;
  document.head.appendChild(link);
}

function rgba(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  const n = m ? parseInt(m[1], 16) : 0;
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}

const CSS = `
.ps-host { display: flex; align-items: flex-start; width: 100%; height: 100%; box-sizing: border-box;
  padding: .5em; font-size: calc(16px * var(--s)); overflow: hidden; }
.ps-host[data-align=center] { justify-content: center; }
.ps-host[data-align=right] { justify-content: flex-end; }
.ps-wrap { display: inline-flex; flex-direction: column; gap: .4em; color: var(--text);
  font-family: var(--font); font-weight: var(--w); }
.ps-wrap[data-gpos=right] { flex-direction: row; align-items: center; gap: .8em; }
.ps-host[data-align=center] .ps-wrap { align-items: center; }
.ps-host[data-align=right] .ps-wrap { align-items: flex-end; }
.ps-wrap[data-bg=card] { background: var(--bg); border-radius: var(--r); padding: .7em 1em; }
.ps-wrap[data-bg=pill] { background: var(--bg); border-radius: 999px; padding: .4em 1.2em .4em .8em; }
.ps-wrap[data-border=true] { box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--c) 60%, transparent); }
.ps-main { display: flex; align-items: center; gap: .4em; }
.ps-wrap[data-layout=stack] .ps-main { flex-direction: column; gap: .15em; }
.ps-icon { color: var(--c); line-height: 0; }
.ps-icon svg { width: 2.6em; height: 2.6em; display: block; transform-origin: 50% 55%; }
.ps-wrap[data-layout=stack] .ps-icon svg { width: 2em; height: 2em; }
.ps-wrap[data-glow=true] .ps-icon svg { filter: drop-shadow(0 0 .35em var(--c)); }
.ps-num { font-size: 4.2em; line-height: .9; font-variant-numeric: tabular-nums; letter-spacing: -.02em;
  min-width: 1.7ch; text-align: right; }
.ps-wrap[data-layout=stack] .ps-num { text-align: center; }
.ps-unit { font-size: 1.05em; font-weight: 700; letter-spacing: .12em; color: var(--c);
  align-self: flex-end; margin-bottom: .45em; white-space: nowrap; }
.ps-wrap[data-layout=stack] .ps-unit { align-self: center; margin: 0; }
.ps-wrap[data-shadow=true] .ps-num, .ps-wrap[data-shadow=true] .ps-unit { text-shadow: 0 .03em .2em rgba(0,0,0,.65); }
.ps-graph { display: block; width: var(--gw); height: 3.2em; }
.ps-wrap[data-anim=beat].ps-live .ps-icon svg { animation: ps-beat var(--period) ease-out infinite; }
.ps-wrap[data-anim=glow].ps-live .ps-icon svg { animation: ps-glow var(--period) ease-in-out infinite; }
@keyframes ps-beat { 0% { transform: scale(1); } 12% { transform: scale(1.18); } 24% { transform: scale(1); }
  36% { transform: scale(1.08); } 55% { transform: scale(1); } }
@keyframes ps-glow { 0%, 100% { opacity: .55; } 15% { opacity: 1; } }
.ps-wrap.ps-stale .ps-num, .ps-wrap.ps-stale .ps-icon, .ps-wrap.ps-stale .ps-graph { opacity: .35; }
.ps-wrap.ps-stale .ps-icon svg { filter: none; }
.ps-hidden { display: none !important; }
`;

export function mount(host) {
  if (!document.getElementById("ps-style")) {
    const s = document.createElement("style");
    s.id = "ps-style"; s.textContent = CSS;
    document.head.appendChild(s);
  }
  host.classList.add("ps-host");
  host.innerHTML = `<div class="ps-wrap ps-stale"><div class="ps-main"><div class="ps-icon"></div>
    <div class="ps-num">--</div><div class="ps-unit"></div></div><canvas class="ps-graph"></canvas></div>`;
  const wrap = host.querySelector(".ps-wrap");
  const iconEl = host.querySelector(".ps-icon");
  const numEl = host.querySelector(".ps-num");
  const unitEl = host.querySelector(".ps-unit");
  const canvas = host.querySelector(".ps-graph");

  let d = normalize();
  let bpm = null, lastT = 0, history = [];
  let color = d.accent;
  const ecg = { buf: null, head: 0, phase: 0, last: 0, carry: 0 };

  function accentFor(v) {
    if (d.zones && v != null) {
      if (v >= d.zone2) return d.zone2Color;
      if (v >= d.zone1) return d.zone1Color;
    }
    return d.accent;
  }

  function applyColor() {
    color = accentFor(bpm);
    host.style.setProperty("--c", color);
  }

  function setDesign(design) {
    d = normalize(design);
    loadFont(d.font);
    const st = host.style;
    st.setProperty("--s", d.size);
    st.setProperty("--text", d.text);
    st.setProperty("--font", `"${d.font}", "Segoe UI", system-ui, sans-serif`);
    st.setProperty("--w", d.weight);
    st.setProperty("--bg", rgba(d.bgColor, d.bgOpacity));
    st.setProperty("--r", d.radius + "px");
    st.setProperty("--gw", d.graphWidth + "em");
    host.dataset.align = d.align;
    Object.assign(wrap.dataset, {
      layout: d.layout, gpos: d.graphPos, bg: d.bg, border: d.border, shadow: d.shadow,
      glow: d.glow, anim: d.iconAnim,
    });
    iconEl.innerHTML = ICONS[d.icon] ?? ICONS.heart;
    iconEl.classList.toggle("ps-hidden", d.icon === "none");
    unitEl.textContent = d.unit;
    unitEl.classList.toggle("ps-hidden", !d.unit);
    canvas.classList.toggle("ps-hidden", d.graph === "none");
    ecg.buf = null;
    applyColor();
    draw(true);
  }

  function show(v, t) {
    bpm = v; lastT = t;
    numEl.textContent = v;
    host.style.setProperty("--period", (60 / Math.max(v, 30)).toFixed(3) + "s");
    applyColor();
  }

  function setReading(v, t) {
    history.push([t, v]);
    if (history.length > 1200) history.splice(0, history.length - 1200);
    show(v, t);
  }

  function setHistory(h) {
    history = (h || []).slice(-1200);
    const last = history[history.length - 1];
    if (last) show(last[1], last[0]);
  }

  const isStale = () => !lastT || Date.now() - lastT > STALE_MS;

  function sizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    return [g, w, h];
  }

  function drawHistory() {
    const [g, w, h] = sizeCanvas();
    const span = d.graphWindow * 1000, now = Date.now();
    const pts = history.filter(p => p[0] >= now - span);
    if (pts.length < 2) return;
    let lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1]));
    if (hi - lo < 20) { const mid = (hi + lo) / 2; lo = mid - 10; hi = mid + 10; }
    const pad = 3;
    const x = t => ((t - (now - span)) / span) * w;
    const y = v => pad + (1 - (v - lo) / (hi - lo)) * (h - pad * 2);

    if (d.graph === "bars") {
      const n = 24, bw = w / n;
      g.fillStyle = color;
      for (let i = 0; i < n; i++) {
        const a = now - span + (i * span) / n, b = a + span / n;
        const vals = pts.filter(p => p[0] >= a && p[0] < b).map(p => p[1]);
        if (!vals.length) continue;
        const v = vals.reduce((s, q) => s + q, 0) / vals.length;
        const top = y(v);
        g.globalAlpha = 0.35 + 0.65 * (i / n);
        g.fillRect(i * bw + 1, top, Math.max(bw - 2, 1), h - top);
      }
      g.globalAlpha = 1;
      return;
    }
    const line = new Path2D();
    pts.forEach((p, i) => i ? line.lineTo(x(p[0]), y(p[1])) : line.moveTo(x(p[0]), y(p[1])));
    if (d.graph === "area") {
      const fill = new Path2D(line);
      fill.lineTo(x(pts[pts.length - 1][0]), h); fill.lineTo(x(pts[0][0]), h); fill.closePath();
      const grad = g.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, rgba(color, 0.45)); grad.addColorStop(1, rgba(color, 0));
      g.fillStyle = grad; g.fill(fill);
    }
    g.lineWidth = 2; g.lineJoin = "round"; g.lineCap = "round"; g.strokeStyle = color;
    if (d.glow) { g.shadowColor = color; g.shadowBlur = 6; }
    else if (d.shadow) { g.shadowColor = "rgba(0,0,0,.5)"; g.shadowBlur = 4; }
    g.stroke(line);
  }

  // Synthetic PQRST wave, phase 0..1 = one heartbeat.
  const gauss = (p, c, w, a) => a * Math.exp(-((p - c) ** 2) / (2 * w * w));
  const wave = p => gauss(p, .12, .025, .12) + gauss(p, .21, .008, -.12) + gauss(p, .23, .01, 1)
    + gauss(p, .25, .01, -.25) + gauss(p, .47, .04, .25);

  function drawEcg(nowMs) {
    const [g, w, h] = sizeCanvas();
    const n = Math.max(Math.round(w), 10);
    if (!ecg.buf || ecg.buf.length !== n) { ecg.buf = new Float32Array(n); ecg.head = 0; ecg.last = nowMs; }
    const speed = n / 4; // samples per second: 4 s across the graph
    const dt = Math.min((nowMs - ecg.last) / 1000, 0.25);
    ecg.last = nowMs;
    ecg.carry += dt * speed;
    const live = !isStale() && bpm;
    while (ecg.carry >= 1) {
      ecg.carry -= 1;
      ecg.phase += live ? (bpm / 60) / speed : 0;
      if (ecg.phase >= 1) ecg.phase -= 1;
      ecg.buf[ecg.head] = live ? wave(ecg.phase) : 0;
      ecg.head = (ecg.head + 1) % n;
    }
    const mid = h * 0.62, amp = h * 0.55, gap = Math.round(n * 0.04);
    g.lineWidth = 2; g.lineJoin = "round"; g.strokeStyle = color;
    if (d.glow) { g.shadowColor = color; g.shadowBlur = 6; }
    g.beginPath();
    let pen = false;
    for (let i = 0; i < n; i++) {
      const ahead = (i - ecg.head + n) % n;
      if (ahead < gap) { pen = false; continue; }
      const yy = mid - ecg.buf[i] * amp;
      pen ? g.lineTo(i, yy) : g.moveTo(i, yy);
      pen = true;
    }
    g.stroke();
    const hx = (ecg.head - 1 + n) % n;
    g.fillStyle = color;
    g.beginPath(); g.arc(hx, mid - ecg.buf[hx] * amp, 2.5, 0, Math.PI * 2); g.fill();
  }

  let lastSlow = 0, raf = 0;
  function draw(force) {
    if (d.graph === "none") return;
    const now = performance.now();
    if (d.graph === "ecg") return drawEcg(now);
    if (force || now - lastSlow > 100) { lastSlow = now; drawHistory(); }
  }

  function frame() {
    const stale = isStale();
    wrap.classList.toggle("ps-stale", stale);
    wrap.classList.toggle("ps-live", !stale);
    draw(false);
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  setDesign(d);
  return { setDesign, setReading, setHistory, destroy: () => cancelAnimationFrame(raf) };
}
