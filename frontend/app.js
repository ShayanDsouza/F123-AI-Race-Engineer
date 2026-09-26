/* Sim vs Reality — app logic */
"use strict";

const $ = (id) => document.getElementById(id);
const api = async (path, opts) => {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
};

const state = {
  mode: "fastest",   // fastest | lapforlap | manual
  laps: [], events: [], drivers: [],
  selectedLap: null,
  listenerRunning: false,
  lapsInitialised: false,
  lapCount: 0,
};

// ------------------------------------------------------------ formatting ----
function fmtLap(ms) {
  if (ms == null) return "—";
  const m = Math.floor(ms / 60000), s = ((ms % 60000) / 1000).toFixed(3);
  return `${m}:${s.padStart(6, "0")}`;
}
function fmtDelta(ms) {
  if (ms == null) return "—";
  const sign = ms < 0 ? "−" : "+";
  return `${sign}${(Math.abs(ms) / 1000).toFixed(3)}`;
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// -------------------------------------------------------------- listener ----
async function toggleListener() {
  try {
    if (state.listenerRunning) {
      await api("/api/listener/stop", { method: "POST" });
    } else {
      await api("/api/listener/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: parseInt($("port-input").value, 10) || 20777 }),
      });
    }
  } catch (e) {
    $("live-status").innerHTML = `<span class="hint err">${esc(e.message)}</span>`;
  }
  pollStatus();
}

let pollTick = 0;
async function pollStatus() {
  let st;
  try { st = await api("/api/status"); } catch (_) { return; }
  state.listenerRunning = st.listener.running;
  $("listener-btn").textContent = st.listener.running ? "Stop" : "Start listening";
  const live = st.live || {};
  const dot = $("listener-dot");
  dot.className = "dot" + (st.listener.running ? (live.receiving ? " rx" : " on") : "");

  const el = $("live-status");
  if (!st.listener.running) {
    el.textContent = "Listener stopped. Set your console's UDP telemetry to this PC's IP, port " + ($("port-input").value || 20777) + ".";
  } else if (!live.receiving) {
    el.innerHTML = `Listening on UDP ${st.listener.port}… no packets yet.` +
      (live.wrong_format_seen ? `<br><span class="hint err">Got packet format ${live.wrong_format_seen} — set UDP Format to 2023 in game options.</span>` : "");
  } else {
    const s = live.session, c = live.car, l = live.lap;
    el.innerHTML =
      `<div>${esc(s ? `${s.track} — ${s.type}` : "Receiving…")} · ` +
      `${live.telemetry_hz ? live.telemetry_hz + " Hz · " : ""}${live.packets_seen.toLocaleString()} packets</div>` +
      (c ? `<div class="live-grid">
        <div class="live-cell"><div class="v">${c.speed ?? "—"}</div><div class="k">km/h</div></div>
        <div class="live-cell"><div class="v">${c.gear > 0 ? c.gear : c.gear === 0 ? "N" : "R"}</div><div class="k">gear</div></div>
        <div class="live-cell"><div class="v">${l ? fmtLap(l.current_time_ms) : "—"}</div><div class="k">lap ${l ? l.number : ""}</div></div>
        <div class="live-cell"><div class="v">${c.tyre ?? "—"}</div><div class="k">tyre</div></div>
        <div class="live-cell"><div class="v">${c.fuel_laps == null ? "—" : (c.fuel_laps > 0 ? "+" : "") + c.fuel_laps}</div><div class="k">fuel Δ laps</div></div>
        <div class="live-cell"><div class="v">${c.ers_pct ?? "—"}%</div><div class="k">ers</div></div>
      </div>` : "");
    if (++pollTick % 3 === 0) loadLaps();  // pick up freshly recorded laps
  }
  maybeAutoCall(live);
  raceTick(live);
}

// ------------------------------------------------------------------ laps ----
// Selection narrows: race weekend -> session -> your lap -> reference.
// Showing every lap you have ever driven, across every circuit, invites
// comparing things that cannot be compared.
function selectedTrack() { return $("track-select").value || null; }

function lapsForTrack() {
  const t = selectedTrack();
  return state.laps.filter((l) => !t || l.track_name === t);
}

function lapsForSelection() {
  const sess = $("session-filter").value;
  return lapsForTrack().filter((l) => !sess || l.session_type === sess);
}

function rebuildTrackOptions() {
  const sel = $("track-select");
  const seen = [];
  for (const l of state.laps) {                 // already newest-first
    if (!seen.includes(l.track_name)) seen.push(l.track_name);
  }
  const cur = sel.value;
  sel.innerHTML = seen.map((t) => {
    const n = state.laps.filter((l) => l.track_name === t).length;
    return `<option value="${esc(t)}">${esc(t)} — ${n} lap${n === 1 ? "" : "s"}</option>`;
  }).join("");
  if (cur && seen.includes(cur)) sel.value = cur;
  return seen;
}

