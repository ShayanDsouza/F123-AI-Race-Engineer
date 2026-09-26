"""Turns your recorded laps into a setup-and-improvement briefing.

The race radio works from the instantaneous car state. This works from the
session so far: pace and consistency per compound, tyre degradation, the
thermal picture that setup changes are judged on, and — where a reference lap
exists — a corner-by-corner account of where the time actually went.
"""
from __future__ import annotations

import statistics
from typing import Optional

from . import analysis, store


def _fmt(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


def _clean(laps: list) -> list:
    """Timed, valid, non-outlier laps — in/out laps distort every average."""
    timed = [l for l in laps if l.get("lap_time_ms") and not l.get("invalid")]
    if len(timed) < 3:
        return timed
    best = min(l["lap_time_ms"] for l in timed)
    return [l for l in timed if l["lap_time_ms"] < best * 1.07]


def compound_summary(laps: list) -> list:
    """Pace and consistency per tyre compound."""
    by: dict = {}
    for l in laps:
        by.setdefault(l.get("tyre") or "Unknown", []).append(l)

    out = []
    for compound, group in by.items():
        clean = _clean(group)
        if not clean:
            continue
        times = sorted(l["lap_time_ms"] for l in clean)
        entry = {
            "compound": compound,
            "laps_counted": len(clean),
            "laps_total": len(group),
            "best": _fmt(times[0]),
            "median": _fmt(int(statistics.median(times))),
            "spread_s": round((times[-1] - times[0]) / 1000, 3),
        }
        if len(times) >= 3:
            entry["consistency_stdev_s"] = round(statistics.pstdev(times) / 1000, 3)
        # degradation: lap time against tyre age, if the stint is long enough
        aged = [(l["tyre_age"], l["lap_time_ms"]) for l in clean
                if l.get("tyre_age") is not None]
        if len(aged) >= 4 and len({a for a, _ in aged}) >= 3:
            xs = [a for a, _ in aged]
            ys = [t for _, t in aged]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            denom = sum((x - mx) ** 2 for x in xs)
            if denom:
                slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
                entry["deg_s_per_lap"] = round(slope / 1000, 3)
        out.append(entry)
    out.sort(key=lambda e: e["best"] or "9")
    return out


def thermal_picture(laps: list) -> Optional[dict]:
    """Average the per-lap thermals across the cleanest laps."""
    have = [l for l in _clean(laps) if l.get("thermal")]
    if not have:
        return None
    # laps carry only a summary id in the list view — reload for the detail
    full = [store.load_lap(l["id"]) for l in have[:8]]
    th = [f["thermal"] for f in full if f and f.get("thermal")]
    if not th:
        return None
    n = len(th)
    mean4 = lambda key: [round(sum(t[key][w] for t in th) / n, 1) for w in range(4)]
    return {
        "laps_averaged": n,
        "wheel_order": ["RL", "RR", "FL", "FR"],
        "tyre_surface_c": mean4("tyre_surface_c"),
        "tyre_inner_c": mean4("tyre_inner_c"),
        "tyre_pressure_psi": mean4("tyre_pressure_psi"),
        "brake_peak_c": mean4("brake_peak_c"),
        "front_rear_surface_delta_c": round(
            sum(t["front_rear_surface_delta_c"] for t in th) / n, 1),
        "left_right_surface_delta_c": round(
            sum(t["left_right_surface_delta_c"] for t in th) / n, 1),
    }


def corner_comparison(best_lap: dict, reference: dict) -> Optional[dict]:
    """Where the time went, corner by corner, against a reference lap."""
    try:
        cmp = analysis.compare(best_lap, reference)
    except Exception:
        return None
    losses = sorted(cmp["corners"], key=lambda c: -c["time_delta_s"])[:6]
    ref_len = cmp["stats"].get("ref_lap_len_m") or 1
    return {
        "reference": reference.get("driver") or "reference",
        "reference_lap_ms": reference.get("lap_time_ms"),
        "your_lap_ms": best_lap.get("lap_time_ms"),
        "ref_lap_len_m": ref_len,
        "top_speed_you": cmp["stats"]["top_speed_you"],
        "top_speed_ref": cmp["stats"]["top_speed_ref"],
        "full_throttle_pct_you": cmp["stats"]["full_throttle_pct_you"],
        "full_throttle_pct_ref": cmp["stats"]["full_throttle_pct_ref"],
        # every corner detected, for the track map (not only the losses)
        "all_corners": [
            {"corner": c["corner"], "frac": c["apex_frac"],
             "time_lost_s": c["time_delta_s"]}
            for c in cmp["corners"]
        ],
        "corners_losing_most": [
            {
                "corner": c["corner"],
                "metres_into_lap": c["apex_m"],
                "frac": c["apex_frac"],
                "time_lost_s": c["time_delta_s"],
                "your_brake_point_m_before_apex": c["you"].get("brake_start_m"),
                "ref_brake_point_m_before_apex": c["ref"].get("brake_start_m"),
                "your_min_speed": c["you"]["min_speed"],
                "ref_min_speed": c["ref"]["min_speed"],
                "your_apex_gear": c["you"]["apex_gear"],
                "ref_apex_gear": c["ref"]["apex_gear"],
                "your_full_throttle_after_m": c["you"].get("full_throttle_after_m"),
                "ref_full_throttle_after_m": c["ref"].get("full_throttle_after_m"),
            }
            for c in losses if c["time_delta_s"] > 0.02
        ],
    }


def build(track: Optional[str] = None, session_type: Optional[str] = None,
          compound: Optional[str] = None) -> dict:
    """Assemble everything the engineer needs to talk about your session."""
    all_laps = store.list_laps()
    mine = [l for l in all_laps if l.get("is_player")]
    # Default to the circuit most recently driven, and always filter to a
    # single circuit — mixing tracks makes every pace figure meaningless.
    if not track and mine:
        track = mine[0].get("track_name")
    if track:
        mine = [l for l in mine if l.get("track_name") == track]
    if session_type:
        mine = [l for l in mine if l.get("session_type") == session_type]
    if compound:
        mine = [l for l in mine if l.get("tyre") == compound]

    if not mine:
        raise ValueError(
            "No recorded laps to debrief yet. Drive a few laps first — the "
            "engineer needs your own lap history to judge pace and setup.")

    track = track or mine[0].get("track_name")
    clean = _clean(mine)
    pool = clean or mine
    best = min(pool, key=lambda l: l["lap_time_ms"])
    best_full = store.load_lap(best["id"])

    # The car to measure against. In a qualifying session that is whoever
    # topped the session — the pole-sitter — from the same session where
    # possible, so the comparison is like-for-like.
    ref_full, ref_meta = None, None
    opponents = [l for l in all_laps
                 if not l.get("is_player") and l.get("track_name") == track
                 and l.get("lap_time_ms")]
    same_session = [l for l in opponents
                    if l.get("session_uid") == best.get("session_uid")]
    pool_ref = same_session or opponents
    if pool_ref:
        ref_meta = min(pool_ref, key=lambda l: l["lap_time_ms"])
        ref_full = store.load_lap(ref_meta["id"])

    corners = None
    if best_full and ref_full:
        corners = corner_comparison(best_full, ref_full)

    # Qualifying framing: are you on pole, and if not, by how much?
    stype = (best.get("session_type") or "").upper()
    is_quali = stype.startswith("Q") or "SHOOTOUT" in stype
    quali = None
    if is_quali and ref_meta:
        gap = best["lap_time_ms"] - ref_meta["lap_time_ms"]
        quali = {
            "session": best.get("session_type"),
            "your_best": _fmt(best.get("lap_time_ms")),
            "session_topper": ref_meta.get("driver"),
            "session_topper_team": ref_meta.get("team"),
            "session_topper_best": _fmt(ref_meta.get("lap_time_ms")),
            "on_pole": gap <= 0,
            "gap_to_pole_s": round(gap / 1000, 3),
            "same_session": bool(same_session),
        }

    from .f123 import packets as pk
    event_2023 = pk.TRACK_TO_2023_EVENT.get(mine[0].get("track_id")) \
        if mine[0].get("track_id") not in pk.SHORT_LAYOUT_IDS else None

    return {
        "track": track,
        "event_2023": event_2023,       # for the track-map outline
        "sessions": sorted({l.get("session_type") for l in mine if l.get("session_type")}),
        "laps_recorded": len(mine),
        "laps_used_for_pace": len(clean),
        "your_best": {
            "time": _fmt(best.get("lap_time_ms")),
            "lap_number": best.get("lap_number"),
            "compound": best.get("tyre"),
            "session": best.get("session_type"),
        },
        "qualifying": quali,
        "by_compound": compound_summary(mine),
        "thermals": thermal_picture(mine),
        "weather": mine[0].get("weather"),
        "track_temp_c": mine[0].get("track_temp"),
        "air_temp_c": mine[0].get("air_temp"),
        "corner_analysis": corners,
        "note_if_no_reference": None if corners else
            "No AI or reference lap recorded at this circuit yet, so corner-level "
            "comparison is unavailable — judge the driving from pace and "
            "consistency only, and say so.",
    }
