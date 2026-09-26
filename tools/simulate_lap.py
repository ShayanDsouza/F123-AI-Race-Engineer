"""Send synthetic F1 23 UDP telemetry to test the pipeline without a PS5.

Builds a physically plausible Bahrain hot-lap (forward/backward speed-profile
pass over a corner list), then packs and emits real packet-format-2023
datagrams (session, lap data, car telemetry, car status, car damage, session
history) at an accelerated rate. Two full laps are driven so two laps get
recorded.

Usage:  python tools/simulate_lap.py [--host 127.0.0.1] [--port 20777]
"""
from __future__ import annotations

import argparse
import struct
import socket
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.f123 import packets as pk  # noqa: E402

TRACK_ID = 3           # Bahrain
TRACK_LEN = 5412.0     # metres
SESSION_TYPE = 13      # Time Trial
SESSION_UID = 777_2023_0001

# (apex position m, apex speed km/h) — loosely Bahrain-shaped
CORNERS = [(600, 95), (900, 130), (1100, 85), (1900, 165), (2200, 110),
           (2650, 75), (3000, 200), (3350, 140), (3700, 90), (4150, 230),
           (4550, 120), (5000, 170)]
VMAX = 325.0


def build_lap(skill: float = 0.93) -> dict:
    """skill < 1 => slower apexes and earlier/softer braking than 'ideal'."""
    ds = 2.0
    s = np.arange(0, TRACK_LEN, ds)
    n = len(s)
    v_target = np.full(n, VMAX * min(1.0, skill + 0.05))
    for pos, apex in CORNERS:
        i = int(pos / ds)
        v_target[max(0, i - 2):i + 3] = apex * skill

    a_acc = 11.0   # m/s^2 at low speed, tapering with v
    a_brk = 38.0 * skill  # braking capability actually used
    v = v_target * 0.0 + 60.0
    # backward pass (braking limits)
    v_lim = v_target.copy()
    for i in range(n - 2, -1, -1):
        v_lim[i] = min(v_lim[i], np.sqrt(v_lim[i + 1] ** 2 + 2 * a_brk * ds * 12.96) )
    # kph^2 conversion: v in km/h -> use m/s internally instead, redo cleanly
    v_ms_target = v_lim / 3.6
    v_ms = np.zeros(n)
    v_ms[0] = min(v_ms_target[0], VMAX / 3.6 * 0.9)
    for i in range(1, n):
        accel = a_acc * max(0.25, 1.0 - (v_ms[i - 1] / (VMAX / 3.6)) ** 2)
        v_ms[i] = min(v_ms_target[i], np.sqrt(v_ms[i - 1] ** 2 + 2 * accel * ds))
    # backward again on the final profile
    for i in range(n - 2, -1, -1):
        v_ms[i] = min(v_ms[i], np.sqrt(v_ms[i + 1] ** 2 + 2 * a_brk * ds))

    dt = ds / np.maximum(v_ms, 5.0)
    t = np.concatenate([[0.0], np.cumsum(dt)])[:-1]
    dv = np.gradient(v_ms, ds)
    accel_ms2 = dv * v_ms
    # available acceleration falls with speed; throttle = demanded fraction
    avail = a_acc * np.maximum(0.25, 1.0 - (v_ms / (VMAX / 3.6)) ** 2)
    throttle = np.clip(accel_ms2 / avail, 0, 1)
    throttle[(accel_ms2 > -0.5) & (throttle > 0.6)] = 1.0   # committed = flat
    throttle[(accel_ms2 >= -0.5) & (v_ms * 3.6 > VMAX * 0.93)] = 1.0
    brake = np.clip(-accel_ms2 / 42.0, 0, 1)
    brake[brake < 0.05] = 0.0
    speed_kmh = v_ms * 3.6
    gear = np.clip((speed_kmh / 42).astype(int) + 1, 1, 8)
    rpm = (7000 + 6000 * ((speed_kmh / 3.6 % 42) / 42)).astype(int)
    drs = ((speed_kmh > 290) & (throttle > 0.95)).astype(int)
    return {"s": s, "t": t, "speed": speed_kmh, "throttle": throttle,
            "brake": brake, "gear": gear, "rpm": rpm, "drs": drs,
            "lap_time": float(t[-1] + dt[-1])}


