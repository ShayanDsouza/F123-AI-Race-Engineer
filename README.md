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

## Get it running

Three ways, easiest first. All of them end with the dashboard open at
**http://localhost:8000** and a *Setup* panel that shows exactly what to type
into the game.

**1. Download a build (no Python needed).** Grab the zip for Windows, macOS
or Linux from the [Releases](../../releases) page, unzip it anywhere, and run
`sim-vs-reality` (double-click on Windows). Windows SmartScreen and macOS
Gatekeeper will warn about an unsigned app the first time: choose *Run anyway*
/ *Open*.

**2. One command, if you have Python 3.10+.**

```
pipx install git+https://github.com/ShayanDsouza/F123-AI-Race-Engineer
sim-vs-reality
```

(`pip install …` works too; `pipx` just keeps it out of your other Python
packages.) `sim-vs-reality --help` lists the options: web port, UDP port,
data folder, `--no-browser`.

**3. From a checkout.** `run.bat` on Windows, `./run.sh` on macOS/Linux. The
first run creates a private `.venv` and installs the dependencies; later runs
start straight away.

Whichever way, the first time the app listens on the network your OS asks
whether to allow it. **Windows:** tick *Private networks* and *Allow access*.
If you clicked *Cancel*, the console's data is silently blocked; run it once
as administrator, or add an inbound rule for UDP 20777 in Windows Defender
Firewall. **macOS:** click *Allow*.

No console handy? The simulator sends a synthetic two-car session (you + an
AI "VERSTAPPEN" on its own lap clock) over real UDP:

```
sim-vs-reality-simulate        # or: python tools/simulate_lap.py
```

## Connect your console (PS5, Xbox, or F1 on PC)

The *Setup* panel in the sidebar shows these steps with **your computer's IP
address filled in**, and turns green the moment telemetry arrives. It opens
by itself on first run; the *Setup* button brings it back.

1. Console and computer on the **same network** (same Wi-Fi, or both on the
   router by cable).
2. F1 23 (F1 24/25 work too) → **Settings → Telemetry Settings**:
   - UDP Telemetry: **On**
   - UDP Broadcast Mode: **On** — the easiest option, no IP address to type.
     If broadcast doesn't reach your computer (some routers isolate Wi-Fi
     clients), set it **Off** and enter the IP address the Setup panel shows.
   - UDP Port: **20777**
   - UDP Send Rate: **60 Hz** (120 Hz gains nothing; the real reference
     telemetry is only ~4 Hz)
   - UDP Format: **2023**
3. Get on track. F1 23 sends **nothing** from the menus. The sidebar's **Hz
   readout** is the diagnostic: ~60 Hz means data is arriving cleanly, frozen
   readings mean the game is paused.

Playing F1 23 on the *same* PC as the app? Use IP address **127.0.0.1**.

**Race-mode HUD on a phone:** the Setup panel lists a `http://<your-ip>:8000`
address. Open it on a phone or tablet on the same network, press *Enter race
mode*, and the engineer speaks from there while the PC records.

The UDP port you set in the sidebar is remembered. Command-line and
environment overrides: `--udp-port` / `F1_UDP_PORT`, `--port` /
`SIMVSREALITY_WEB_PORT`, `--data` / `SIMVSREALITY_DATA`, `F1_UDP_AUTOSTART=0`
to start the listener by hand.

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
- Recorded laps are plain JSON in the data folder (`data/laps/` in a
  checkout; for installed copies `%LOCALAPPDATA%\SimVsReality` on Windows,
  `~/Library/Application Support/SimVsReality` on macOS,
  `~/.local/share/sim-vs-reality` on Linux; the Setup panel shows the path).
  Real data is cached beside them in `fastf1_cache/`.
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

## Developing

```
pip install -r requirements-dev.txt
python -m pytest                          # 59 tests, no console/network/key needed
pyinstaller packaging/sim-vs-reality.spec # standalone bundle in dist/sim-vs-reality/
```

Pushing a tag such as `v1.2.0` makes GitHub Actions build the Windows, macOS
and Linux bundles and attach them to a release; every push runs the tests on
all three platforms.

## Project layout

```
backend/
  app.py            FastAPI app + REST API
  cli.py            `sim-vs-reality` command: starts the server, opens the browser
  paths.py          where data lives (checkout vs installed vs bundled)
  settings.py       persisted settings (UDP port)
  netinfo.py        this machine's LAN address for the game's telemetry screen
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
packaging/          PyInstaller spec for the standalone bundle
.github/workflows/  tests on every push; release bundles on tags
data/               recorded laps, FastF1 cache, settings, Gemini key (checkout only)
```

### API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | listener state + live car/session snapshot (polled by the UI) |
| `GET /api/setup` | this machine's IP addresses, port, and whether telemetry is arriving |
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

- **No packets** — the Setup panel says what it sees: nothing at all (game
  settings or firewall), packets in the wrong format (set UDP Format 2023),
  or packets that stopped (game paused / in menus). `GET /api/setup` has the
  same information.
- **A lap didn't save** — `GET /api/status` → `last_finalize` says exactly why
  (too few samples, incomplete coverage, invalidated). Flashbacks and restarts
  are discarded on purpose.
- **Per-car state** — `GET /api/debug/cars` dumps every car the recorder sees.
- **Stale UI after an update** — assets are versioned with `?v=N` in
  `index.html`; bump it when editing `frontend/*.js|css`.