function rebuildSessionOptions() {
  const sel = $("session-filter");
  const cur = sel.value;
  const seen = [];
  for (const l of lapsForTrack()) {
    if (!seen.includes(l.session_type)) seen.push(l.session_type);
  }
  sel.innerHTML = `<option value="">All sessions</option>` + seen.map((x) =>
    `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  if (cur && seen.includes(cur)) sel.value = cur;
}

function rebuildLapOptions() {
  const sel = $("game-lap-select");
  const cur = sel.value;
  const pool = lapsForSelection();
  sel.innerHTML = pool.map((l) =>
    `<option value="${esc(l.id)}">L${l.lap_number} — ${fmtLap(l.lap_time_ms)}` +
    ` (${esc(l.session_type)}${l.tyre ? ", " + esc(l.tyre) : ""}` +
    `${l.invalid ? ", invalid" : ""})</option>`).join("");
  if (cur && pool.some((l) => l.id === cur)) sel.value = cur;
  else if (state.mode === "fastest") {
    const best = pool.filter((l) => !l.invalid && l.lap_time_ms)
      .reduce((b, l) => (!b || l.lap_time_ms < b.lap_time_ms ? l : b), null);
    if (best) sel.value = best.id;
  }
  $("no-laps-hint").style.display = state.laps.length ? "none" : "block";
}

function onTrackChange() {
  rebuildSessionOptions();
  rebuildLapOptions();
  onGameLapPicked();
  if (refSource() === "ingame") loadIngameLaps();
}

async function loadLaps() {
  try { state.laps = await api("/api/laps"); } catch (_) { return; }
  rebuildTrackOptions();
  rebuildSessionOptions();
  rebuildLapOptions();
  if (!state.lapsInitialised && state.laps.length) {
    state.lapsInitialised = true;   // resolve the weekend once, not every poll
    onGameLapPicked();
  }

  const ul = $("lap-list");
  if (ul) {
  ul.innerHTML = "";
  for (const l of state.laps.slice(0, 40)) {
    const li = document.createElement("li");
    li.innerHTML =
      `<button class="del" title="delete lap">✕</button>` +
      `<span class="t">${esc(l.track_name)} · ${fmtLap(l.lap_time_ms)}</span><br>` +
      `<span class="m">${esc(l.session_type)} · lap ${l.lap_number}` +
      `${l.tyre ? " · " + esc(l.tyre) : ""}${l.invalid ? " · ⚠ invalid" : ""}</span>`;
    li.addEventListener("click", (e) => {
      if (e.target.classList.contains("del")) return;
      if (state.mode === "fastest") setMode("manual");  // honour the explicit pick
      $("track-select").value = l.track_name;
      rebuildSessionOptions();
      $("session-filter").value = l.session_type;
      rebuildLapOptions();
      $("game-lap-select").value = l.id;
      onGameLapPicked();
      document.querySelectorAll(".lap-list li").forEach((x) => x.classList.remove("sel"));
      li.classList.add("sel");
    });
    li.querySelector(".del").addEventListener("click", async () => {
      if (!confirm("Delete this recorded lap?")) return;
      try { await api("/api/laps/" + encodeURIComponent(l.id), { method: "DELETE" }); } catch (_) {}
      loadLaps();
    });
    ul.appendChild(li);
  }
  }
}

// A lap can only be compared against its own circuit, so once a game lap is
// chosen the event list collapses to the one matching Grand Prix.
function onGameLapPicked() {
  const lap = state.laps.find((l) => l.id === $("game-lap-select").value);
  const sel = $("event-select");
  if (!lap) {
    restoreEventOptions();
    return;
  }
  if (!lap.suggested_event) {
    sel.innerHTML = `<option>No 2023 equivalent</option>`;
    sel.disabled = true;
    $("track-event").textContent = "";
    $("compare-btn").disabled = refSource() === "real";
    $("compare-msg").innerHTML =
      `<span class="err">${esc(lap.track_name)} wasn't on the 2023 F1 calendar` +
      ` (or is a short layout), so there's no real telemetry to compare against.` +
      ` Drive a circuit that raced in 2023.</span>`;
    return;
  }
  sel.disabled = false;
  $("compare-btn").disabled = false;
  $("track-event").textContent = `Real-world equivalent: ${lap.suggested_event} (2023).`;
  $("compare-msg").textContent = "";
  const ev = state.events.find((e) => e.name === lap.suggested_event);
  sel.innerHTML = ev
    ? `<option value="${esc(ev.name)}">R${ev.round} — ${esc(ev.name)}</option>`
    : `<option value="${esc(lap.suggested_event)}">${esc(lap.suggested_event)}</option>`;
  sel.value = lap.suggested_event;
  onEventChange();
  if (refSource() === "ingame") loadIngameLaps();
}

function restoreEventOptions() {
  const sel = $("event-select");
  const cur = sel.value;
  sel.disabled = false;
  sel.innerHTML = state.events.map((ev) =>
    `<option value="${esc(ev.name)}">R${ev.round} — ${esc(ev.name)}${ev.format.includes("sprint") ? " (Sprint)" : ""}</option>`
  ).join("");
  if (state.events.some((e) => e.name === cur)) sel.value = cur;
}

// ------------------------------------------------------------- real data ----
async function loadEvents() {
  try { state.events = await api("/api/real/events"); } catch (e) {
    $("compare-msg").innerHTML = `<span class="err">Could not load 2023 calendar: ${esc(e.message)}</span>`;
    return;
  }
  $("event-select").innerHTML = state.events.map((ev) =>
    `<option value="${esc(ev.name)}">R${ev.round} — ${esc(ev.name)}${ev.format.includes("sprint") ? " (Sprint)" : ""}</option>`
  ).join("");
  onEventChange();
}

function currentEvent() {
  return state.events.find((ev) => ev.name === $("event-select").value);
}

function onEventChange() {
  const ev = currentEvent();
  if (!ev) return;
  const cur = $("session-select").value;
  $("session-select").innerHTML = ev.sessions.map((s) =>
    `<option value="${s}">${{ FP1: "Practice 1", FP2: "Practice 2", FP3: "Practice 3", Q: "Qualifying", SS: "Sprint Shootout", S: "Sprint", R: "Race" }[s] || s}</option>`
  ).join("");
  $("session-select").value = ev.sessions.includes(cur) ? cur : (ev.sessions.includes("Q") ? "Q" : ev.sessions[0]);
  onSessionChange();
}

async function onSessionChange() {
  const ev = currentEvent();
  if (!ev) return;
  const sess = $("session-select").value;
  $("driver-select").innerHTML = `<option>Loading drivers…</option>`;
  $("compare-btn").disabled = true;
  try {
    state.drivers = await api(`/api/real/drivers?event=${encodeURIComponent(ev.name)}&session=${sess}`);
  } catch (e) {
    $("driver-select").innerHTML = `<option>—</option>`;
    $("compare-msg").innerHTML = `<span class="hint err">Drivers: ${esc(e.message)}</span>`;
    $("compare-btn").disabled = false;
    return;
  }
  const opts = state.drivers.map((d) =>
    `<option value="${esc(d.abbreviation)}">${d.position ? d.position + ". " : ""}${esc(d.abbreviation)} — ${esc(d.name)} (${esc(d.team)})</option>`
  ).join("");
  $("driver-select").innerHTML = opts;
  $("compare-btn").disabled = false;
  $("compare-msg").textContent = "";
  loadRefLaps("driver-select", "ref-lap-select");
  loadSessionFastest();
}

// The session's overall fastest lap — whoever set it — offered as a one-click
// reference alongside the per-driver fastest.
async function loadSessionFastest() {
  const ev = currentEvent();
  const hint = $("fastest-hint");
  hint.style.display = "none";
  state.fastest = null;
  if (!ev) return;
  const sess = $("session-select").value;
  try {
    const f = await api(`/api/real/fastest?event=${encodeURIComponent(ev.name)}&session=${sess}`);
    state.fastest = f;
    if (!state.drivers.some((d) => d.abbreviation === f.driver)) return;
    hint.style.display = "block";
    hint.innerHTML =
      `⚡ Session fastest: <b>${esc(f.driver)}</b> L${f.lap_number} — ` +
      `${fmtLap(f.lap_time_ms)}${f.compound && f.compound !== "Nan" ? " · " + esc(f.compound) : ""} ` +
      `<button id="use-fastest" class="btn btn-mini">Use this</button>`;
    $("use-fastest").addEventListener("click", async () => {
      $("driver-select").value = f.driver;
      await loadRefLaps("driver-select", "ref-lap-select");
      $("ref-lap-select").value = String(f.lap_number);
    });
  } catch (_) { /* session may have no timed laps */ }
}

// --- in-game AI reference -------------------------------------------------
function refSource() { return $("ref-source-select").value; }

function onRefSourceChange() {
  const ingame = refSource() === "ingame";
  $("real-block").style.display = ingame ? "none" : "block";
  $("ingame-block").style.display = ingame ? "block" : "none";
  $("compare-msg").textContent = "";
  $("compare-btn").disabled = false;
  if (ingame) loadIngameLaps();
}