# ------------------------------------------------------------------ packing ----
def header(packet_id: int, session_time: float, frame: int) -> bytes:
    return struct.pack(pk.HEADER_FMT, 2023, 23, 1, 22, 1, packet_id,
                       SESSION_UID, session_time, frame, frame, 0, 255)


def pad_to(data: bytes, size: int) -> bytes:
    return data + b"\x00" * (size - len(data))


def session_packet(session_time: float, frame: int) -> bytes:
    body = struct.pack(pk.SESSION_FMT, 0, 31, 26, 3, int(TRACK_LEN),
                       SESSION_TYPE, TRACK_ID, 0, 3600, 3600)
    return pad_to(header(pk.PID_SESSION, session_time, frame) + body, 644)


def sector_times(trace: dict) -> tuple:
    """(s1, s2, s3) in ms for a built lap, split at thirds of the distance."""
    t1 = float(np.interp(TRACK_LEN / 3, trace["s"], trace["t"]))
    t2 = float(np.interp(2 * TRACK_LEN / 3, trace["s"], trace["t"]))
    total = int(trace["lap_time"] * 1000)
    s1, s2 = int(t1 * 1000), int((t2 - t1) * 1000)
    return s1, s2, total - s1 - s2


def _lap_car(lap_num, lap_dist, cur_ms, last_ms, total_dist, position,
             s1=0, s2=0) -> bytes:
    """s1/s2: this lap's completed sector times so far (0 until the sector
    is done), exactly as the game reports them."""
    return struct.pack(pk.LAP_DATA_FMT, last_ms, cur_ms, s1 % 60000, s1 // 60000,
                       s2 % 60000, s2 // 60000, 0, 0,
                       lap_dist, total_dist, 0.0, position, lap_num, 0, 0,
                       min(2, int(3 * lap_dist / TRACK_LEN)), 0, 0, 0, 0, 0, 0,
                       position, 1, 2, 0, 0, 0, 0)


def lap_data_packet(session_time, frame, cars) -> bytes:
    """cars: list of (lap_num, dist, cur_ms, last_ms, total, position[, s1, s2])."""
    body = b"".join(_lap_car(*c) for c in cars)
    empty = struct.pack(pk.LAP_DATA_FMT, *([0] * 8), 0.0, 0.0, 0.0,
                        *([0] * 15), 0, 0, 0)
    body += empty * (pk.NUM_CARS - len(cars))
    return (header(pk.PID_LAP_DATA, session_time, frame) + body
            + struct.pack("<BB", 0, 0))


def _tel_car(speed, throttle, brake, gear, rpm, drs) -> bytes:
    return struct.pack(pk.CAR_TELEMETRY_FMT, int(speed), float(throttle), 0.0,
                       float(brake), 0, int(gear), int(rpm), int(drs), 50, 0,
                       400, 400, 400, 400, 90, 90, 90, 90, 95, 95, 95, 95, 105,
                       23.0, 23.0, 21.5, 21.5, 0, 0, 0, 0)


def telemetry_packet(session_time, frame, cars) -> bytes:
    """cars: list of (speed, throttle, brake, gear, rpm, drs)."""
    body = b"".join(_tel_car(*c) for c in cars)
    body += b"\x00" * pk.CAR_TELEMETRY_SIZE * (pk.NUM_CARS - len(cars))
    return (header(pk.PID_CAR_TELEMETRY, session_time, frame) + body
            + struct.pack("<BBb", 255, 255, int(cars[0][3])))


