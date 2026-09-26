# Sim vs Reality — F1 23 telemetry coach

Compare your **F1 23 (PS5)** laps against **real 2023 F1 telemetry** *and* against
the **AI drivers inside your own game session**, corner by corner — where you
brake too early, where the lap time is, plus an AI race engineer for live
strategy calls.

## What it does

- **Records every car, not just yours.** F1 23 broadcasts telemetry for all 22
  cars, so the app captures the AI opponents' laps too, labelled with their real
  names (VERSTAPPEN, LECLERC, RUSSELL…). Your trace is kept at full resolution;
  opponents are sampled every 3 m to keep a full grid affordable on disk.
- **Two reference sources.** Overlay your lap on a **real 2023 driver** (via
  [FastF1](https://docs.fastf1.dev), with [OpenF1](https://openf1.org) as an
  alternate) or on an **in-game AI driver** from the same session.
- **Three pairing modes.** *Fastest* (your best vs their best), *Lap-for-lap*
  (your lap 19 vs their lap 19), or *Manual*.
- **Analysis.** Distance-aligned speed / throttle / brake / gear overlays, a
  time-delta curve, sector or distance splits, a per-corner table (braking
  point, apex speed, apex gear, full-throttle point), ranked coaching notes and
  strategy notes drawn from the real race.
- **AI race engineer (Gemini).** A *debrief* mode with the full dashboard, and
  a *race* mode that speaks pit calls and strategy changes to you while you
  drive — see setup below.

## Quick start

```
run.bat            # creates .venv on first run, then serves http://localhost:8000
```

The UDP listener starts with the app (port 20777, or `F1_UDP_PORT`), so laps
are captured from the first session you drive. Set `F1_UDP_AUTOSTART=0` to
start it by hand from the sidebar instead.

No PS5 handy? The simulator sends a synthetic two-car session (you + an AI
"VERSTAPPEN" on its own lap clock) over real UDP:

```
.venv\Scripts\python.exe tools\simulate_lap.py
```

Tests (no console, no network, no API key needed):

```
pip install -r requirements-dev.txt
python -m pytest
```

## PS5 setup (F1 23)

1. PS5 and this PC on the **same network**.
2. Find this PC's IPv4 address: `ipconfig`.
3. F1 23 → **Settings → Telemetry Settings**:
   - UDP Telemetry: **On** · Broadcast Mode: **Off**
   - IP Address: **your PC's IP** · Port: **20777**
   - Send Rate: **60 Hz** (120 Hz gains nothing — the real reference telemetry
     is only ~4 Hz, so 60 Hz is already far finer)
   - UDP Format: **2023**
4. Check the sidebar shows *Listening on UDP 20777*, then get on track.

F1 23 sends **nothing** from the menus — telemetry only flows once you're in a
session. The **Hz readout** in the sidebar is the diagnostic: ~60 Hz means data
is arriving cleanly. Frozen readings mean the game is paused.

Works with F1 24/25 too if you set **UDP Format: 2023**.

## AI race engineer (Google Gemini)

Get a free key at **https://aistudio.google.com/apikey** and paste it into
the *Race engineer* panel in the sidebar. It is stored in `data/secrets.json`
(git-ignored, never sent anywhere but Google) and can be forgotten from the
same panel. An environment variable works too and takes precedence:

```powershell
setx GEMINI_API_KEY "AIza..."
```

(`GOOGLE_API_KEY` works too.) The app picks a Gemini model automatically from
the ones your key can see, preferring a fast one for live race calls. The
engineer only ever sees *your* laps from the session being driven — never the
AI cars' laps or laps from another circuit.

### Two modes

**Debrief mode** is the dashboard: overlays, delta curve, corner table,
coaching and strategy notes. Use it between sessions.

**Race mode** is for while you're actually driving — a full-screen pulsing orb
and a small HUD, with the engineer **speaking to you** over your PC's voice.
Press *Enter race mode* in the sidebar.

- The orb pulses blue normally, amber for a heads-up, and fast red when
  something needs action this lap.
- It calls you **once per completed lap** by default (*Auto*), and stays quiet
  when nothing has changed — it's shown its own recent calls so it follows
  through on a plan instead of repeating itself.
- Type a question and hit Enter to ask mid-race ("how are the tyres?"), or
  press *Radio now* for an immediate call.
- *Mute* silences the voice but keeps the text. **Esc** exits.

Voice uses your browser's built-in speech synthesis — no extra key, no latency
from another API call.

### Session debrief

**Debrief my session** analyses your *recorded laps* rather than the live car
state, and answers "what setup should I run, and where do I improve?".

It feeds the engineer your pace and consistency per compound, tyre degradation
across the stint, the per-lap tyre and brake temperatures, and — when an AI or
reference lap exists at that circuit — a corner-by-corner account of where the
time went. Back comes a ranked list of setup changes (each with the evidence
and a confidence level), the corners with time available, and a qualifying
plan.

Everything is scoped to **one circuit**: mixing tracks would make every pace
figure meaningless.

**After qualifying**, race mode fires this automatically when the session ends
and tells you the gap to the session-topping car, who set it, and which corners
account for the deficit. You can also press **Debrief** at any time.

Setup advice needs tyre temperatures, which are only recorded for laps driven
after this feature existed — older laps still give pace, consistency and corner
analysis.

## Notes & limits

- **Same circuit only.** Comparisons are refused across different tracks — the
  traces are aligned on normalized lap distance, so a mismatch would produce
  confident nonsense. Short layouts and non-2023 circuits are rejected too.
- **Condition warnings.** Wet-vs-dry, drying-track and big track-temperature
  differences are flagged, because a wet reference lap makes every delta
  meaningless. (Canada 2023 qualifying is the classic trap: it was wet.)
- **Sector splits** are exact when both laps report official sector times;
  otherwise they're labelled "Split — approx, by distance".
- Recorded laps are plain JSON in `data/laps/`. Real data is cached in
  `data/fastf1_cache/`.
- Real telemetry has no steering channel and quantized throttle/brake; treat
  braking-point differences under ~5 m as noise.
- **Flashbacks** are handled: a rewind inside a lap drops the stretch you are
  about to re-drive (the lap is saved with a `flashbacks` count); a rewind
  back across the start line discards the partial lap.
- **Lap timing.** A lap is closed when the distance counter resets, and its
  number and sector times are read so that the game's lap counter ticking
  late (pit exit) or early does not mislabel it. If the game's reported lap
  time disagrees with the trace by more than 1.5 s the trace clock is used and
  the lap is flagged `lap_time_estimated`.

## Project layout

```
backend/
  app.py            FastAPI app + REST API
  analysis.py       distance alignment, delta, corner detection
  coach.py          coaching, strategy and condition warnings
  engineer.py       AI race engineer (Google Gemini)
  debrief.py        lap-history analysis for setup + improvement advice
  real_data.py      FastF1 wrapper (2023 season, disk-cached)
  openf1_data.py    OpenF1 alternate source
  store.py          lap JSON storage
  f123/
    packets.py      F1 23 UDP packet-format-2023 parser
    listener.py     threaded UDP listener
    recorder.py     multi-car lap assembly
frontend/           dashboard (vanilla JS + canvas charts)
tools/
  simulate_lap.py   synthetic two-car UDP session for testing
tests/              pytest suite: packet parsing, lap assembly, analysis, store, API
data/               recorded laps, FastF1 cache, Gemini key (created at runtime)
```

### API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | listener state + live car/session snapshot (polled by the UI) |
| `GET /api/laps?scope=player\|opponent\|all` | recorded laps (headers only, indexed) |
| `GET/DELETE /api/laps/{id}` | one lap with its full trace |
| `GET /api/compare` | your lap vs a real 2023 lap (FastF1 or OpenF1) |
| `GET /api/compare_ingame` | your lap vs an AI car from your own session |
| `GET /api/real/…` | 2023 calendar, drivers, laps, session fastest, track outline |
| `GET /api/engineer/briefing` | the analysed lap history the debrief is built on |
| `POST /api/engineer/advice\|debrief\|ask` | Gemini race calls, debrief, follow-ups |
| `POST/DELETE /api/engineer/key` | store or forget the Gemini key locally |
| `GET /api/debug/cars` | per-car recorder state |

## Roadmap

Ordered by how much they would change what you see on screen.

1. **Live push instead of polling.** The UI asks `/api/status` every 2 s. A
   WebSocket fed straight from the recorder would make race mode react within a
   frame and drop the per-poll lap re-listing entirely.
2. **A game-derived track map.** The motion packet carries world X/Y/Z for
   every car; recording it alongside the trace would give a circuit outline
   for *every* track, including short layouts and pre-2023 circuits, instead
   of borrowing the real 2023 outline.
3. **Late sector-time fill.** The session-history packet for a lap arrives up
   to a second after the line. Sector times are already recovered from the
   closing LapData packet; patching the saved file when the official history
   arrives would make the two agree to the millisecond.
4. **Corner names.** Detected corners are numbered by position; a per-circuit
   table (T1 = "Turn 1", Spa's "Eau Rouge") would make the coaching notes and
   radio calls read like a real engineer's.
5. **Per-lap ERS and fuel deltas** for the AI cars, so the in-game comparison
   can say how much of the gap is engine mode rather than driving.
6. **Persist the engineer's radio history** per session on disk, so a restart
   mid-race does not make it repeat calls it already gave.

## Troubleshooting

- **No packets** — check UDP Format is 2023 and you're on track, not in menus.
  `GET /api/status` shows `wrong_format_seen` if the format is off.
- **A lap didn't save** — `GET /api/status` → `last_finalize` says exactly why
  (too few samples, incomplete coverage, invalidated). Flashbacks and restarts
  are discarded on purpose.
- **Per-car state** — `GET /api/debug/cars` dumps every car the recorder sees.
- **Stale UI after an update** — assets are versioned with `?v=N` in
  `index.html`; bump it when editing `frontend/*.js|css`.