async function loadIngameLaps() {
  const mine = state.laps.find((l) => l.id === $("game-lap-select").value);
  const sel = $("ingame-lap-select");
  let opp = [];
  try { opp = await api("/api/laps?scope=opponent"); } catch (_) {}
  state.opponents = opp;

  // Only AI laps from the same circuit can be overlaid.
  const usable = mine ? opp.filter((l) => l.track_id === mine.track_id) : opp;
  const sameSession = mine ? usable.filter((l) => l.session_uid === mine.session_uid) : [];
  const pool = sameSession.length ? sameSession : usable;

  if (!pool.length) {
    sel.innerHTML = `<option value="">No AI laps recorded yet</option>`;
    $("ingame-hint").innerHTML = mine
      ? `<span class="err">No AI laps recorded at ${esc(mine.track_name)} yet. ` +
        `The game broadcasts every car — drive a session with AI opponents and ` +
        `their laps are captured automatically.</span>`
      : "Record a lap of your own first.";
    return;
  }
  // Fastest lap per driver first, then the rest.
  const best = {};
  for (const l of pool) {
    if (!l.lap_time_ms) continue;
    if (!best[l.driver] || l.lap_time_ms < best[l.driver].lap_time_ms) best[l.driver] = l;
  }
  const bestIds = new Set(Object.values(best).map((l) => l.id));
  const opt = (l, star) =>
    `<option value="${esc(l.id)}">${esc(l.driver)} — L${l.lap_number} ` +
    `${fmtLap(l.lap_time_ms)}${star ? " ★ best" : ""}${l.invalid ? " (invalid)" : ""}</option>`;
  sel.innerHTML =
    Object.values(best).sort((a, b) => a.lap_time_ms - b.lap_time_ms).map((l) => opt(l, true)).join("") +
    pool.filter((l) => !bestIds.has(l.id))
        .sort((a, b) => (a.lap_time_ms || 1e9) - (b.lap_time_ms || 1e9))
        .map((l) => opt(l, false)).join("");
  $("ingame-hint").textContent =
    `${pool.length} AI lap${pool.length === 1 ? "" : "s"} recorded` +
    (sameSession.length ? " in this session." : " at this track.");
}

async function loadRefLaps(driverSelId, lapSelId) {
  const ev = currentEvent();
  const driver = $(driverSelId).value;
  const lapSel = $(lapSelId);
  lapSel.innerHTML = `<option value="">Fastest lap</option>`;
  if (!ev || !driver || driver.includes("…")) return;
  try {
    const laps = await api(`/api/real/laps?event=${encodeURIComponent(ev.name)}&session=${$("session-select").value}&driver=${driver}`);
    lapSel.innerHTML = `<option value="">Fastest lap</option>` + laps
      .filter((l) => l.lap_time_ms)
      .map((l) => `<option value="${l.lap_number}">L${l.lap_number} — ${fmtLap(l.lap_time_ms)}` +
        `${l.compound && l.compound !== "Nan" ? " · " + esc(l.compound) : ""}` +
        `${l.is_fastest ? " ★" : ""}${l.deleted ? " (deleted)" : ""}</option>`).join("");
  } catch (_) { /* fastest-only is fine */ }
}

// --------------------------------------------------------------- compare ----
async function runCompare() {
  const ev = currentEvent();
  if (!ev && refSource() === "real") return;
  const sess = $("session-select").value;
  const source = $("source-select").value;
  const driver = $("driver-select").value;
  const lapId = $("game-lap-select").value;
  if (!lapId) {
    $("compare-msg").innerHTML = `<span class="err">Record a lap first (or run the simulator).</span>`;
    return;
  }
  // Which reference lap depends on the pairing mode.
  let refLap = "";
  if (state.mode === "manual") {
    refLap = $("ref-lap-select").value;
  } else if (state.mode === "lapforlap") {
    const mine = state.laps.find((l) => l.id === lapId);
    if (!mine) return;
    refLap = String(mine.lap_number);
  }
  let url;
  if (refSource() === "ingame") {
    const refId = $("ingame-lap-select").value;
    if (!refId) {
      $("compare-msg").innerHTML = `<span class="err">Pick an in-game AI lap first.</span>`;
      return;
    }
    url = `/api/compare_ingame?lap_id=${encodeURIComponent(lapId)}` +
          `&ref_lap_id=${encodeURIComponent(refId)}`;
  } else {
    url = `/api/compare?lap_id=${encodeURIComponent(lapId)}&event=${encodeURIComponent(ev.name)}` +
          `&session=${sess}&driver=${driver}&source=${source}`;
    if (refLap) url += `&ref_lap=${refLap}`;
  }
  $("empty-state").style.display = "none";
  $("results").style.display = "none";
  $("loading").style.display = "flex";
  $("loading-msg").textContent = "Fetching real telemetry — first load of a session downloads its data and can take a minute…";
  $("compare-btn").disabled = true;
  $("compare-msg").textContent = "";
  try {
    const result = await api(url);
    renderResult(result);
  } catch (e) {
    $("loading").style.display = "none";
    $("empty-state").style.display = "block";
    $("compare-msg").innerHTML = `<span class="hint err">${esc(e.message)}</span>`;
  }
  $("compare-btn").disabled = false;
}

// ---------------------------------------------------------------- render ----
const YOU = "#3987e5", REF = "#199e70";

// Draw the circuit for a Compare result: each corner placed on the real 2023
// outline, coloured by how much time you lose there, with a "brake here" tick
// at the reference braking point wherever you brake too early.
async function renderCompareMap(r) {
  const panel = $("cmp-map-panel");
  panel.style.display = "none";
  const ev = r.map_event;
  const corners = r.corners || [];
  const refLen = (r.stats || {}).ref_lap_len_m || 0;
  if (!ev || !corners.length || !refLen) return;

  let outline;
  try {
    outline = await api(`/api/real/trackmap?event=${encodeURIComponent(ev)}&session=Q`);
  } catch (_) { return; }
  if (!outline || !outline.points || !outline.points.length) return;

  panel.style.display = "block";
  $("cmp-map-note").textContent = `${esc(ev)} · real 2023 layout · red = your biggest losses`;

  const canvas = $("cmp-map"), wrap = $("cmp-map-wrap");
  const dpr = window.devicePixelRatio || 1;
  const Wd = wrap.clientWidth || 640, Ht = Math.round(Wd * 0.6);
  canvas.width = Wd * dpr; canvas.height = Ht * dpr; canvas.style.height = Ht + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, Wd, Ht);
  const pad = 46;
  const PX = (nx) => pad + nx * (Wd - 2 * pad);
  const PY = (ny) => pad + ny * (Ht - 2 * pad);

  // ribbon
  ctx.beginPath();
  outline.points.forEach((p, i) => { const X = PX(p[0]), Y = PY(p[1]); i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); });
  ctx.closePath();
  ctx.strokeStyle = "#3a3a37"; ctx.lineWidth = 10; ctx.lineJoin = "round"; ctx.stroke();
  ctx.strokeStyle = "#5f5f5a"; ctx.lineWidth = 2; ctx.stroke();

  const at = (frac) => {
    let best = outline.points[0], bd = 9;
    for (const p of outline.points) { const d = Math.abs(p[2] - ((frac % 1 + 1) % 1)); if (d < bd) { bd = d; best = p; } }
    return best;
  };
  // start/finish
  ctx.fillStyle = "#c3c2b7";
  const s0 = outline.points[0];
  ctx.beginPath(); ctx.arc(PX(s0[0]), PY(s0[1]), 4, 0, 7); ctx.fill();

  const worst = Math.max(0.05, ...corners.map((c) => c.time_delta_s));

  // "brake here" ticks first (under the dots)
  for (const c of corners) {
    const yb = c.you && c.you.brake_start_m, rb = c.ref && c.ref.brake_start_m;
    if (c.time_delta_s > 0.03 && yb != null && rb != null && yb - rb > 6) {
      const bf = c.apex_frac - rb / refLen;      // reference braking point
      const p = at(bf);
      // small green diamond = brake HERE (you currently brake earlier)
      const X = PX(p[0]), Y = PY(p[1]);
      ctx.save(); ctx.translate(X, Y); ctx.rotate(Math.PI / 4);
      ctx.fillStyle = "#199e70"; ctx.strokeStyle = "#1a1a19"; ctx.lineWidth = 1.5;
      ctx.fillRect(-4, -4, 8, 8); ctx.strokeRect(-4, -4, 8, 8);
      ctx.restore();
    }
  }

  // corner apex dots
  for (const c of corners) {
    const p = at(c.apex_frac);
    const X = PX(p[0]), Y = PY(p[1]);
    const loss = c.time_delta_s;
    const col = loss <= 0.03 ? "#199e70" : loss < worst * 0.5 ? "#fab219" : "#e66767";
    const rad = loss <= 0.03 ? 5 : 6 + Math.min(6, (loss / worst) * 6);
    ctx.beginPath(); ctx.arc(X, Y, rad, 0, 7);
    ctx.fillStyle = col; ctx.fill();
    ctx.strokeStyle = "#1a1a19"; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.font = "bold 10px system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("T" + c.corner, X, Y - rad - 7);
  }

  let legend = wrap.querySelector(".db-map-legend");
  if (!legend) { legend = document.createElement("div"); legend.className = "db-map-legend"; wrap.appendChild(legend); }
  legend.innerHTML =
    `<span><i style="background:#e66767"></i>biggest loss</span>` +
    `<span><i style="background:#fab219"></i>smaller loss</span>` +
    `<span><i style="background:#199e70"></i>on/ahead &nbsp;·&nbsp; green diamond = brake here (later)</span>` +
    `<span>● start/finish</span>`;
}

