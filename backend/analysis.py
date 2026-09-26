"""Comparison engine: aligns two laps on a common normalized-distance grid,
computes the time delta curve, detects corners, and measures per-corner
braking / apex / exit differences.

Traces are dicts with `columns` + `samples` (game recorder, FastF1 or OpenF1
serializations all fit). Time may be `time_ms` or `time_s`.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

GRID_N = 800
BRAKE_ON = 0.15       # brake channel threshold = "braking"
FULL_THROTTLE = 0.90


# ---------------------------------------------------------------- loading ----
def to_arrays(trace: dict) -> dict:
    cols = trace["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    data = np.asarray(trace["samples"], dtype=float)
    if data.ndim != 2 or len(data) < 20:
        raise ValueError("trace too short to analyse")

    dist = data[:, idx["dist"]]
    if "time_ms" in idx:
        t = data[:, idx["time_ms"]] / 1000.0
    else:
        t = data[:, idx["time_s"]]

    # sort by distance, drop backwards jumps and duplicates
    order = np.argsort(dist, kind="stable")
    dist, data, t = dist[order], data[order], t[order]
    keep = np.concatenate([[True], np.diff(dist) > 0.01])
    dist, data, t = dist[keep], data[keep], t[keep]

    lap_len = float(trace.get("track_length") or dist[-1])
    lap_len = max(lap_len, float(dist[-1]))

    return {
        "dist": dist,
        "x": dist / lap_len,
        "lap_len": lap_len,
        "t": t - t[0],
        "speed": data[:, idx["speed"]],
        "throttle": np.clip(data[:, idx["throttle"]], 0, 1),
        "brake": np.clip(data[:, idx["brake"]], 0, 1),
        "gear": data[:, idx["gear"]],
        "drs": data[:, idx["drs"]] if "drs" in idx else np.zeros_like(dist),
        "lap_time_s": (trace.get("lap_time_ms") or 0) / 1000.0 or None,
    }


def resample(arr: dict, grid: np.ndarray) -> dict:
    out = {"lap_len": arr["lap_len"], "lap_time_s": arr["lap_time_s"]}
    for ch in ("t", "speed", "throttle", "brake", "gear", "drs"):
        out[ch] = np.interp(grid, arr["x"], arr[ch])
    out["t"] -= out["t"][0]
    out["gear"] = np.round(out["gear"])
    out["drs"] = (out["drs"] > 0.5).astype(float)
    return out


def _smooth(y: np.ndarray, w: int = 9) -> np.ndarray:
    kernel = np.ones(w) / w
    return np.convolve(np.pad(y, w // 2, mode="edge"), kernel, mode="valid")


# ---------------------------------------------------------------- corners ----
def detect_corners(speed: np.ndarray, grid: np.ndarray, lap_len: float) -> list:
    """Local minima of the (smoothed) speed trace = corner apexes."""
    s = _smooth(speed)
    n = len(s)
    win = max(3, int(n * 100.0 / lap_len))       # ~100 m neighbourhood
    vmax = float(np.max(s))
    apex_idx = []
    for i in range(1, n - 1):
        lo, hi = max(0, i - win), min(n, i + win + 1)
        if s[i] != np.min(s[lo:hi]):
            continue
        if s[i] > vmax - 25:                      # too fast to be a corner
            continue
        prominence = min(np.max(s[lo:i + 1]), np.max(s[i:hi])) - s[i]
        if prominence < 8:                        # kink, not a corner
            continue
        if apex_idx and (grid[i] - grid[apex_idx[-1]]) * lap_len < 120:
            if s[i] < s[apex_idx[-1]]:
                apex_idx[-1] = i                  # keep the slower point
            continue
        apex_idx.append(i)
    return apex_idx


def _brake_start_before(brake: np.ndarray, apex: int) -> Optional[int]:
    """Walk back from the apex to where braking began (None = no braking)."""
    i = apex
    while i > 0 and brake[i] < BRAKE_ON:
        i -= 1
        if apex - i > 60:                         # no brake zone within ~half km
            return None
    if brake[i] < BRAKE_ON:
        return None
    while i > 0 and brake[i - 1] >= BRAKE_ON:
        i -= 1
    return i


def _throttle_full_after(throttle: np.ndarray, apex: int) -> Optional[int]:
    """First sustained full-throttle point after the apex."""
    n = len(throttle)
    for i in range(apex, min(n, apex + 80)):
        if throttle[i] >= FULL_THROTTLE:
            ahead = throttle[i:min(n, i + 5)]
            if np.all(ahead >= FULL_THROTTLE - 0.05):
                return i
    return None


# ---------------------------------------------------------------- compare ----
def compare(trace_a: dict, trace_b: dict) -> dict:
    """a = the player / lap under review, b = the reference lap."""
    A = resample(to_arrays(trace_a), np.linspace(0, 1, GRID_N))
    B = resample(to_arrays(trace_b), np.linspace(0, 1, GRID_N))
    grid = np.linspace(0, 1, GRID_N)

    delta = A["t"] - B["t"]
    # pin the curve so it ends exactly at the true lap-time difference
    if A["lap_time_s"] and B["lap_time_s"]:
        true_diff = A["lap_time_s"] - B["lap_time_s"]
        delta = delta + (true_diff - delta[-1]) * grid

    ref_len = B["lap_len"]
    apexes = detect_corners(B["speed"], grid, ref_len)

    corners = []
    for num, apex in enumerate(apexes, start=1):
        entry = {"corner": num, "apex_frac": round(float(grid[apex]), 4),
                 "apex_m": round(float(grid[apex] * ref_len))}
        for tag, tr in (("you", A), ("ref", B)):
            bs = _brake_start_before(tr["brake"], apex)
            ft = _throttle_full_after(tr["throttle"], apex)
            # this trace's own apex near the reference apex
            lo, hi = max(0, apex - 12), min(GRID_N, apex + 13)
            own_apex = lo + int(np.argmin(tr["speed"][lo:hi]))
            entry[tag] = {
                "brake_start_m": round(float((grid[apex] - grid[bs]) * ref_len))
                                 if bs is not None else None,
                "min_speed": round(float(tr["speed"][own_apex])),
                "apex_gear": int(tr["gear"][own_apex]),
                "full_throttle_after_m": round(float((grid[ft] - grid[apex]) * ref_len))
                                          if ft is not None else None,
            }
        # time lost across the corner: from earliest braking to common exit
        starts = [apex - 25]
        for tag, tr in (("you", A), ("ref", B)):
            bs = _brake_start_before(tr["brake"], apex)
            if bs is not None:
                starts.append(bs)
        seg_start = max(0, min(starts) - 3)
        seg_end = min(GRID_N - 1, apex + 40)
        entry["time_delta_s"] = round(float(delta[seg_end] - delta[seg_start]), 3)
        entry["seg"] = [round(float(grid[seg_start]), 4), round(float(grid[seg_end]), 4)]
        corners.append(entry)

    # sector splits on the reference lap's distance axis
    sectors = _sector_deltas(trace_a, trace_b, A, B, grid, delta)

    stats = {
        "lap_time_ms_you": trace_a.get("lap_time_ms"),
        "lap_time_ms_ref": trace_b.get("lap_time_ms"),
        "top_speed_you": round(float(np.max(A["speed"]))),
        "top_speed_ref": round(float(np.max(B["speed"]))),
        "avg_speed_you": round(float(np.mean(A["speed"]))),
        "avg_speed_ref": round(float(np.mean(B["speed"]))),
        "full_throttle_pct_you": round(float(np.mean(A["throttle"] >= FULL_THROTTLE)) * 100),
        "full_throttle_pct_ref": round(float(np.mean(B["throttle"] >= FULL_THROTTLE)) * 100),
        "braking_pct_you": round(float(np.mean(A["brake"] >= BRAKE_ON)) * 100),
        "braking_pct_ref": round(float(np.mean(B["brake"] >= BRAKE_ON)) * 100),
        "drs_pct_you": round(float(np.mean(A["drs"])) * 100),
        "drs_pct_ref": round(float(np.mean(B["drs"])) * 100),
        "ref_lap_len_m": round(ref_len),
    }

    def ser(tr):
        return {
            "t": [round(float(v), 3) for v in tr["t"]],
            "speed": [round(float(v), 1) for v in tr["speed"]],
            "throttle": [round(float(v), 3) for v in tr["throttle"]],
            "brake": [round(float(v), 3) for v in tr["brake"]],
            "gear": [int(v) for v in tr["gear"]],
            "drs": [int(v) for v in tr["drs"]],
        }

    return {
        "grid": [round(float(v), 4) for v in grid],
        "you": ser(A),
        "ref": ser(B),
        "delta": [round(float(v), 3) for v in delta],
        "corners": corners,
        "sectors": sectors,
        "stats": stats,
    }


def _sector_deltas(trace_a, trace_b, A, B, grid, delta) -> list:
    """Per-sector time deltas. Uses real sector times when both laps have
    them; boundaries for the chart come from the reference lap's timing."""
    out = []
    s_a = [trace_a.get(k) for k in ("sector1_ms", "sector2_ms", "sector3_ms")]
    s_b = [trace_b.get(k) for k in ("sector1_ms", "sector2_ms", "sector3_ms")]

    # boundary fractions: distance fraction of the ref lap at cumulative
    # sector times (fallback: thirds)
    bounds = [1 / 3, 2 / 3]
    if all(s_b) and B["lap_time_s"]:
        cum = np.cumsum([s / 1000.0 for s in s_b[:2]])
        bounds = [float(np.interp(c, B["t"], grid)) for c in cum]

    exact = all(s_a) and all(s_b)
    edges = [0.0] + bounds + [1.0]
    for i in range(3):
        if exact:
            d_ms = s_a[i] - s_b[i]
        else:
            lo = int(edges[i] * (len(grid) - 1))
            hi = int(edges[i + 1] * (len(grid) - 1))
            d_ms = int((delta[hi] - delta[lo]) * 1000)
        out.append({
            "sector": i + 1,
            "you_ms": s_a[i] if exact else None,
            "ref_ms": s_b[i] if exact else None,
            "exact": bool(exact),
            "delta_ms": int(d_ms),
            "from_frac": round(edges[i], 4),
            "to_frac": round(edges[i + 1], 4),
        })
    return out
