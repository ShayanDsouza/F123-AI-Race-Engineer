"""Sim vs Reality — FastAPI backend.

Serves the dashboard, controls the F1 23 UDP listener, exposes recorded game
laps, real 2023 data (FastF1 / OpenF1) and the comparison + coaching API.

Run:  uvicorn backend.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, coach, netinfo, settings, store
from .f123 import packets as pk
from .paths import DATA_DIR, frontend_dir
from .f123.listener import UdpListener
from .f123.recorder import LapRecorder

log = logging.getLogger("simvsreality")
logging.basicConfig(level=logging.INFO)

FRONTEND_DIR = frontend_dir()

recorder = LapRecorder(on_lap_complete=lambda rec: _on_lap(rec))
listener = UdpListener(on_datagram=recorder.feed)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Always be listening. Requiring a manual click after every restart just
    loses laps the user already drove."""
    port = int(os.environ.get("F1_UDP_PORT") or settings.load()["udp_port"])
    log.info("data directory: %s", DATA_DIR)
    if os.environ.get("F1_UDP_AUTOSTART", "1") != "0":
        if listener.start(port):
            log.info("UDP listener auto-started on %s", port)
        else:
            log.warning("could not auto-start listener: %s", listener.error)
    try:
        yield
    finally:
        listener.stop()


app = FastAPI(title="Sim vs Reality", version="1.1", lifespan=_lifespan)


def _on_lap(record: dict):
    lap_id = store.save_lap(record)
    log.info("Recorded lap %s — %s %s %sms", lap_id, record.get("track_name"),
             record.get("session_type"), record.get("lap_time_ms"))


# ------------------------------------------------------------------ status ----
@app.get("/api/status")
def status():
    return {
        "listener": {
            "running": listener.running,
            "port": listener.port,
            "error": listener.error,
            "last_sender": listener.last_sender,
            "datagrams": listener.datagrams,
        },
        "live": recorder.live_status(),
    }


@app.get("/api/setup")
def setup_info():
    """Everything the setup panel needs to tell a new user what to type into
    the game: this machine's LAN address(es), the port, and whether anything
    is arriving yet."""
    ips = netinfo.lan_ips()
    web_port = int(os.environ.get("SIMVSREALITY_WEB_PORT", "8000"))
    return {
        "hostname": netinfo.hostname(),
        "ips": ips,
        "primary_ip": ips[0] if ips else None,
        "udp_port": (listener.port if listener.running
                     else settings.load()["udp_port"]),
        "packet_format": pk.PACKET_FORMAT,
        "web_urls": [f"http://{ip}:{web_port}" for ip in ips],
        "data_dir": str(DATA_DIR),
        "listener": {
            "running": listener.running,
            "error": listener.error,
            "datagrams": listener.datagrams,
            "last_sender": listener.last_sender,
        },
        "receiving": recorder.live_status()["receiving"],
        "wrong_format_seen": recorder.wrong_format_seen,
        "packets_seen": recorder.packets_seen,
    }


class ListenerConfig(BaseModel):
    port: int = 20777


@app.post("/api/listener/start")
def listener_start(cfg: ListenerConfig):
    if not (1 <= cfg.port <= 65535):
        raise HTTPException(status_code=400, detail="Port must be 1–65535.")
    if not listener.start(cfg.port):
        raise HTTPException(status_code=400, detail=listener.error)
    settings.save(udp_port=cfg.port)          # survives a restart
    return {"running": True, "port": cfg.port}


@app.post("/api/listener/stop")
def listener_stop():
    listener.stop()
    return {"running": False}


# ------------------------------------------------------------------- laps ----
@app.get("/api/laps")
def laps(scope: str = Query("player", pattern="^(player|opponent|all)$"),
         session_uid: Optional[str] = None):
    """Recorded laps. `scope` splits your own laps from the AI opponents the
    game broadcast alongside you."""
    out = store.list_laps()
    if scope == "player":
        out = [l for l in out if l.get("is_player")]
    elif scope == "opponent":
        out = [l for l in out if not l.get("is_player")]
    if session_uid:
        out = [l for l in out if l.get("session_uid") == session_uid]
    for lap in out:
        lap["suggested_event"] = pk.TRACK_TO_2023_EVENT.get(lap.get("track_id"))
    return out


@app.get("/api/laps/{lap_id}")
def lap_detail(lap_id: str):
    rec = store.load_lap(lap_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="lap not found")
    return rec


@app.delete("/api/laps/{lap_id}")
def lap_delete(lap_id: str):
    if not store.delete_lap(lap_id):
        raise HTTPException(status_code=404, detail="lap not found")
    return {"deleted": lap_id}


# -------------------------------------------------------------- real data ----
@app.get("/api/real/events")
def real_events():
    from . import real_data
    return real_data.schedule()