function renderResult(r) {
  $("loading").style.display = "none";
  $("results").style.display = "block";
  destroyCharts();
  renderCompareMap(r);

  const my = r.meta.you, rf = r.meta.ref;
  $("warnings").innerHTML = (r.warnings || []).map((w) =>
    `<div class="warn-banner w${w.severity}"><span class="icon">${w.severity >= 3 ? "⚠" : "ℹ"}</span>` +
    `<span><b>${w.severity >= 3 ? "Not a like-for-like comparison" : "Worth knowing"}:</b> ${esc(w.text)}</span></div>`
  ).join("");
  const youLabel = my.driver ? my.driver : "You";
  const youDesc = my.driver
    ? `${esc(my.driver_name || my.driver)} L${my.lap_number} (${esc(my.compound || "?")})`
    : `${esc(my.track_name || "")} ${esc(my.session_type || "")} lap ${my.lap_number}${my.tyre ? " on " + esc(my.tyre) + "s" : ""}${my.invalid ? " ⚠ invalid" : ""}`;
  const refDesc = `${esc(rf.driver_name || rf.driver)} — ${esc(rf.team || "")} · ${esc(rf.event)} ${esc(rf.session)} L${rf.lap_number}${rf.compound ? " (" + esc(rf.compound) + ")" : ""} · ${esc(rf.source)}`;
  $("matchup").innerHTML =
    `<span class="sw" style="background:${YOU}"></span><span class="who">${esc(youLabel)}</span> <span>${youDesc}</span>` +
    ` &nbsp;vs&nbsp; <span class="sw" style="background:${REF}"></span><span class="who">${esc(rf.driver)}</span> <span>${refDesc}</span>`;

  const aName = my.driver || "You";
  const st = r.stats;
  const gap = st.lap_time_ms_you != null && st.lap_time_ms_ref != null
    ? st.lap_time_ms_you - st.lap_time_ms_ref : null;
  $("stat-tiles").innerHTML = [
    tile(my.driver ? esc(my.driver) + " lap" : "Your lap", fmtLap(st.lap_time_ms_you)),
    tile(`${esc(rf.driver)} lap`, fmtLap(st.lap_time_ms_ref)),
    tile("Gap", gap == null ? "—" : fmtDelta(gap) + "s", gap == null ? "" : gap <= 0 ? "gain" : "loss"),
    tile("Top speed", `${st.top_speed_you} <span class="s">/ ${st.top_speed_ref} km/h</span>`),
    tile("Full throttle", `${st.full_throttle_pct_you}% <span class="s">/ ${st.full_throttle_pct_ref}%</span>`),
    tile("DRS open", `${st.drs_pct_you}% <span class="s">/ ${st.drs_pct_ref}%</span>`),
  ].join("");

  // Without official sector times from both laps the split is derived from
  // distance, so label it honestly rather than printing 0:00.000.
  $("sector-tiles").innerHTML = r.sectors.map((s) =>
    `<div class="tile"><div class="k">${s.exact ? "Sector" : "Split"} ${s.sector}</div>` +
    `<div class="v ${s.delta_ms <= 0 ? "gain" : "loss"}">${fmtDelta(s.delta_ms)}s</div>` +
    `<div class="s">${s.exact ? `${fmtLap(s.you_ms)} vs ${fmtLap(s.ref_ms)}`
                              : "approx — by distance"}</div></div>`
  ).join("");

  const corners = r.corners.map((c) => ({ frac: c.apex_frac, label: "T" + c.corner }));
  const sectors = r.sectors.map((s) => ({ from: s.from_frac, to: s.to_frac }));
  const refLen = st.ref_lap_len_m;
  const common = { grid: r.grid, refLen, sectors };

  new TelemetryChart({ ...common, el: $("chart-speed"), height: 240, yLabel: "Speed (km/h)",
    corners, unit: "km/h", yFmt: (v) => Math.round(v),
    series: [{ name: aName, color: YOU, values: r.you.speed },
             { name: rf.driver, color: REF, values: r.ref.speed }] });

  new TelemetryChart({ ...common, el: $("chart-delta"), height: 150,
    yLabel: "Delta to " + rf.driver + " (s)  — red = you lose time", signFill: true,
    unit: "s", yFmt: (v) => (v > 0 ? "+" : "") + v.toFixed(2),
    series: [{ name: "Δ time", color: "#c3c2b7", values: r.delta }] });

  new TelemetryChart({ ...common, el: $("chart-throttle"), height: 130, yLabel: "Throttle (%)",
    yMin: 0, yMax: 1.02, unit: "%", yFmt: (v) => Math.round(v * 100),
    series: [{ name: aName, color: YOU, values: r.you.throttle },
             { name: rf.driver, color: REF, values: r.ref.throttle }] });

  new TelemetryChart({ ...common, el: $("chart-brake"), height: 130, yLabel: "Brake (%)",
    yMin: 0, yMax: 1.02, unit: "%", yFmt: (v) => Math.round(v * 100),
    series: [{ name: aName, color: YOU, values: r.you.brake },
             { name: rf.driver, color: REF, values: r.ref.brake }] });

  new TelemetryChart({ ...common, el: $("chart-gear"), height: 120, yLabel: "Gear",
    yMin: 0.5, yMax: 8.5, xAxis: true, yFmt: (v) => Math.round(v),
    series: [{ name: aName, color: YOU, values: r.you.gear, step: true },
             { name: rf.driver, color: REF, values: r.ref.gear, step: true }] });

  // corner table
  const tbody = $("corner-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const c of r.corners) {
    const tr = document.createElement("tr");
    const bp = (v) => v == null ? "—" : `${v}<span class="u"> m</span>`;
    tr.innerHTML =
      `<td>T${c.corner} <span class="u">(${c.apex_m} m)</span></td>` +
      `<td>${bp(c.you.brake_start_m)}</td><td>${bp(c.ref.brake_start_m)}</td>` +
      `<td>${c.you.min_speed}<span class="u"> km/h</span></td><td>${c.ref.min_speed}<span class="u"> km/h</span></td>` +
      `<td>${c.you.apex_gear} / ${c.ref.apex_gear}</td>` +
      `<td>${bp(c.you.full_throttle_after_m)} / ${bp(c.ref.full_throttle_after_m)}</td>` +
      `<td class="${c.time_delta_s <= 0 ? "gain" : "loss"}">${c.time_delta_s > 0 ? "+" : ""}${c.time_delta_s.toFixed(2)}s</td>`;
    tr.addEventListener("mouseenter", () => setHighlight(c.seg));
    tr.addEventListener("mouseleave", () => setHighlight(null));
    tbody.appendChild(tr);
  }

  const sevName = { 3: "Fix first", 2: "Work on", 1: "Note", 0: "Good" };
  $("insights").innerHTML = (r.insights || []).map((n) =>
    `<li><span class="chip s${n.severity}">${sevName[n.severity] || "Note"}</span><span>${esc(n.text)}</span></li>`
  ).join("") || "<li>No notes.</li>";

  const kindName = { reference: "Reference", your_car: "Your car", fuel: "Fuel",
                     tyres: "Tyres", real_strategy: "Real 2023", advice: "Approach" };
  if ($("strategy")) $("strategy").innerHTML = (r.strategy || []).map((n) =>
    `<li><span class="chip s1">${kindName[n.kind] || "Note"}</span><span>${esc(n.text)}</span></li>`
  ).join("") || "<li>No strategy notes for this session.</li>";
}

