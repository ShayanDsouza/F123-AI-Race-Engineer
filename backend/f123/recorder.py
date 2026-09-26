"""Assembles per-lap telemetry traces from the F1 23 UDP packet stream.

The game broadcasts data for every car on track, not just yours, so this
records the AI opponents too — that's what makes "my lap vs Verstappen's lap
in the game" possible alongside the real-2023 comparison.

The player's trace is kept at full resolution; opponents are decimated by
distance (a sample every few metres) to keep a full grid's worth of laps to a
sane size on disk.

Flashbacks rewind lap distance (and the game's lap timer) without changing the
lap counter: the samples the driver is about to re-drive are dropped so the
trace stays monotonic. A rewind across the start line, and a session restart,
discard the partial lap instead.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional

from . import packets as pk

OPPONENT_SAMPLE_METRES = 3.0   # decimation for non-player cars
SESSION_TAKEOVER_S = 5.0       # quiet period before another session may take over
FLASHBACK_REWIND_M = 30.0      # backwards jump in lap distance that means a flashback
LAP_TIME_TOLERANCE_MS = 1500   # game lap time vs trace clock before we distrust it


def _new_thermal() -> dict:
    """Accumulators for one lap. Wheel order is RL, RR, FL, FR."""
    return {"n": 0, "surface": [0.0] * 4, "inner": [0.0] * 4,
            "pressure": [0.0] * 4, "brake_max": [0.0] * 4}


class CarState:
    """Per-car lap assembly state."""

    __slots__ = ("samples", "current_lap_num", "lap_invalid",
                 "last_lap_distance", "last_sample_dist", "latest_lap",
                 "latest_status", "latest_damage", "latest_telemetry",
                 "sector_history", "wear_at_lap", "fuel_at_lap", "thermal",
                 "buffer_lap_num", "flashbacks", "wrap_guard")

    def __init__(self):
        self.samples: list = []
        self.current_lap_num: Optional[int] = None
        self.lap_invalid: bool = False
        self.last_lap_distance: float = -1.0
        self.last_sample_dist: float = -1e9
        self.latest_lap: Optional[pk.LapData] = None
        self.latest_status: Optional[pk.CarStatus] = None
        self.latest_damage: Optional[pk.CarDamage] = None
        self.latest_telemetry: Optional[pk.CarTelemetry] = None
        self.sector_history: dict = {}
        self.wear_at_lap: dict = {}
        self.fuel_at_lap: dict = {}
        # running sums for this lap: tyre/brake temps and pressures are the
        # only real setup signal the game gives us
        self.thermal: dict = _new_thermal()
        # The lap number the buffered samples belong to. The game's counter
        # can lag the distance reset by seconds (pit exit) or lead it by a
        # frame, so it is read mid-lap, never from the packet that opened or
        # closed the lap.
        self.buffer_lap_num: Optional[int] = None
        self.flashbacks: int = 0
        self.wrap_guard: bool = False

    def discard(self):
        self.samples = []
        self.lap_invalid = False
        self.last_lap_distance = -1.0
        self.last_sample_dist = -1e9
        self.thermal = _new_thermal()
        self.buffer_lap_num = None
        self.flashbacks = 0


def _thermal_summary(th: dict) -> Optional[dict]:
    """Mean tyre temps/pressures and peak brake temps for the lap.

    Wheel order is RL, RR, FL, FR, so front/rear and left/right balance can be
    read straight off it — that is what setup changes are judged on.
    """
    n = th.get("n") or 0
    if n < 20:
        return None
    avg = lambda key: [round(th[key][w] / n, 1) for w in range(4)]
    surface, inner, pressure = avg("surface"), avg("inner"), avg("pressure")
    return {
        "wheel_order": ["RL", "RR", "FL", "FR"],
        "tyre_surface_c": surface,
        "tyre_inner_c": inner,
        "tyre_pressure_psi": pressure,
        "brake_peak_c": [round(v) for v in th["brake_max"]],
        "front_rear_surface_delta_c": round(
            (surface[2] + surface[3]) / 2 - (surface[0] + surface[1]) / 2, 1),
        "left_right_surface_delta_c": round(
            (surface[0] + surface[2]) / 2 - (surface[1] + surface[3]) / 2, 1),
    }


def _sector_times(hist: Optional[pk.LapHistoryEntry],
                  closing_lap: Optional[pk.LapData],
                  lap_time_ms: int) -> tuple:
    """Official sector times for the lap just completed, or (None,)*3.

    The session-history packet is the authority but arrives up to a second
    after the line, so it usually is not there yet. The last LapData packet
    of the lap carries sectors 1 and 2 (they are zeroed once the counter
    ticks over); sector 3 is what remains of the lap time.
    """
    if hist and hist.lap_time_ms > 0 and hist.sector1_ms > 0 \
            and hist.sector2_ms > 0 and hist.sector3_ms > 0:
        return hist.sector1_ms, hist.sector2_ms, hist.sector3_ms
    if closing_lap is not None and lap_time_ms > 0:
        s1 = closing_lap.sector1_time_ms + 60000 * closing_lap.sector1_minutes
        s2 = closing_lap.sector2_time_ms + 60000 * closing_lap.sector2_minutes
        s3 = lap_time_ms - s1 - s2
        if s1 > 0 and s2 > 0 and s3 > 0:
            return s1, s2, s3
    return None, None, None


class LapRecorder:
    def __init__(self, on_lap_complete: Callable[[dict], None]):
        self._on_lap_complete = on_lap_complete
        self._lock = threading.Lock()
        self.record_opponents: bool = True
        self.reset_session_state()
        # live state exposed to the UI
        self.last_packet_time: float = 0.0
        self.packets_seen: int = 0
        self.wrong_format_seen: Optional[int] = None
        self._tel_times: deque = deque(maxlen=120)
        self.last_finalize: Optional[dict] = None
        self.discards: int = 0
        self.last_discard: Optional[dict] = None
        self.ignored_packets: int = 0

    def reset_session_state(self):
        self.session_uid: Optional[int] = None
        self.session_seen_at: float = 0.0
        self.session: Optional[pk.SessionData] = None
        self.player_index: int = 0
        self.cars: dict = {}                 # car_index -> CarState
        self.participants: dict = {}         # car_index -> pk.Participant

    def _car(self, index: int) -> CarState:
        car = self.cars.get(index)
        if car is None:
            car = self.cars[index] = CarState()
        return car

    # ------------------------------------------------------------------
    def feed(self, data: bytes):
        header = pk.parse_header(data)
        if header is None:
            return
        self.packets_seen += 1
        self.last_packet_time = time.time()

        if header.packet_format != pk.PACKET_FORMAT:
            self.wrong_format_seen = header.packet_format
            return

        with self._lock:
            # Lock onto one session. Another source on the same port (a second
            # console, a stale simulator) would otherwise reset our state on
            # every alternating packet and no lap would ever complete. A
            # genuinely new session takes over once the old one goes quiet.
            now = time.time()
            if self.session_uid is None:
                self.session_uid = header.session_uid
                self.session_seen_at = now
            elif header.session_uid != self.session_uid:
                if now - self.session_seen_at < SESSION_TAKEOVER_S:
                    self.ignored_packets += 1
                    return
                self.reset_session_state()
                self.session_uid = header.session_uid
                self.session_seen_at = now
            else:
                self.session_seen_at = now
            self.player_index = header.player_car_index

            pid = header.packet_id
            if pid == pk.PID_SESSION:
                self.session = pk.parse_session(data)
            elif pid == pk.PID_PARTICIPANTS:
                parts = pk.parse_participants(data)
                if parts:
                    self.participants = {i: p for i, p in enumerate(parts)}
            elif pid == pk.PID_LAP_DATA:
                self._on_lap_data(data)
            elif pid == pk.PID_CAR_TELEMETRY:
                self._on_car_telemetry(data)
            elif pid == pk.PID_CAR_STATUS:
                for idx in self._tracked_indices():
                    st = pk.parse_car_status(data, idx)
                    if st:
                        self._car(idx).latest_status = st
            elif pid == pk.PID_CAR_DAMAGE:
                for idx in self._tracked_indices():
                    dmg = pk.parse_car_damage(data, idx)
                    if dmg:
                        self._car(idx).latest_damage = dmg
            elif pid == pk.PID_SESSION_HISTORY:
                hist = pk.parse_session_history(data)
                if hist:
                    car = self._car(hist.car_index)
                    for i, lap in enumerate(hist.laps):
                        car.sector_history[i + 1] = lap
            elif pid == pk.PID_FINAL_CLASSIFICATION:
                for idx in list(self.cars):
                    self._finalize_lap(idx, abandoned=True)

    def _tracked_indices(self) -> range:
        return range(pk.NUM_CARS) if self.record_opponents \
            else range(self.player_index, self.player_index + 1)

    # ------------------------------------------------------------------
    def _on_lap_data(self, data: bytes):
        for idx in self._tracked_indices():
            lap = pk.parse_lap_data(data, idx)
            if lap is None:
                continue
            # Cars that never leave the garage report nothing useful
            if lap.result_status == 0 and lap.current_lap_num == 0:
                continue
            car = self._car(idx)
            prev = car.latest_lap
            d = lap.lap_distance
            last = car.last_lap_distance
            track_len = self.session.track_length if self.session else 0

            # A completed lap is detected by the DISTANCE wrapping from near
            # the end of the lap back to the start, NOT by the game's lap
            # counter. Coming out of the pits the counter can lag the distance
            # reset by many seconds, which used to bleed the first ~2 km of the
            # first flying lap into the out-lap's buffer (that lost the opening
            # of every first flying lap). The distance reset does not lag.
            wrapped = (track_len and last > 0.6 * track_len
                       and 0 <= d < 0.2 * track_len)
            # Flashback back across the start line: distance leaps forward by
            # most of a lap. The lap we were building is gone.
            leapt_back_a_lap = (track_len and last >= 0
                                and d > last + 0.5 * track_len)
            # Flashback within the lap: distance rewinds by more than a few
            # metres. The rewound stretch is about to be driven again.
            rewound = last >= 0 and d < last - FLASHBACK_REWIND_M

            if car.current_lap_num is None:
                car.current_lap_num = lap.current_lap_num
            elif lap.current_lap_num < car.current_lap_num:
                # session restart / rewind to an earlier lap
                car.discard()
                car.current_lap_num = lap.current_lap_num
                car.wrap_guard = False
            else:
                car.current_lap_num = lap.current_lap_num  # keep label in sync

            if wrapped and not car.wrap_guard:
                self._finalize_lap(idx, last_lap_time_ms=lap.last_lap_time_ms,
                                   closing_lap=prev)
                car.wrap_guard = True            # don't re-fire near the line
            elif leapt_back_a_lap:
                car.discard()
                car.wrap_guard = False           # the line will be crossed again
                self.discards += 1
                self.last_discard = {"car": idx, "at": time.strftime("%H:%M:%S"),
                                     "reason": "flashback across the start line"}
            elif rewound and not wrapped:
                # keep only what lies before the point we rewound to
                car.samples = [smp for smp in car.samples if smp[0] < d]
                car.last_sample_dist = car.samples[-1][0] if car.samples else -1e9
                car.flashbacks += 1
            elif d > 800:
                car.wrap_guard = False           # clearly into the new lap

            car.last_lap_distance = d
            # From here on this packet describes the lap now being buffered.
            # Near the line the counter may not have ticked yet (or may have
            # ticked early); by mid-lap it is unambiguous.
            mid_lap = track_len and 0.2 * track_len < d < 0.8 * track_len
            if car.buffer_lap_num is None or mid_lap:
                car.buffer_lap_num = lap.current_lap_num

            if lap.current_lap_invalid:
                car.lap_invalid = True
            car.latest_lap = lap

            n = lap.current_lap_num
            if car.latest_status and n not in car.fuel_at_lap:
                car.fuel_at_lap[n] = round(car.latest_status.fuel_in_tank, 2)
            if car.latest_damage and n not in car.wear_at_lap:
                car.wear_at_lap[n] = round(sum(car.latest_damage.tyres_wear) / 4.0, 2)

    def _on_car_telemetry(self, data: bytes):
        self._tel_times.append(time.time())
        for idx in self._tracked_indices():
            tel = pk.parse_car_telemetry(data, idx)
            if tel is None:
                continue
            car = self._car(idx)
            car.latest_telemetry = tel
            lap = car.latest_lap
            if lap is None or lap.lap_distance < 0 or lap.driver_status == 0:
                continue
            # Opponents are sampled by distance to keep a full grid affordable
            if idx != self.player_index:
                if lap.lap_distance - car.last_sample_dist < OPPONENT_SAMPLE_METRES:
                    continue
                car.last_sample_dist = lap.lap_distance
            if idx == self.player_index:
                th = car.thermal
                th["n"] += 1
                for w in range(4):
                    th["surface"][w] += tel.tyres_surface_temperature[w]
                    th["inner"][w] += tel.tyres_inner_temperature[w]
                    th["pressure"][w] += tel.tyres_pressure[w]
                    th["brake_max"][w] = max(th["brake_max"][w],
                                             tel.brakes_temperature[w])
            car.samples.append((
                round(lap.lap_distance, 1),
                lap.current_lap_time_ms,
                tel.speed,
                round(tel.throttle, 3),
                round(tel.brake, 3),
                tel.gear,
                tel.drs,
                round(tel.steer, 3),
                tel.engine_rpm,
            ))

    # ------------------------------------------------------------------
    def _finalize_lap(self, car_index: int, last_lap_time_ms: int = 0,
                      abandoned: bool = False,
                      closing_lap: Optional[pk.LapData] = None):
        """Close the buffered lap for one car and hand it to storage.

        `closing_lap` is the last LapData packet *before* the distance reset:
        it still carries the completed lap's sector 1 and 2 times, which the
        packet that closed the lap has usually already zeroed.
        """
        car = self.cars.get(car_index)
        if car is None:
            return
        samples = car.samples
        car.samples = []
        invalid = car.lap_invalid
        car.lap_invalid = False
        car.last_lap_distance = -1.0
        car.last_sample_dist = -1e9
        thermal_done, car.thermal = car.thermal, _new_thermal()
        lap_num = car.buffer_lap_num if car.buffer_lap_num is not None \
            else (car.current_lap_num or 0)
        car.buffer_lap_num = None
        flashbacks, car.flashbacks = car.flashbacks, 0

        is_player = car_index == self.player_index
        track_len = self.session.track_length if self.session else 0
        coverage = (samples[-1][0] - samples[0][0]) if samples else 0
        trace_ms = samples[-1][1] if samples else 0
        # The game's lastLapTime is exact — unless it has not been updated
        # yet (the counter lag above), in which case it is the previous lap's.
        # The trace clock is at most a frame short, so a big disagreement
        # means the game value is stale.
        lap_time = last_lap_time_ms
        lap_time_estimated = False
        if trace_ms and (lap_time <= 0
                         or abs(lap_time - trace_ms) > LAP_TIME_TOLERANCE_MS):
            lap_time, lap_time_estimated = trace_ms, True
        min_samples = 50 if is_player else 25

        if is_player:
            self.last_finalize = {
                "at": time.strftime("%H:%M:%S"),
                "lap_number": lap_num,
                "samples": len(samples), "coverage_m": round(coverage),
                "first_m": round(samples[0][0]) if samples else None,
                "last_m": round(samples[-1][0]) if samples else None,
                "track_len": track_len, "lap_time_ms": lap_time,
                "flashbacks": flashbacks,
                "saved": False, "reason": None,
            }

        def reject(reason: str):
            if is_player:
                self.last_finalize["reason"] = reason

        if abandoned:
            return reject("session ended mid-lap")
        if len(samples) < min_samples:
            return reject(f"only {len(samples)} telemetry samples collected")
        if lap_time <= 0:
            return reject("no lap time reported")
        if track_len and coverage < 0.75 * track_len:
            return reject(f"trace covered {round(coverage)}m of {track_len}m "
                          f"(need {round(0.75 * track_len)}m)")
        if is_player:
            self.last_finalize["saved"] = True

        s1, s2, s3 = _sector_times(car.sector_history.get(lap_num),
                                   closing_lap, lap_time)
        part = self.participants.get(car_index)
        status, damage = car.latest_status, car.latest_damage
        record = {
            "source": "f123",
            "is_player": is_player,
            "car_index": car_index,
            "driver": (part.name if part else
                       ("You" if is_player else f"Car {car_index + 1}")),
            "team": pk.TEAM_NAMES.get(part.team_id) if part else None,
            "ai_controlled": bool(part.ai_controlled) if part else not is_player,
            "recorded_at": time.time(),
            "session_uid": str(self.session_uid),
            "track_id": self.session.track_id if self.session else -1,
            "track_name": self.session.track_name if self.session else "Unknown",
            "track_length": track_len,
            "session_type": self.session.session_name if self.session else "Unknown",
            "weather": pk.WEATHER.get(self.session.weather, "?") if self.session else "?",
            "air_temp": self.session.air_temperature if self.session else None,
            "track_temp": self.session.track_temperature if self.session else None,
            "lap_number": lap_num,
            "lap_time_ms": lap_time,
            "lap_time_estimated": lap_time_estimated,
            "flashbacks": flashbacks,
            "invalid": invalid,
            "sector1_ms": s1,
            "sector2_ms": s2,
            "sector3_ms": s3,
            "tyre": status.tyre_name if status else None,
            "tyre_age": status.tyres_age_laps if status else None,
            "fuel_kg": round(status.fuel_in_tank, 1) if status else None,
            "ers_deployed_mj": round(status.ers_deployed_this_lap / 1e6, 2)
                               if status else None,
            "avg_tyre_wear": round(sum(damage.tyres_wear) / 4.0, 1) if damage else None,
            "fuel_trend": dict(sorted(car.fuel_at_lap.items())) if is_player else {},
            "wear_trend": dict(sorted(car.wear_at_lap.items())) if is_player else {},
            "thermal": _thermal_summary(thermal_done) if is_player else None,
            "columns": ["dist", "time_ms", "speed", "throttle", "brake",
                        "gear", "drs", "steer", "rpm"],
            "samples": samples,
        }
        self._on_lap_complete(record)

    # ------------------------------------------------------------------
    def _telemetry_hz(self) -> Optional[float]:
        times = self._tel_times
        if len(times) < 10:
            return None
        span = times[-1] - times[0]
        if span <= 0 or (time.time() - times[-1]) > 2.0:
            return None
        return round((len(times) - 1) / span, 1)

    def live_status(self) -> dict:
        with self._lock:
            car = self.cars.get(self.player_index)
            lap = car.latest_lap if car else None
            tel = car.latest_telemetry if car else None
            status = car.latest_status if car else None
            damage = car.latest_damage if car else None
            return {
                "receiving": (time.time() - self.last_packet_time) < 3.0,
                "packets_seen": self.packets_seen,
                "wrong_format_seen": self.wrong_format_seen,
                "telemetry_hz": self._telemetry_hz(),
                "samples_buffered": len(car.samples) if car else 0,
                "driver_status": lap.driver_status if lap else None,
                "last_finalize": self.last_finalize,
                "discards": self.discards,
                "last_discard": self.last_discard,
                "cars_tracked": len(self.cars),
                "ignored_packets": self.ignored_packets,
                "session_uid": str(self.session_uid) if self.session_uid else None,
                "flashbacks_this_lap": car.flashbacks if car else 0,
                "session": {
                    "track": self.session.track_name,
                    "track_id": self.session.track_id,
                    "type": self.session.session_name,
                    "weather": pk.WEATHER.get(self.session.weather, "?"),
                    "track_length": self.session.track_length,
                    "total_laps": self.session.total_laps or None,
                    "time_left_s": self.session.session_time_left or None,
                    "duration_s": self.session.session_duration or None,
                    "air_temp": self.session.air_temperature,
                    "track_temp": self.session.track_temperature,
                } if self.session else None,
                "race": {
                    "delta_ahead_ms": lap.delta_car_in_front_ms or None,
                    "delta_leader_ms": lap.delta_race_leader_ms or None,
                    "pit_stops": lap.num_pit_stops,
                    "pit_status": lap.pit_status,
                    "penalties": lap.penalties,
                    "wear_trend": dict(sorted(car.wear_at_lap.items())) if car else {},
                    "fuel_trend": dict(sorted(car.fuel_at_lap.items())) if car else {},
                } if lap else None,
                "lap": {
                    "number": lap.current_lap_num,
                    "distance": lap.lap_distance,
                    "current_time_ms": lap.current_lap_time_ms,
                    "last_time_ms": lap.last_lap_time_ms,
                    "position": lap.car_position,
                    "invalid": bool(lap.current_lap_invalid),
                } if lap else None,
                "car": {
                    "speed": tel.speed if tel else None,
                    "gear": tel.gear if tel else None,
                    "rpm": tel.engine_rpm if tel else None,
                    "drs": tel.drs if tel else None,
                    "throttle": tel.throttle if tel else None,
                    "brake": tel.brake if tel else None,
                    "tyre": status.tyre_name if status else None,
                    "tyre_age": status.tyres_age_laps if status else None,
                    "tyre_wear": round(sum(damage.tyres_wear) / 4.0, 1) if damage else None,
                    "fuel_kg": round(status.fuel_in_tank, 1) if status else None,
                    "fuel_laps": round(status.fuel_remaining_laps, 1) if status else None,
                    "ers_pct": round(status.ers_store_energy / 4e6 * 100)
                               if status else None,
                } if tel else None,
            }