@app.get("/api/real/drivers")
def real_drivers(event: str, session: str = "Q"):
    from . import real_data
    try:
        return real_data.drivers(event, session)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FastF1: {exc}")


@app.get("/api/real/fastest")
def real_fastest(event: str, session: str):
    """The overall fastest lap of the session, not per-driver."""
    from . import real_data
    try:
        result = real_data.session_fastest(event, session)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FastF1: {exc}")
    if result is None:
        raise HTTPException(status_code=404, detail="no timed laps in this session")
    return result


@app.get("/api/real/trackmap")
def real_trackmap(event: str, session: str = "Q"):
    """Top-down outline of the circuit for the debrief track map."""
    from . import real_data
    try:
        outline = real_data.track_outline(event, session)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FastF1: {exc}")
    if outline is None:
        raise HTTPException(status_code=404, detail="no track outline available")
    return outline


@app.get("/api/real/laps")
def real_laps(event: str, session: str, driver: str):
    from . import real_data
    try:
        return real_data.driver_laps(event, session, driver)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FastF1: {exc}")


# -------------------------------------------------------------- comparison ----
def _real_trace(event: str, session: str, driver: str,
                lap: Optional[int], source: str) -> dict:
    if source == "openf1":
        from . import openf1_data, real_data
        sess = openf1_data.find_session(2023, event.split(" Grand Prix")[0],
                                        _openf1_session_name(session))
        if sess is None:
            raise HTTPException(status_code=404, detail="OpenF1 session not found")
        num = next((d["number"] for d in openf1_data.drivers(sess["session_key"])
                    if d["abbreviation"] == driver), None)
        if num is None:
            raise HTTPException(status_code=404, detail=f"OpenF1: driver {driver} not found")
        if lap is None:
            lap_list = openf1_data.laps(sess["session_key"], int(num))
            timed = [l for l in lap_list if l["lap_time_ms"]]
            if not timed:
                raise HTTPException(status_code=404, detail="no timed laps")
            lap = min(timed, key=lambda l: l["lap_time_ms"])["lap_number"]
        trace = openf1_data.lap_telemetry(sess["session_key"], int(num), int(lap))
        if trace is None:
            raise HTTPException(status_code=404, detail="OpenF1: no car data for that lap")
        trace["driver"] = driver
        trace["event"] = event
        trace["session"] = session
        return trace
    from . import real_data
    try:
        return real_data.lap_telemetry(event, session, driver, lap)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FastF1: {exc}")


def _require_same_track(game_lap: dict, event: str):
    """Refuse to overlay a lap on a different circuit — the traces are aligned
    on normalized lap distance, so a mismatch produces confident nonsense."""
    track_id = game_lap.get("track_id", -1)
    track_name = game_lap.get("track_name", "your track")

    if track_id in pk.SHORT_LAYOUT_IDS:
        raise HTTPException(status_code=400, detail=(
            f"'{track_name}' is a short/alternate layout, which shares its name "
            f"with a real circuit but not its geometry. There is no real 2023 "
            f"telemetry for it — record a lap on the full circuit to compare."))

    expected = pk.TRACK_TO_2023_EVENT.get(track_id)
    if expected is None:
        raise HTTPException(status_code=400, detail=(
            f"'{track_name}' was not on the 2023 F1 calendar, so there is no real "
            f"2023 telemetry to compare against. Tracks that do work include "
            f"Bahrain, Melbourne, Monaco, Montreal, Silverstone, Spa, Monza, "
            f"Suzuka and Abu Dhabi."))

    if expected != event:
        raise HTTPException(status_code=400, detail=(
            f"Track mismatch: your lap was set at {track_name}, which is the "
            f"{expected}. You asked to compare it against the {event}. Laps can "
            f"only be compared on the same circuit."))


def _openf1_session_name(code: str) -> str:
    return {"FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3",
            "Q": "Qualifying", "SS": "Sprint Shootout", "S": "Sprint",
            "R": "Race"}.get(code, code)


@app.get("/api/compare")
def compare(lap_id: str, event: str, session: str, driver: str,
            ref_lap: Optional[int] = None,
            source: str = Query("fastf1", pattern="^(fastf1|openf1)$"),
            race_strategy: bool = True):
    """Game lap vs real driver lap."""
    game_lap = store.load_lap(lap_id)
    if game_lap is None:
        raise HTTPException(status_code=404, detail="game lap not found")
    _require_same_track(game_lap, event)
    ref = _real_trace(event, session, driver, ref_lap, source)

    try:
        result = analysis.compare(game_lap, ref)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    ref_label = ref.get("driver", driver)
    result["warnings"] = coach.condition_warnings(game_lap, ref)
    result["insights"] = coach.driving_insights(result, ref_label)
    result["strategy"] = _strategy(event, session, driver, ref, game_lap,
                                   race_strategy)
    result["map_event"] = event          # 2023 event for the track outline
    result["meta"] = {
        "you": {k: game_lap.get(k) for k in (
            "id", "track_name", "session_type", "lap_number", "lap_time_ms",
            "tyre", "tyre_age", "weather", "track_temp", "invalid")},
        "ref": {k: ref.get(k) for k in (
            "driver", "driver_name", "team", "event", "session", "lap_number",
            "lap_time_ms", "compound", "tyre_life", "weather", "source")},
    }
    return result