function tile(k, v, cls = "") {
  return `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
}

// -------------------------------------------------------- race engineer ----
const PIT_LABEL = {
  stay_out: "Stay out", pit_window_open: "Pit window open",
  box_within_3_laps: "Box within 3 laps", box_next_lap: "Box next lap",
  box_this_lap: "BOX THIS LAP",
};

async function engineerStatus() {
  let st;
  try { st = await api("/api/engineer/status"); } catch (_) { return; }
  state.engineerReady = st.available;
  $("eng-dot").className = "dot" + (st.available ? " on" : "");
  $("eng-status").innerHTML = st.available
    ? `Ready — powered by Google Gemini` +
      `${st.key_source === "file" ? ` (key saved in the app · <a href="#" id="eng-key-forget">forget</a>)` : ""}. ` +
      `Get on track, then hit Radio, or use race mode for spoken calls while you drive.`
    : `<span class="err">No Gemini key. Paste one below (stored locally in ` +
      `<code>data/secrets.json</code>), or set <code>${esc(st.key_env)}</code> and ` +
      `restart. Free keys: <b>aistudio.google.com/apikey</b>.</span>`;
  $("eng-key-row").style.display = st.available ? "none" : "flex";
  $("eng-btn").disabled = !st.available;
  const forget = $("eng-key-forget");
  if (forget) forget.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!confirm("Forget the saved Gemini key?")) return;
    try { await api("/api/engineer/key", { method: "DELETE" }); } catch (_) {}
    engineerStatus();
  });
}

async function saveEngineerKey() {
  const key = $("eng-key").value.trim();
  if (!key) return;
  $("eng-key-btn").disabled = true;
  try {
    await api("/api/engineer/key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    $("eng-key").value = "";
    $("eng-key-msg").textContent = "";
    await engineerStatus();
  } catch (e) {
    $("eng-key-msg").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
  $("eng-key-btn").disabled = false;
}

let engBusy = false;
async function callEngineer(question) {
  if (engBusy || !state.engineerReady) return;
  engBusy = true;
  $("eng-btn").disabled = true;
  $("eng-btn").textContent = "…";
  try {
    const advice = await api("/api/engineer/advice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question || null }),
    });
    renderAdvice(advice);
    $("eng-question").value = "";
  } catch (e) {
    const li = document.createElement("li");
    li.className = "eng-msg";
    li.innerHTML = `<div class="meta err">${esc(e.message)}</div>`;
    $("eng-feed").prepend(li);
  }
  engBusy = false;
  $("eng-btn").disabled = false;
  $("eng-btn").textContent = "Radio";
}

function renderAdvice(a) {
  const li = document.createElement("li");
  li.className = "eng-msg " + esc(a.urgency || "info");
  const tips = (a.driving || []).map((t) => `<li>${esc(t)}</li>`).join("");
  li.innerHTML =
    `<div class="radio">“${esc(a.radio)}”</div>` +
    `<div class="pit"><b>${esc(PIT_LABEL[a.pit.recommendation] || a.pit.recommendation)}</b>` +
    `${a.pit.compound && a.pit.compound !== "none" ? " · " + esc(a.pit.compound) : ""}` +
    `${a.pit.target_lap ? " · target lap " + a.pit.target_lap : ""}` +
    ` — ${esc(a.pit.reason)}</div>` +
    (tips ? `<ul>${tips}</ul>` : "") +
    `<div class="meta">${esc(a.tyre_outlook || "")}</div>` +
    `<div class="meta">Lap ${a.lap ?? "—"} · ${new Date((a.at || 0) * 1000).toLocaleTimeString()}</div>`;
  $("eng-feed").prepend(li);
  while ($("eng-feed").children.length > 20) $("eng-feed").lastChild.remove();
}

// Auto-call once per completed lap while the checkbox is on.
let lastAutoLap = null;
function maybeAutoCall(live) {
  if (race.on) return;                        // race mode makes its own per-lap call
  if (!$("eng-auto").checked || !state.engineerReady) return;
  const n = live && live.lap && live.lap.number;
  if (!n || n === lastAutoLap) return;
  if (lastAutoLap !== null) callEngineer(null);
  lastAutoLap = n;
}

// ---------------------------------------------------------------- debrief ----
// Built from your recorded laps rather than the live car state: pace and
// consistency per compound, the thermal picture, and where the time went
// against the quickest car on track.
let debriefBusy = false;

async function runDebrief(question, opts) {
  if (debriefBusy) return;
  debriefBusy = true;
  const inRace = (opts && opts.fromRace) || false;
  const track = (opts && opts.track) || null;
  const sessionType = (opts && opts.sessionType) || null;

  if (inRace) {
    setOrb("thinking", "Debriefing");
    $("race-radio").textContent = "Analysing the session…";
  } else {
    // Refresh visibly: clear the previous debrief and show the loader so it's
    // obvious a new analysis is running (it takes up to a minute).
    $("debrief-view").style.display = "none";
    $("empty-state").style.display = "none";
    $("results").style.display = "none";
    $("db-chat") && ($("db-chat").innerHTML = "");
    $("loading-msg").textContent = "Analysing your laps… this takes up to a minute.";
    $("loading").style.display = "flex";
    $("debrief-msg").textContent = "";
    $("debrief-btn").disabled = true;
    $("debrief-btn").textContent = "Analysing…";
  }
  try {
    const r = await api("/api/engineer/debrief", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track, session_type: sessionType,
                             question: question || null }),
    });
    renderDebrief(r);
    if (inRace) {
      const q = (r.briefing || {}).qualifying;
      const line = q && !q.on_pole
        ? `${r.headline} You were ${q.gap_to_pole_s} seconds off ${q.session_topper}.`
        : r.headline;
      $("race-radio").textContent = "“" + line + "”";
      setOrb(q && !q.on_pole ? "note" : "info", "Debrief");
      race.spoken = true;
      speak(line);
    } else {
      $("debrief-msg").textContent = "";
    }
  } catch (e) {
    const msg = e.message;
    if (inRace) { $("race-radio").textContent = msg; setOrb("", "Error"); }
    else $("debrief-msg").innerHTML = `<span class="err">${esc(msg)}</span>`;
  }
  debriefBusy = false;
  $("debrief-btn").disabled = false;
  $("debrief-btn").textContent = "Debrief my session";
}

function renderDebrief(r) {
  $("empty-state").style.display = "none";
  $("results").style.display = "none";
  $("loading").style.display = "none";
  $("debrief-view").style.display = "block";

  $("db-headline").textContent = r.headline || "";
  $("db-pace").textContent = r.pace || "";

  const q = (r.briefing || {}).qualifying;
  const qEl = $("db-quali");
  if (q) {
    qEl.style.display = "block";
    qEl.className = "db-quali " + (q.on_pole ? "pole" : "off");
    qEl.innerHTML = q.on_pole
      ? `<b>Pole.</b> Your ${esc(q.your_best)} beat ${esc(q.session_topper)} ` +
        `(${esc(q.session_topper_best)}) by ${Math.abs(q.gap_to_pole_s)}s.`
      : `<b>P2 or lower — ${q.gap_to_pole_s}s off pole.</b> ` +
        `${esc(q.session_topper)}${q.session_topper_team ? " (" + esc(q.session_topper_team) + ")" : ""} ` +
        `set ${esc(q.session_topper_best)}; your best was ${esc(q.your_best)}.`;
  } else qEl.style.display = "none";

  $("db-setup").innerHTML = (r.setup || []).map((x) =>
    `<li><div class="t">${esc(x.area)}` +
    `<span class="db-conf ${esc(x.confidence)}">${esc(x.confidence)}</span></div>` +
    `<div>${esc(x.change)}</div><div class="why">${esc(x.why)}</div></li>`
  ).join("") || "<li>No setup changes suggested.</li>";

  $("db-driving").innerHTML = (r.driving || []).map((x) =>
    `<li><div class="t">${esc(x.where)}` +
    `${x.time_available_s ? `<span class="gain">+${x.time_available_s}s</span>` : ""}</div>` +
    `<div>${esc(x.issue)}</div><div class="why">Fix: ${esc(x.fix)}</div></li>`
  ).join("") || "<li>No specific corner losses found.</li>";

  $("db-plan").textContent = r.qualifying_plan || "";
  $("db-chat").innerHTML = "";              // fresh debrief -> fresh conversation
  $("db-ask-msg").textContent = "";
  renderTrackMap(r);
  const b = r.briefing || {};
  $("db-meta").textContent =
    `${b.track || ""} · ${b.laps_recorded || 0} laps recorded` +
    `${b.sessions ? " · " + b.sessions.join(", ") : ""}` +
    `${r.model ? " · " + r.model : ""}` +
    `${b.thermals ? "" : " · tyre temperatures unavailable for these laps"}`;
}

// Draw the circuit outline (from FastF1 real position data) and place the
// debrief's corners on it, coloured by how much time each one costs.
async function renderTrackMap(r) {
  const panel = $("db-map-panel");
  const ca = r.corner_analysis;
  const ev = (r.briefing || {}).event_2023;
  panel.style.display = "none";
  if (!ca || !ev || !(ca.all_corners || ca.corners_losing_most)) return;

  let outline;
  try {
    outline = await api(`/api/real/trackmap?event=${encodeURIComponent(ev)}&session=Q`);
  } catch (_) { return; }                       // no map is fine — panel stays hidden
  if (!outline || !outline.points || !outline.points.length) return;

  panel.style.display = "block";
  $("db-map-note").textContent = `${esc(ev)} · real 2023 layout`;
  const canvas = $("db-map");
  const wrap = $("db-map-wrap");
  const dpr = window.devicePixelRatio || 1;
  const W = wrap.clientWidth || 600, H = Math.round(W * 0.62);
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.height = H + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const pad = 46;
  const px = (nx) => pad + nx * (W - 2 * pad);
  const py = (ny) => pad + ny * (H - 2 * pad);

  // the track ribbon
  ctx.beginPath();
  outline.points.forEach((p, i) => {
    const X = px(p[0]), Y = py(p[1]);
    i === 0 ? ctx.moveTo(X, Y) : ctx.lineTo(X, Y);
  });
  ctx.closePath();
  ctx.strokeStyle = "#3a3a37"; ctx.lineWidth = 9; ctx.lineJoin = "round"; ctx.stroke();
  ctx.strokeStyle = "#5f5f5a"; ctx.lineWidth = 2; ctx.stroke();

  // start/finish tick
  const s0 = outline.points[0];
  ctx.fillStyle = "#c3c2b7"; ctx.font = "11px system-ui, sans-serif";
  ctx.beginPath(); ctx.arc(px(s0[0]), py(s0[1]), 4, 0, 7); ctx.fill();

  // find the outline point nearest a given lap fraction
  const at = (frac) => {
    let best = outline.points[0], bd = 9;
    for (const p of outline.points) { const d = Math.abs(p[2] - frac); if (d < bd) { bd = d; best = p; } }
    return best;
  };

  const corners = ca.all_corners && ca.all_corners.length ? ca.all_corners
    : ca.corners_losing_most.map((c) => ({ corner: c.corner, frac: c.frac, time_lost_s: c.time_lost_s }));
  const worst = Math.max(0.05, ...corners.map((c) => c.time_lost_s));

  for (const c of corners) {
    if (c.frac == null) continue;
    const p = at(c.frac);
    const X = px(p[0]), Y = py(p[1]);
    const loss = c.time_lost_s;
    // gain (you faster) blue, small loss neutral, big loss red
    const col = loss <= 0.03 ? "#199e70"
      : loss < worst * 0.5 ? "#fab219" : "#e66767";
    const rad = loss <= 0.03 ? 5 : 6 + Math.min(6, (loss / worst) * 6);
    ctx.beginPath(); ctx.arc(X, Y, rad, 0, 7);
    ctx.fillStyle = col; ctx.fill();
    ctx.strokeStyle = "#1a1a19"; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.font = "bold 10px system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("T" + c.corner, X, Y - rad - 7);
  }

  // legend
  let legend = wrap.querySelector(".db-map-legend");
  if (!legend) { legend = document.createElement("div"); legend.className = "db-map-legend"; wrap.appendChild(legend); }
  legend.innerHTML =
    `<span><i style="background:#e66767"></i>biggest time loss</span>` +
    `<span><i style="background:#fab219"></i>smaller loss</span>` +
    `<span><i style="background:#199e70"></i>you're on or ahead</span>` +
    `<span>● start/finish</span>`;
}