def participants_packet(session_time, frame) -> bytes:
    """Player in car 0, an AI 'VERSTAPPEN' in car 1."""
    def part(ai, driver_id, team_id, number, name):
        return struct.pack(pk.PARTICIPANT_FMT, ai, driver_id, 0, team_id, 0,
                           number, 0, name.encode("utf-8")[:47], 0, 0, 0)
    body = part(0, 255, 8, 55, "PLAYER") + part(1, 9, 2, 1, "VERSTAPPEN")
    body += b"\x00" * pk.PARTICIPANT_SIZE * (pk.NUM_CARS - 2)
    return (header(pk.PID_PARTICIPANTS, session_time, frame)
            + struct.pack("<B", 2) + body)


def status_packet(session_time, frame, fuel, ers_deployed) -> bytes:
    car = struct.pack(pk.CAR_STATUS_FMT, 2, 1, 1, 58, 0, float(fuel), 110.0,
                      float(fuel / 1.8), 13000, 3500, 8, 1, 0, 18, 16, 3, 0,
                      770.0, 120.0, 3.2e6, 2, 1.1e6, 1.4e6, float(ers_deployed), 0)
    return (header(pk.PID_CAR_STATUS, session_time, frame) + car * 2
            + b"\x00" * pk.CAR_STATUS_SIZE * (pk.NUM_CARS - 2))


def damage_packet(session_time, frame, wear) -> bytes:
    car = struct.pack(pk.CAR_DAMAGE_FMT, wear, wear, wear + 1.5, wear + 1.5,
                      *([0] * 26))
    return (header(pk.PID_CAR_DAMAGE, session_time, frame) + car * 2
            + b"\x00" * pk.CAR_DAMAGE_SIZE * (pk.NUM_CARS - 2))


