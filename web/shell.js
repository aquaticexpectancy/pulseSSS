// App chrome shared by Live, Design and Setup: sidebar navigation plus live status.

const ICON = {
  heart: '<path fill="currentColor" d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>',
  live: '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M3 12h4l2.5-6 4 12 3-8 1.5 2H21"/>',
  design: '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.6-.8 1.6-1.6 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5c0-4-4-7.4-9-7.4z"/></g><circle cx="7.5" cy="11" r="1.3" fill="currentColor"/><circle cx="10.5" cy="7" r="1.3" fill="currentColor"/><circle cx="15" cy="7.5" r="1.3" fill="currentColor"/>',
  setup: '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></g>',
};
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

const NAV = [["/", "Live", "live"], ["/design", "Design", "design"], ["/setup", "Setup", "setup"]];
export const LABELS = {
  connected: "Connected", scanning: "Looking for Fitbit Air", connecting: "Connecting",
  disconnected: "Reconnecting", starting: "Starting", offline: "App not running",
  paused: "Disconnected", waiting: "Waiting for your phone", "no-token": "Pulsoid token needed",
  "bad-token": "Pulsoid refused the token", "no-phone": "Phone not paired",
};

export function mountShell() {
  const side = document.createElement("aside");
  side.className = "side";
  side.innerHTML = `
    <div class="brand">${svg("heart")}<span>pulse<b>SSS</b></span></div>
    <nav class="sidenav">${NAV.map(([href, label, icon]) =>
      `<a href="${href}" class="${location.pathname === href ? "on" : ""}">${svg(icon)}<span>${label}</span></a>`).join("")}
    </nav>
    <div class="side-status">
      <div class="mini-bpm" id="sb-bpm-wrap"><span id="sb-bpm">--</span><small>BPM</small></div>
      <div class="pill" id="sb-pill" data-s="offline"><i class="dot"></i><span id="sb-status">Connecting</span></div>
    </div>`;
  document.body.prepend(side);
  document.body.classList.add("shell");

  const $ = id => document.getElementById(id);
  let lastT = 0;
  const setStatus = (s, device) => {
    $("sb-pill").dataset.s = s;
    $("sb-status").textContent = LABELS[s] || s;
    $("sb-pill").title = device || "";
  };
  setInterval(() => { $("sb-bpm-wrap").style.opacity = lastT && Date.now() - lastT < 6000 ? 1 : .35; }, 1000);

  (function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.type === "hello") { setStatus(m.status, m.device); if (m.bpm != null) { lastT = m.t; $("sb-bpm").textContent = m.bpm; } }
      else if (m.type === "status") setStatus(m.status, m.device);
      else if (m.type === "hr") { lastT = m.t; $("sb-bpm").textContent = m.bpm; }
    };
    ws.onclose = () => { setStatus("offline"); setTimeout(connect, 2000); };
  })();
}

mountShell();