async function askDebrief() {
  const q = $("db-question").value.trim();
  if (!q) return;
  $("db-question").value = "";
  $("db-ask-btn").disabled = true;
  const li = document.createElement("li");
  li.innerHTML = `<div class="q">${esc(q)}</div>` +
    `<div class="a thinking">Engineer is thinking…</div>`;
  $("db-chat").appendChild(li);
  li.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const r = await api("/api/engineer/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    li.querySelector(".a").outerHTML =
      `<div class="a"><span class="who">Engineer:</span> ${esc(r.answer)}</div>`;
  } catch (e) {
    li.querySelector(".a").outerHTML =
      `<div class="a err">${esc(e.message)}</div>`;
  }
  $("db-ask-btn").disabled = false;
  $("db-question").focus();
}

// -------------------------------------------------------------- race mode ----
// A glanceable, voice-first view for use while actually driving: one pulsing
// orb, one spoken line, and a small HUD. No charts, nothing to read closely.
const race = { on: false, lastLap: null, busy: false, lastSpoken: "", spoken: false,
               wasQualifying: null, lastTrack: null, debriefed: false };

// Voices are the browser's own (local, no API key, no network). Populate the
// picker once they're available — Chrome loads them asynchronously.
function loadVoices() {
  if (!("speechSynthesis" in window)) return;
  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return;
  const sel = $("race-voice");
  if (sel.dataset.filled === String(voices.length)) return;
  sel.dataset.filled = String(voices.length);
  const english = voices.filter((v) => /^en/i.test(v.lang));
  const list = english.length ? english : voices;
  // A British male voice is the closest thing to a real race engineer.
  const preferred = list.findIndex((v) => /en[-_]GB/i.test(v.lang) && /george|daniel|oliver/i.test(v.name));
  const fallback = list.findIndex((v) => /en[-_]GB/i.test(v.lang));
  sel.innerHTML = list.map((v, i) =>
    `<option value="${esc(v.name)}">${esc(v.name.replace(/^Microsoft /, ""))}</option>`).join("");
  const saved = localStorage.getItem("engineerVoice");
  if (saved && list.some((v) => v.name === saved)) sel.value = saved;
  else sel.selectedIndex = Math.max(0, preferred >= 0 ? preferred : fallback);
}