@app.get("/api/compare_ingame")
def compare_ingame(lap_id: str, ref_lap_id: str):
    """Your lap vs another car's lap from the same F1 23 session — the AI
    Verstappen in the game, not the real one."""
    mine = store.load_lap(lap_id)
    ref = store.load_lap(ref_lap_id)
    if mine is None or ref is None:
        raise HTTPException(status_code=404, detail="lap not found")
    if mine.get("track_id") != ref.get("track_id"):
        raise HTTPException(status_code=400, detail=(
            f"Track mismatch: your lap is at {mine.get('track_name')} and the "
            f"reference is at {ref.get('track_name')}. Laps can only be "
            f"compared on the same circuit."))

    try:
        result = analysis.compare(mine, ref)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    ref_label = ref.get("driver") or "the AI"
    result["warnings"] = _ingame_warnings(mine, ref)
    result["insights"] = coach.driving_insights(result, ref_label)
    result["strategy"] = coach.ingame_strategy_notes(mine, ref)
    result["meta"] = {
        "you": {k: mine.get(k) for k in (
            "id", "track_name", "session_type", "lap_number", "lap_time_ms",
            "tyre", "tyre_age", "weather", "track_temp", "invalid")},
        "ref": {
            "driver": ref_label,
            "driver_name": ref_label,
            "team": ref.get("team"),
            "event": f"{ref.get('track_name')} (in-game)",
            "session": ref.get("session_type"),
            "lap_number": ref.get("lap_number"),
            "lap_time_ms": ref.get("lap_time_ms"),
            "compound": ref.get("tyre"),
            "tyre_life": ref.get("tyre_age"),
            "source": "f1-23",
        },
    }
    # the real 2023 outline for this circuit (in-game laps map to the real one)
    result["map_event"] = pk.TRACK_TO_2023_EVENT.get(mine.get("track_id")) \
        if mine.get("track_id") not in pk.SHORT_LAYOUT_IDS else None
    return result


def _ingame_warnings(mine: dict, ref: dict) -> list:
    out = []
    if mine.get("session_uid") != ref.get("session_uid"):
        out.append({"kind": "session", "severity": 2, "text":
                    "These laps come from different game sessions, so track "
                    "temperature, fuel load and car setup may not match."})
    if mine.get("session_type") != ref.get("session_type"):
        out.append({"kind": "session", "severity": 2, "text":
                    f"Different session types: yours is {mine.get('session_type')} "
                    f"and the reference is {ref.get('session_type')}. Fuel loads "
                    f"and engine modes differ between them."})
    if mine.get("weather") != ref.get("weather"):
        out.append({"kind": "weather", "severity": 3, "text":
                    f"Conditions differ: you had {mine.get('weather')} and the "
                    f"reference lap had {ref.get('weather')}."})
    if ref.get("invalid"):
        out.append({"kind": "session", "severity": 1, "text":
                    "The reference lap was marked invalid in-game (track limits), "
                    "so part of it may be off the racing line."})
    return out


def _strategy(event: str, session: str, driver: str, ref: dict,
              game_lap: Optional[dict], include_race: bool) -> list:
    from . import real_data
    ref_stints, top5 = [], []
    if include_race:
        try:
            ref_stints = real_data.stints(event, session, driver) \
                if session in ("R", "S") else []
            top5 = real_data.race_strategy_summary(event)
        except Exception as exc:
            log.warning("race strategy unavailable for %s: %s", event, exc)
    return coach.strategy_notes(session, ref, ref_stints, top5, game_lap)


@app.get("/api/debug/cars")
def debug_cars():
    """Per-car recorder state — why an opponent lap is or isn't being captured."""
    r = recorder
    out = []
    for idx in sorted(r.cars):
        car = r.cars[idx]
        lap = car.latest_lap
        part = r.participants.get(idx)
        out.append({
            "car": idx,
            "player": idx == r.player_index,
            "name": part.name if part else None,
            "ai": bool(part.ai_controlled) if part else None,
            "lap_num": car.current_lap_num,
            "samples": len(car.samples),
            "driver_status": lap.driver_status if lap else None,
            "result_status": lap.result_status if lap else None,
            "pit_status": lap.pit_status if lap else None,
            "distance": round(lap.lap_distance) if lap else None,
            "last_lap_ms": lap.last_lap_time_ms if lap else None,
        })
    return {"player_index": r.player_index,
            "participants_known": len(r.participants),
            "record_opponents": r.record_opponents, "cars": out}