def history_packet(session_time, frame, lap_times: list) -> bytes:
    """lap_times: list of (lap_ms, s1_ms, s2_ms) for car 0; sector 3 is the
    remainder."""
    fixed = struct.pack(pk.SESSION_HISTORY_FIXED_FMT, 0, len(lap_times),
                        1, 1, 1, 1, 1)
    laps = b""
    for ms, s1, s2 in lap_times:
        s3 = ms - s1 - s2
        laps += struct.pack(pk.LAP_HISTORY_FMT, ms, s1 % 60000, s1 // 60000,
                            s2 % 60000, s2 // 60000, s3 % 60000, s3 // 60000, 0x01)
    laps += b"\x00" * pk.LAP_HISTORY_SIZE * (100 - len(lap_times))
    stints = struct.pack("<BBB", 255, 18, 16) + b"\x00" * 21
    return header(pk.PID_SESSION_HISTORY, session_time, frame) + fixed + laps + stints


# ------------------------------------------------------------------ session ----
def session_datagrams(lap: dict, ai: dict, laps: int = 2, rate: float = 20.0,
                      rng_seed: int = 1):
    """Yield (delay_s, datagram) for a whole synthetic session: `laps` full
    laps of the player, plus a few ticks of the next lap so the last one
    finalizes. The AI car runs on its own clock, so it completes its (quicker)
    laps at its own times and reports its own lap and sector times. This is
    the same stream `main()` puts on the wire; tests feed it to the recorder
    directly."""
    frame = 0
    session_time = 0.0
    tick = 1.0 / rate
    lap_ms = int(lap["lap_time"] * 1000)
    ai_ms = int(ai["lap_time"] * 1000)
    lap_sectors, ai_sectors = sector_times(lap), sector_times(ai)
    done_laps: list = []                      # (ms, s1, s2) for the player

    def sectors_so_far(sec, d):
        s1 = sec[0] if d > TRACK_LEN / 3 else 0
        s2 = sec[1] if d > 2 * TRACK_LEN / 3 else 0
        return s1, s2

    for lap_num in range(1, laps + 2):
        n_ticks = 40 if lap_num > laps else int(lap["lap_time"] / tick)
        for k in range(n_ticks):
            t_lap = k * tick
            frame += 1
            session_time += tick
            cur_ms = int(t_lap * 1000)
            last_ms = done_laps[-1][0] if done_laps else 0
            # the AI car on its own lap clock
            ai_elapsed = (lap_num - 1) * lap["lap_time"] + t_lap
            ai_lap_num = int(ai_elapsed // ai["lap_time"]) + 1
            ai_t = ai_elapsed - (ai_lap_num - 1) * ai["lap_time"]
            ai_last = ai_ms if ai_lap_num > 1 else 0

            cars_lap, cars_tel = [], []
            cars = ((lap, lap_num, t_lap, cur_ms, last_ms, lap_sectors),
                    (ai, ai_lap_num, ai_t, int(ai_t * 1000), ai_last, ai_sectors))
            for pos, (trace, num, t_in, cur, last, sec) in enumerate(cars, start=1):
                d = float(np.interp(t_in, trace["t"], trace["s"]))
                d = min(d, TRACK_LEN - 0.1)
                i = min(len(trace["s"]) - 1, int(d / 2.0))
                total = (num - 1) * TRACK_LEN + d
                s1, s2 = sectors_so_far(sec, d)
                cars_lap.append((num, d, cur, last, total, pos, s1, s2))
                cars_tel.append((trace["speed"][i], trace["throttle"][i],
                                 trace["brake"][i], trace["gear"][i],
                                 trace["rpm"][i], trace["drs"][i]))
            yield tick, lap_data_packet(session_time, frame, cars_lap)
            yield 0.0, telemetry_packet(session_time, frame, cars_tel)
            if frame % 20 == 0:
                yield 0.0, session_packet(session_time, frame)
                yield 0.0, participants_packet(session_time, frame)
                total = (lap_num - 1) * TRACK_LEN + cars_lap[0][1]
                fuel = 12.0 - 1.6 * (total / TRACK_LEN)
                wear = 3.0 * (total / TRACK_LEN)
                ers = min(4e6, 4e6 * t_lap / lap["lap_time"] * 0.8)
                yield 0.0, status_packet(session_time, frame, fuel, ers)
                yield 0.0, damage_packet(session_time, frame, wear)
            if frame % 60 == 0 and done_laps:
                yield 0.0, history_packet(session_time, frame, done_laps)
        if lap_num <= laps:
            noise = int(np.random.default_rng(rng_seed + lap_num).normal(0, 250))
            done_laps.append((lap_ms + noise, lap_sectors[0], lap_sectors[1]))
            yield 0.0, history_packet(session_time, frame, done_laps)
            print(f"  lap {lap_num} complete: {done_laps[-1][0]} ms")


# --------------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20777)
    ap.add_argument("--rate", type=float, default=20.0, help="sim ticks/sec (game time)")
    ap.add_argument("--speedup", type=float, default=25.0, help="x faster than real time")
    ap.add_argument("--laps", type=int, default=2)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (args.host, args.port)
    lap = build_lap()
    # A slightly quicker AI car so there is something to compare against
    ai = build_lap(skill=0.955)
    lap_ms = int(lap["lap_time"] * 1000)
    ai_ms = int(ai["lap_time"] * 1000)
    print(f"Synthetic lap time: {lap_ms // 60000}:{lap_ms % 60000 / 1000:06.3f} "
          f"(AI VERSTAPPEN {ai_ms // 60000}:{ai_ms % 60000 / 1000:06.3f}) "
          f"over {TRACK_LEN:.0f} m — sending to {addr[0]}:{addr[1]}")

    for delay, datagram in session_datagrams(lap, ai, laps=args.laps, rate=args.rate):
        if delay:
            time.sleep(delay / args.speedup)
        sock.sendto(datagram, addr)
    print("Done.")


if __name__ == "__main__":
    main()