// Browsers block speech until the page has had a real user gesture, and a
// background tab can be throttled. If an utterance never starts, say so
// instead of leaving the driver wondering why the radio is silent.
function speak(text, force) {
  if (!text || !("speechSynthesis" in window)) return;
  if (!force && $("race-mute").checked) return;
  if (!force && text === race.lastSpoken) return;   // never say it twice
  race.lastSpoken = text;
  let started = false;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.06;                            // clipped, radio-like
    u.pitch = 0.95;
    const want = $("race-voice").value;
    const voices = window.speechSynthesis.getVoices();
    const v = voices.find((x) => x.name === want)
      || voices.find((x) => /en[-_]GB/i.test(x.lang))
      || voices.find((x) => /^en/i.test(x.lang));
    if (v) u.voice = v;
    u.onstart = () => { started = true; setVoiceWarning(""); };
    u.onerror = (e) => setVoiceWarning("Voice blocked by the browser (" +
      (e.error || "error") + "). Open http://localhost:8000 in Chrome or Edge " +
      "and press Test voice.");
    window.speechSynthesis.speak(u);
    setTimeout(() => {
      if (!started && !window.speechSynthesis.speaking) {
        setVoiceWarning("No audio came out. Open http://localhost:8000 directly " +
          "in Chrome or Edge, keep the tab visible, and press Test voice.");
      }
    }, 1800);
  } catch (_) { setVoiceWarning("This browser can't speak — the text still works."); }
}

function setVoiceWarning(msg) {
  const el = $("race-voice-warn");
  if (!el) return;
  el.textContent = msg || "";
  el.style.display = msg ? "block" : "none";
}

function enterRaceMode() {
  race.on = true;
  race.lastLap = null;
  race.spoken = false;
  $("race-view").classList.add("on");
  // Browsers only allow speech after a user gesture — this click is it.
  loadVoices();
}

function exitRaceMode() {
  race.on = false;
  $("race-view").classList.remove("on");
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}

function setOrb(cls, label) {
  const orb = $("race-orb");
  orb.className = "race-orb" + (cls ? " " + cls : "");
  $("race-orb-label").textContent = label;
}