# --------------------------------------------------------- race engineer ----
def _own_recent_laps(live: dict) -> list:
    """The player's laps from the session being driven right now. Opponent
    laps, and the driver's laps from other tracks, would give the engineer a
    'recent lap' history that is not theirs."""
    mine = [l for l in store.list_laps() if l.get("is_player")]
    uid = live.get("session_uid")
    same = [l for l in mine if uid and l.get("session_uid") == uid]
    if same:
        return same
    track = (live.get("session") or {}).get("track")
    return [l for l in mine if l.get("track_name") == track]


class EngineerRequest(BaseModel):
    question: Optional[str] = None
    mode: str = "race"


@app.get("/api/engineer/status")
def engineer_status():
    from .engineer import engineer
    return {"available": engineer.available, "provider": "google-gemini",
            "key_env": "GEMINI_API_KEY",
            "key_source": engineer.key_source,
            "history": engineer.history[-10:]}


class KeyRequest(BaseModel):
    key: str


@app.post("/api/engineer/key")
def engineer_set_key(req: KeyRequest):
    """Store a Gemini key beside the recorded laps (git-ignored) so a shipped
    copy works without touching environment variables. The key itself is
    never sent back out."""
    from . import engineer as eng
    key = (req.key or "").strip()
    if not key.startswith("AIza") or len(key) < 30:
        raise HTTPException(status_code=400, detail=(
            "That does not look like a Gemini API key — they start with "
            "'AIza'. Get one at https://aistudio.google.com/apikey"))
    eng.write_local_key(key)
    eng.engineer.forget_client()
    return {"available": eng.engineer.available,
            "key_source": eng.engineer.key_source}


@app.delete("/api/engineer/key")
def engineer_clear_key():
    from . import engineer as eng
    eng.clear_local_key()
    eng.engineer.forget_client()
    return {"available": eng.engineer.available,
            "key_source": eng.engineer.key_source}


@app.post("/api/engineer/reset")
def engineer_reset():
    from .engineer import engineer
    engineer.reset_history()
    return {"ok": True}


class DebriefRequest(BaseModel):
    track: Optional[str] = None
    session_type: Optional[str] = None
    compound: Optional[str] = None
    question: Optional[str] = None


@app.get("/api/engineer/briefing")
def engineer_briefing(track: Optional[str] = None,
                      session_type: Optional[str] = None,
                      compound: Optional[str] = None):
    """The analysed lap history, without calling the model — useful on its own
    and makes the debrief inspectable."""
    from . import debrief as db
    try:
        return db.build(track, session_type, compound)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/engineer/debrief")
def engineer_debrief(req: DebriefRequest):
    """Setup and improvement advice built from your recorded laps."""
    from . import debrief as db, engineer as eng
    try:
        briefing = db.build(req.track, req.session_type, req.compound)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    event = None
    for lap in store.list_laps():
        if lap.get("track_name") == briefing["track"]:
            event = pk.TRACK_TO_2023_EVENT.get(lap.get("track_id"))
            break
    try:
        result = eng.engineer.debrief(briefing, event=event,
                                      question=req.question)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.exception("debrief failed")
        raise HTTPException(status_code=502, detail=f"Race engineer: {exc}")
    result["briefing"] = briefing
    return result


class AskRequest(BaseModel):
    question: str


@app.post("/api/engineer/ask")
def engineer_ask(req: AskRequest):
    """Free-text follow-up about the most recent debrief."""
    from .engineer import engineer
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    try:
        return engineer.ask_debrief(q)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.exception("debrief ask failed")
        raise HTTPException(status_code=502, detail=f"Race engineer: {exc}")


@app.post("/api/engineer/advice")
def engineer_advice(req: EngineerRequest):
    from . import engineer as eng
    live = recorder.live_status()
    if not live.get("receiving") or not live.get("session"):
        raise HTTPException(status_code=409, detail=(
            "No live telemetry. Start the listener and get on track in F1 23 — "
            "the engineer works from your live car state."))

    state = eng.build_state(live, _own_recent_laps(live))
    track_id = (live.get("session") or {}).get("track_id")
    event = (pk.TRACK_TO_2023_EVENT.get(track_id)
             if track_id not in pk.SHORT_LAYOUT_IDS else None)
    mode = "debrief" if req.mode == "debrief" else "race"
    try:
        return eng.engineer.advice(state, event=event, question=req.question,
                                   mode=mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.exception("engineer call failed")
        raise HTTPException(status_code=502, detail=f"Race engineer: {exc}")


# ---------------------------------------------------------------- frontend ----
@app.middleware("http")
async def no_stale_frontend(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
