"""OpenF1 (https://api.openf1.org) — secondary real-data source.

Free, no auth, data from 2023 onwards. Car data is sampled at ~3.7 Hz so
FastF1 is the default for telemetry overlays; OpenF1 is offered as a
fallback/cross-check source and for quick session/lap listings.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import requests

BASE = "https://api.openf1.org/v1"
_session = requests.Session()


def _get(path: str, **params) -> list:
    for attempt in range(3):
        resp = _session.get(f"{BASE}/{path}", params=params, timeout=30)
        if resp.status_code == 429:  # rate limited
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return []


def sessions(year: int = 2023) -> list:
    return _get("sessions", year=year)


def find_session(year: int, country_or_circuit: str, session_name: str) -> Optional[dict]:
    for s in sessions(year):
        if s.get("session_name", "").lower() != session_name.lower():
            continue
        hay = f"{s.get('country_name','')} {s.get('circuit_short_name','')}".lower()
        if country_or_circuit.lower() in hay:
            return s
    return None


def drivers(session_key: int) -> list:
    return [{
        "abbreviation": d.get("name_acronym"),
        "number": str(d.get("driver_number")),
        "name": d.get("full_name"),
        "team": d.get("team_name"),
    } for d in _get("drivers", session_key=session_key)]


def laps(session_key: int, driver_number: int) -> list:
    out = []
    for lap in _get("laps", session_key=session_key, driver_number=driver_number):
        dur = lap.get("lap_duration")
        out.append({
            "lap_number": lap.get("lap_number"),
            "lap_time_ms": int(dur * 1000) if dur else None,
            "sector1_ms": int(lap["duration_sector_1"] * 1000) if lap.get("duration_sector_1") else None,
            "sector2_ms": int(lap["duration_sector_2"] * 1000) if lap.get("duration_sector_2") else None,
            "sector3_ms": int(lap["duration_sector_3"] * 1000) if lap.get("duration_sector_3") else None,
            "pit_out": bool(lap.get("is_pit_out_lap")),
            "date_start": lap.get("date_start"),
        })
    return out


def lap_telemetry(session_key: int, driver_number: int, lap_number: int) -> Optional[dict]:
    """Approximate distance-indexed telemetry for one lap.

    OpenF1 car_data has no distance channel, so distance is integrated from
    speed over time — good enough for coarse overlays.
    """
    all_laps = _get("laps", session_key=session_key, driver_number=driver_number)
    lap = next((l for l in all_laps if l.get("lap_number") == lap_number), None)
    if lap is None or not lap.get("date_start") or not lap.get("lap_duration"):
        return None

    t0 = lap["date_start"]
    duration = float(lap["lap_duration"])
    # ISO end time: use the API's own >=/<= filters
    import datetime as dt
    start = dt.datetime.fromisoformat(t0.replace("Z", "+00:00"))
    end = start + dt.timedelta(seconds=duration)
    rows = _get("car_data", session_key=session_key, driver_number=driver_number,
                **{"date>": start.isoformat(), "date<": end.isoformat()})
    if len(rows) < 10:
        return None
    rows.sort(key=lambda r: r["date"])
    ts = np.array([dt.datetime.fromisoformat(r["date"].replace("Z", "+00:00")).timestamp()
                   for r in rows])
    ts = ts - ts[0]
    speed_kmh = np.array([r.get("speed") or 0 for r in rows], dtype=float)
    dist = np.concatenate([[0.0], np.cumsum(np.diff(ts) * (speed_kmh[:-1] / 3.6))])

    return {
        "source": "openf1",
        "driver_number": driver_number,
        "lap_number": lap_number,
        "lap_time_ms": int(duration * 1000),
        "sector1_ms": int(lap["duration_sector_1"] * 1000) if lap.get("duration_sector_1") else None,
        "sector2_ms": int(lap["duration_sector_2"] * 1000) if lap.get("duration_sector_2") else None,
        "sector3_ms": int(lap["duration_sector_3"] * 1000) if lap.get("duration_sector_3") else None,
        "track_length": float(dist[-1]),
        "columns": ["dist", "time_s", "speed", "throttle", "brake", "gear", "drs", "rpm"],
        "samples": [
            [round(float(d), 1), round(float(t), 3), float(s),
             round((r.get("throttle") or 0) / 100.0, 3),
             1 if (r.get("brake") or 0) > 50 else 0,
             int(r.get("n_gear") or 0),
             1 if (r.get("drs") or 0) in (10, 12, 14) else 0,
             int(r.get("rpm") or 0)]
            for d, t, s, r in zip(dist, ts, speed_kmh, rows)
        ],
    }