function renderRaceHud(live) {
  const s = live.session || {}, l = live.lap || {}, c = live.car || {};
  // Practice and qualifying are timed; their total_laps is a rolling estimate,
  // so show the clock rather than a bogus "lap 1 of 1".
  const timed = !/^(race|sprint)/i.test(s.type || "");
  let tail = "";
  if (s.track && l.number) {
    if (timed && s.time_left_s != null) {
      const m = Math.floor(s.time_left_s / 60), sec = s.time_left_s % 60;
      tail = ` · lap ${l.number} · ${m}:${String(sec).padStart(2, "0")} left`;
    } else if (!timed && s.total_laps) {
      tail = ` · lap ${l.number} of ${s.total_laps}`;
    } else {
      tail = ` · lap ${l.number}`;
    }
  }
  $("race-session").textContent = s.track ? `${s.track} · ${s.type}${tail}`
                                          : "Waiting for telemetry…";

  const wear = c.tyre_wear, fuelLaps = c.fuel_laps;
  const cell = (v, k, cls) =>
    `<div class="cell ${cls || ""}"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("race-hud").innerHTML = [
    cell(l.position ?? "—", "position"),
    cell(l.last_time_ms ? fmtLap(l.last_time_ms) : "—", "last lap"),
    cell(c.tyre ?? "—", "tyre"),
    cell(wear != null ? Math.round(wear) + "%" : "—", "wear",
         wear >= 60 ? "bad" : wear >= 40 ? "warn" : ""),
    // This is the MFD delta: laps of fuel IN HAND versus what's needed to
    // finish, not laps until empty. Negative means you will run short.
    cell(fuelLaps != null ? (fuelLaps > 0 ? "+" : "") + fuelLaps : "—", "fuel Δ laps",
         fuelLaps != null && fuelLaps < 0 ? "bad"
           : fuelLaps != null && fuelLaps < 0.5 ? "warn" : ""),
    cell(c.ers_pct != null ? c.ers_pct + "%" : "—", "ers"),
  ].join("");
}

async function raceCall(question) {
  if (race.busy || !state.engineerReady) return;
  race.busy = true;
  setOrb("thinking", "Thinking");
  try {
    const a = await api("/api/engineer/advice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question || null, mode: "race" }),
    });
    $("race-radio").textContent = "“" + a.radio + "”";
    const p = a.pit || {};
    $("race-pit").innerHTML = p.recommendation
      ? `<b>${esc(PIT_LABEL[p.recommendation] || p.recommendation)}</b>` +
        `${p.compound && p.compound !== "none" ? " · " + esc(p.compound) : ""}` +
        `${p.target_lap ? " · lap " + p.target_lap : ""}`
      : "";
    setOrb(a.urgency, a.urgency === "urgent" ? "Box / act"
                    : a.urgency === "note" ? "Heads up" : "On track");
    race.spoken = true;
    if (a.speak !== false) speak(a.radio);
    renderAdvice(a);                          // also log it in the debrief feed
  } catch (e) {
    $("race-radio").textContent = e.message;
    setOrb("", "Error");
  }
  race.busy = false;
}

// Called from the status poll: talk once per completed lap.
function raceTick(live) {
  if (!race.on) return;
  // Only paint the HUD from live data. Showing last-known numbers while the
  // signal is down reads as working when it isn't.
  // Qualifying just ended: give the post-mortem against the pole time.
  if (!live.receiving && race.wasQualifying && !race.debriefed) {
    race.debriefed = true;
    runDebrief(null, { fromRace: true, track: race.lastTrack,
                       sessionType: race.wasQualifying });
    return;
  }
  if (!live.receiving) {
    setOrb("", "No signal");
    // Draw the empty HUD rather than nothing, so it reads as "waiting" not "broken".
    renderRaceHud({ session: {}, lap: {}, car: {} });
    $("race-session").textContent = "No telemetry — is the game on track and unpaused?";
    return;
  }
  renderRaceHud(live);
  const st = (live.session || {}).type || "";
  if (/^(Q|Sprint Shootout|Short Q|One-Shot)/i.test(st)) {
    race.wasQualifying = st;                 // remember for the post-session call
    race.lastTrack = (live.session || {}).track || null;
    race.debriefed = false;
  }
  const n = live.lap && live.lap.number;

  // Until the engineer has actually said something, say what we're waiting
  // for. "Waiting for telemetry" while telemetry is streaming is a lie.
  if (!race.spoken) {
    if (!state.engineerReady) {
      setOrb("", "No key");
      $("race-radio").textContent = "Add your Gemini API key to enable the engineer.";
    } else if (!$("race-auto").checked) {
      setOrb("", "Manual");
      $("race-radio").textContent = "Auto is off — press Radio now when you want a call.";
    } else {
      setOrb("", "Standing by");
      $("race-radio").textContent = n
        ? `Standing by — first call when you complete lap ${n}.`
        : "Standing by for your first lap.";
    }
  }

  if (!$("race-auto").checked || !state.engineerReady) return;
  if (!n) return;
  if (race.lastLap === null) { race.lastLap = n; return; }  // don't fire on entry
  if (n !== race.lastLap) {
    race.lastLap = n;
    raceCall(null);
  }
}

// ------------------------------------------------------------------ mode ----
const MODE_TEXT = {
  fastest: "Your quickest clean lap at this track vs the driver's quickest lap of that session.",
  lapforlap: "Your lap number matched against the same lap number of the reference driver — like running side by side.",
  manual: "Pick both laps yourself.",
};

function setMode(mode) {
  state.mode = mode;
  for (const m of ["fastest", "lapforlap", "manual"]) {
    $("mode-" + m).classList.toggle("active", mode === m);
  }
  // In the automatic modes the reference lap is implied, so hide the picker.
  $("ref-lap-block").style.display = mode === "manual" ? "block" : "none";
  $("mode-explain").textContent = MODE_TEXT[mode];
  applyMode();
}

// Auto-select the laps implied by the current mode.
function applyMode() {
  if (state.mode === "fastest") {
    const lap = fastestOwnLap();
    if (lap) $("game-lap-select").value = lap.id;
  }
  onGameLapPicked();
}

// Your quickest valid lap, preferring the track already selected.
function fastestOwnLap() {
  return lapsForSelection()
    .filter((l) => !l.invalid && l.lap_time_ms)
    .reduce((best, l) => (!best || l.lap_time_ms < best.lap_time_ms ? l : best), null);
}

// ------------------------------------------------------------------ init ----
$("listener-btn").addEventListener("click", toggleListener);
if ($("refresh-laps")) $("refresh-laps").addEventListener("click", loadLaps);
$("track-select").addEventListener("change", onTrackChange);
$("session-filter").addEventListener("change", () => {
  rebuildLapOptions(); onGameLapPicked();
});
$("game-lap-select").addEventListener("change", onGameLapPicked);
$("event-select").addEventListener("change", onEventChange);
$("session-select").addEventListener("change", onSessionChange);
$("driver-select").addEventListener("change", () => loadRefLaps("driver-select", "ref-lap-select"));
$("compare-btn").addEventListener("click", runCompare);
$("ref-source-select").addEventListener("change", onRefSourceChange);
$("mode-fastest").addEventListener("click", () => setMode("fastest"));
$("mode-lapforlap").addEventListener("click", () => setMode("lapforlap"));
$("mode-manual").addEventListener("click", () => setMode("manual"));

if ("speechSynthesis" in window) {
  window.speechSynthesis.onvoiceschanged = loadVoices;
  loadVoices();
}
$("race-voice").addEventListener("change", () => {
  localStorage.setItem("engineerVoice", $("race-voice").value);
  speak("Radio check. Engineer here.", true);
});
$("race-test").addEventListener("click", () => {
  loadVoices();
  speak("Radio check, radio check. Box this lap, box, box.", true);
});
$("debrief-btn").addEventListener("click", () => runDebrief(null, {
  track: selectedTrack(),                       // the weekend chosen in Compare
  sessionType: $("session-filter").value || null,  // "" = all sessions
}));
$("db-ask-btn").addEventListener("click", askDebrief);
$("db-question").addEventListener("keydown", (e) => { if (e.key === "Enter") askDebrief(); });
$("race-debrief").addEventListener("click", () =>
  runDebrief(null, { fromRace: true, track: race.lastTrack }));
$("race-enter").addEventListener("click", enterRaceMode);
$("race-exit").addEventListener("click", exitRaceMode);
$("race-radio-btn").addEventListener("click", () => raceCall($("race-question").value.trim()));
$("race-question").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { raceCall($("race-question").value.trim()); $("race-question").value = ""; }
});
$("race-mute").addEventListener("change", () => {
  if ($("race-mute").checked && "speechSynthesis" in window) window.speechSynthesis.cancel();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && race.on) exitRaceMode(); });

$("eng-btn").addEventListener("click", () => callEngineer($("eng-question").value.trim()));
$("eng-key-btn").addEventListener("click", saveEngineerKey);
$("eng-key").addEventListener("keydown", (e) => { if (e.key === "Enter") saveEngineerKey(); });
$("eng-question").addEventListener("keydown", (e) => {
  if (e.key === "Enter") callEngineer($("eng-question").value.trim());
});

loadLaps();
loadEvents();
pollStatus();
engineerStatus();
setInterval(pollStatus, 2000);
