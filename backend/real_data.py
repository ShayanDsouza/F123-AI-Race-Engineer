"""Real F1 2023 data via FastF1 (primary) — schedule, sessions, drivers,
laps, telemetry, stints. Everything is cached on disk by FastF1 itself and
loaded sessions are kept in a small in-memory LRU.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import fastf1

log = logging.getLogger("simvsreality.real_data")

from .paths import DATA_DIR

CACHE_DIR = DATA_DIR / "fastf1_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
fastf1.Cache.enable_cache(str(CACHE_DIR))

YEAR = 2023

_lock = threading.Lock()
_sessions: "OrderedDict[str, fastf1.core.Session]" = OrderedDict()
_MAX_SESSIONS = 4

# session codes offered per event format
SESSIONS_CONVENTIONAL = ["FP1", "FP2", "FP3", "Q", "R"]
SESSIONS_SPRINT_2023 = ["FP1", "Q", "SS", "S", "R"]  # sprint shootout format


def _td_ms(value) -> Optional[int]:
    """timedelta -> int milliseconds (None-safe)."""
    if value is None or pd.isna(value):
        return None
    return int(value.total_seconds() * 1000)


def schedule() -> list:
    sched = fastf1.get_event_schedule(YEAR, include_testing=False)
    events = []
    for _, ev in sched.iterrows():
        fmt = str(ev["EventFormat"])
        events.append({
            "round": int(ev["RoundNumber"]),
            "name": str(ev["EventName"]),
            "location": str(ev["Location"]),
            "country": str(ev["Country"]),
            "date": str(pd.Timestamp(ev["EventDate"]).date()),
            "format": fmt,
            "sessions": SESSIONS_SPRINT_2023 if "sprint" in fmt else SESSIONS_CONVENTIONAL,
        })
    return events


def get_session(event: str, session_code: str,
                light: bool = False) -> fastf1.core.Session:
    """Load (and memoize) a session. light=True loads lap timing only — much
    faster; used for stint/strategy queries. A full session satisfies light
    requests too."""
    full_key = f"{event}|{session_code}|full".lower()
    light_key = f"{event}|{session_code}|light".lower()
    with _lock:
        for key in ((full_key,) if not light else (full_key, light_key)):
            if key in _sessions:
                _sessions.move_to_end(key)
                return _sessions[key]
        session = fastf1.get_session(YEAR, event, session_code)
        session.load(laps=True, telemetry=not light, weather=not light,
                     messages=False)
        key = light_key if light else full_key
        _sessions[key] = session
        while len(_sessions) > _MAX_SESSIONS:
            _sessions.popitem(last=False)
        return session


def drivers(event: str, session_code: str) -> list:
    session = get_session(event, session_code, light=True)
    out = []
    for _, row in session.results.iterrows():
        abbr = str(row.get("Abbreviation", ""))
        if not abbr or abbr == "nan":
            continue
        pos = row.get("Position")
        out.append({
            "abbreviation": abbr,
            "number": str(row.get("DriverNumber", "")),
            "name": str(row.get("FullName", abbr)),
            "team": str(row.get("TeamName", "")),
            "position": None if pd.isna(pos) else int(pos),
        })
    out.sort(key=lambda d: d["position"] if d["position"] is not None else 99)
    return out


def driver_laps(event: str, session_code: str, driver: str) -> list:
    session = get_session(event, session_code, light=True)
    laps = session.laps.pick_drivers(driver)
    if laps.empty:
        return []
    fastest = laps.pick_fastest()
    fastest_n = None if fastest is None or pd.isna(fastest["LapNumber"]) \
        else int(fastest["LapNumber"])
    out = []
    for _, lap in laps.iterrows():
        n = lap["LapNumber"]
        if pd.isna(n):
            continue
        deleted = bool(lap.get("Deleted", False)) if "Deleted" in lap else False
        out.append({
            "lap_number": int(n),
            "lap_time_ms": _td_ms(lap["LapTime"]),
            "sector1_ms": _td_ms(lap["Sector1Time"]),
            "sector2_ms": _td_ms(lap["Sector2Time"]),
            "sector3_ms": _td_ms(lap["Sector3Time"]),
            "compound": str(lap["Compound"]).capitalize(),
            "tyre_life": None if pd.isna(lap["TyreLife"]) else int(lap["TyreLife"]),
            "stint": None if pd.isna(lap["Stint"]) else int(lap["Stint"]),
            "is_fastest": fastest_n == int(n),
            "deleted": deleted,
            "pit_in": not pd.isna(lap["PitInTime"]),
            "pit_out": not pd.isna(lap["PitOutTime"]),
        })
    return out


def session_fastest(event: str, session_code: str) -> Optional[dict]:
    """The single quickest lap of the whole session, whoever set it."""
    session = get_session(event, session_code, light=True)
    laps = session.laps
    if laps is None or laps.empty:
        return None
    lap = laps.pick_fastest()
    if lap is None or pd.isna(lap["LapTime"]):
        return None
    abbr = str(lap["Driver"])
    try:
        info = session.get_driver(abbr)
        name, team = str(info.get("FullName", abbr)), str(info.get("TeamName", ""))
    except Exception:
        name, team = abbr, ""
    return {
        "driver": abbr,
        "driver_name": name,
        "team": team,
        "lap_number": int(lap["LapNumber"]),
        "lap_time_ms": _td_ms(lap["LapTime"]),
        "compound": str(lap["Compound"]).capitalize(),
    }


_outline_cache: dict = {}


def track_outline(event: str, session_code: str = "Q") -> Optional[dict]:
    """Normalized top-down outline of the circuit from a fast real lap's
    position telemetry, so the frontend can draw the track and place the
    debrief's corners on it. Points are (x, y, dist_fraction) with x/y scaled
    into a 0..1 box preserving aspect ratio (y already flipped for screen)."""
    key = f"{event}|{session_code}".lower()
    if key in _outline_cache:
        return _outline_cache[key]
    try:
        session = get_session(event, session_code)
        lap = session.laps.pick_fastest()
        if lap is None:
            return None
        tel = lap.get_telemetry()
        x = tel["X"].to_numpy(dtype=float)
        y = tel["Y"].to_numpy(dtype=float)
        dist = tel["Distance"].to_numpy(dtype=float)
    except Exception as exc:
        log.warning("track outline for %s unavailable: %s", event, exc)
        return None

    dist = dist - dist[0]
    total = float(dist[-1]) or 1.0
    xmin, xmax = float(x.min()), float(x.max())
    ymin, ymax = float(y.min()), float(y.max())
    span = max(xmax - xmin, ymax - ymin) or 1.0
    # centre within the larger span so aspect ratio is preserved
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2

    n = len(x)
    step = max(1, n // 400)          # ~400 points is plenty for a smooth loop
    pts = []
    for i in range(0, n, step):
        nx = (x[i] - cx) / span + 0.5
        ny = 0.5 - (y[i] - cy) / span     # flip Y: screen coords grow downward
        pts.append([round(nx, 4), round(ny, 4), round(dist[i] / total, 4)])
    if pts[-1][2] < 0.999:
        pts.append([pts[0][0], pts[0][1], 1.0])   # close the loop

    result = {"event": event, "points": pts, "length_m": round(total)}
    _outline_cache[key] = result
    return result


def lap_telemetry(event: str, session_code: str, driver: str,
                  lap_number: Optional[int] = None) -> dict:
    """Distance-indexed telemetry for one lap (fastest if lap_number None)."""
    session = get_session(event, session_code)
    laps = session.laps.pick_drivers(driver)
    if laps.empty:
        raise ValueError(f"No laps for {driver} in {event} {session_code}")

    if lap_number is None:
        lap = laps.pick_fastest()
        if lap is None:
            raise ValueError(f"No timed lap for {driver}")
    else:
        match = laps[laps["LapNumber"] == lap_number]
        if match.empty:
            timed = sorted(int(n) for n in laps["LapNumber"].dropna().unique())
            near = [n for n in timed if abs(n - lap_number) <= 3]
            hint = (f" Their nearest laps are {near}." if near else
                    f" They ran laps {timed[0]}–{timed[-1]}." if timed else "")
            raise ValueError(
                f"{driver} has no lap {lap_number} in this session.{hint} "
                f"Try Fastest mode, or a session where they ran that many laps.")
        lap = match.iloc[0]

    tel = lap.get_telemetry()  # includes interpolated Distance channel
    dist = tel["Distance"].to_numpy(dtype=float)
    dist = dist - dist[0]
    time_s = tel["Time"].dt.total_seconds().to_numpy(dtype=float)
    time_s = time_s - time_s[0]
    drs_raw = tel["DRS"].to_numpy()
    drs = np.isin(drs_raw, (10, 12, 14)).astype(int)

    lap_time_ms = _td_ms(lap["LapTime"])
    info = session.get_driver(driver)
    weather = _lap_weather(session, lap)

    return {
        "source": "fastf1",
        "event": event,
        "session": session_code,
        "driver": driver,
        "driver_name": str(info.get("FullName", driver)),
        "team": str(info.get("TeamName", "")),
        "lap_number": int(lap["LapNumber"]),
        "lap_time_ms": lap_time_ms,
        "sector1_ms": _td_ms(lap["Sector1Time"]),
        "sector2_ms": _td_ms(lap["Sector2Time"]),
        "sector3_ms": _td_ms(lap["Sector3Time"]),
        "compound": str(lap["Compound"]).capitalize(),
        "tyre_life": None if pd.isna(lap["TyreLife"]) else int(lap["TyreLife"]),
        "weather": weather,
        "track_length": float(dist[-1]),
        "columns": ["dist", "time_s", "speed", "throttle", "brake", "gear", "drs", "rpm"],
        "samples": [
            [round(float(d), 1), round(float(t), 3), float(s),
             round(float(th) / 100.0, 3), int(b), int(g), int(dr), int(r)]
            for d, t, s, th, b, g, dr, r in zip(
                dist, time_s, tel["Speed"].to_numpy(dtype=float),
                tel["Throttle"].to_numpy(dtype=float),
                tel["Brake"].to_numpy(dtype=int),
                tel["nGear"].to_numpy(dtype=int),
                drs, tel["RPM"].to_numpy(dtype=int))
        ],
    }


WET_COMPOUNDS = {"Intermediate", "Wet"}


def _lap_weather(session, lap) -> dict:
    """Conditions during one lap: the weather sample nearest its start, plus
    the tyre actually fitted (a wet/inter tyre is the strongest wet signal)."""
    compound = str(lap["Compound"]).capitalize()
    out = {"rainfall": None, "air_temp": None, "track_temp": None,
           "compound": compound, "wet": compound in WET_COMPOUNDS,
           "mixed_session": False}
    try:
        # Session-level context: "not raining right now" is not the same as a
        # dry track. If it rained at any point, or anyone ran inters/wets, the
        # track was still evolving and slick laps are off true dry pace.
        session_compounds = {str(c).capitalize() for c in session.laps["Compound"]}
        out["mixed_session"] = bool(session_compounds & WET_COMPOUNDS)

        wdf = session.weather_data
        start = lap["LapStartTime"]
        if wdf is None or wdf.empty or pd.isna(start):
            return out
        out["mixed_session"] = out["mixed_session"] or bool(wdf["Rainfall"].any())
        idx = (wdf["Time"] - start).abs().idxmin()
        row = wdf.loc[idx]
        rain = bool(row["Rainfall"])
        out.update({
            "rainfall": rain,
            "air_temp": round(float(row["AirTemp"]), 1),
            "track_temp": round(float(row["TrackTemp"]), 1),
            "wet": rain or compound in WET_COMPOUNDS,
        })
    except Exception:
        pass  # weather is advisory; never fail a comparison over it
    return out


def stints(event: str, session_code: str, driver: str) -> list:
    """Stint summary for strategy notes (compound, laps, degradation)."""
    session = get_session(event, session_code, light=True)
    laps = session.laps.pick_drivers(driver)
    if laps.empty:
        return []
    out = []
    for stint_n, grp in laps.groupby("Stint"):
        if pd.isna(stint_n):
            continue
        timed = grp[~grp["LapTime"].isna() & grp["PitInTime"].isna()
                    & grp["PitOutTime"].isna()]
        deg_ms_per_lap = None
        if len(timed) >= 4:
            x = timed["TyreLife"].to_numpy(dtype=float)
            y = timed["LapTime"].dt.total_seconds().to_numpy(dtype=float)
            # robust-ish: drop laps >4s off the stint median (SC, traffic)
            med = np.median(y)
            mask = np.abs(y - med) < 4.0
            if mask.sum() >= 4:
                slope = np.polyfit(x[mask], y[mask], 1)[0]
                deg_ms_per_lap = int(slope * 1000)
        out.append({
            "stint": int(stint_n),
            "compound": str(grp["Compound"].iloc[0]).capitalize(),
            "from_lap": int(grp["LapNumber"].min()),
            "to_lap": int(grp["LapNumber"].max()),
            "laps": int(len(grp)),
            "best_ms": _td_ms(grp["LapTime"].min()),
            "deg_ms_per_lap": deg_ms_per_lap,
        })
    return out


def race_strategy_summary(event: str, top_n: int = 5) -> list:
    """Stints of the top finishers of the 2023 race — the strategy baseline."""
    session = get_session(event, "R", light=True)
    out = []
    for _, row in session.results.head(top_n).iterrows():
        abbr = str(row.get("Abbreviation", ""))
        if not abbr:
            continue
        pos = row.get("Position")
        out.append({
            "driver": abbr,
            "name": str(row.get("FullName", abbr)),
            "position": None if pd.isna(pos) else int(pos),
            "stints": stints(event, "R", abbr),
        })
    return out
