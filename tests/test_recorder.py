"""Lap assembly from the packet stream: lap numbering at the line, sector
times, flashbacks, restarts and the session lock."""
import numpy as np

from backend.f123 import packets as pk
from backend.f123.recorder import LapRecorder
from tools import simulate_lap as sim


def _by_car(records):
    out = {}
    for r in records:
        out.setdefault(r["car_index"], []).append(r)
    return out


def test_two_laps_per_car_with_the_right_numbers(recorded, traces):
    lap, ai = traces
    by_car = _by_car(recorded)
    assert sorted(by_car) == [0, 1]
    for idx, recs in by_car.items():
        assert [r["lap_number"] for r in recs] == [1, 2]
        expected = int((lap if idx == 0 else ai)["lap_time"] * 1000)
        for r in recs:
            assert abs(r["lap_time_ms"] - expected) < 1500
            assert r["lap_time_estimated"] is False
            assert r["flashbacks"] == 0
            assert r["track_name"] == "Bahrain" and r["session_type"] == "Time Trial"
            dist = np.array([s[0] for s in r["samples"]])
            assert dist[-1] - dist[0] > 0.9 * sim.TRACK_LEN
            assert np.all(np.diff(dist) >= 0)


def test_player_and_opponent_identity(recorded):
    by_car = _by_car(recorded)
    you, ai = by_car[0][0], by_car[1][0]
    assert you["is_player"] and you["driver"] == "PLAYER"
    assert not ai["is_player"] and ai["driver"] == "VERSTAPPEN"
    assert ai["team"] == "Red Bull Racing" and ai["ai_controlled"]
    # the player is kept at full rate, opponents decimated to one per 3 m
    assert len(ai["samples"]) <= sim.TRACK_LEN / 3 + 1
    assert len(you["samples"]) >= 0.98 * (you["lap_time_ms"] / 50)   # 20 Hz sim
    assert you["thermal"] is not None and ai["thermal"] is None
    assert you["thermal"]["wheel_order"] == ["RL", "RR", "FL", "FR"]
    assert you["tyre"] == "Soft" and you["fuel_kg"] is not None


def test_sector_times_for_both_cars(recorded, traces):
    """The player's come from the session-history packet; the AI never gets
    one in the simulator, so its sectors come from the closing LapData."""
    lap, ai = traces
    for idx, trace in ((0, lap), (1, ai)):
        s1, s2, _ = sim.sector_times(trace)
        for r in _by_car(recorded)[idx]:
            assert (r["sector1_ms"], r["sector2_ms"]) == (s1, s2)
            assert r["sector1_ms"] + r["sector2_ms"] + r["sector3_ms"] == r["lap_time_ms"]


# ---------------------------------------------------------- hand-rolled ----
class Car:
    """Drive one car through hand-crafted LapData/telemetry packets."""

    def __init__(self, recorder: LapRecorder):
        self.rec = recorder
        self.frame = 0
        self.rec.feed(sim.session_packet(0.0, 0))
        self.rec.feed(sim.participants_packet(0.0, 0))

    def step(self, lap_num, dist, cur_ms, last_ms=0, s1=0, s2=0):
        self.frame += 1
        body = struct_lap(lap_num, dist, cur_ms, last_ms, s1, s2)
        empty = struct_lap(0, 0.0, 0, 0)
        data = (sim.header(pk.PID_LAP_DATA, self.frame / 60, self.frame)
                + body + empty * (pk.NUM_CARS - 1) + b"\x00\x00")
        self.rec.feed(data)
        self.rec.feed(sim.telemetry_packet(self.frame / 60, self.frame,
                                           [(200, 1.0, 0.0, 6, 10000, 0)]))

    def lap(self, lap_num, ms_per_lap=90000, step_m=10.0, last_ms=0,
            counter_at_line=None, s1=None, s2=None):
        """One full lap. `counter_at_line` lets the lap counter tick late or
        early relative to the distance reset."""
        n = int(sim.TRACK_LEN / step_m)
        s1 = s1 if s1 is not None else ms_per_lap // 3
        s2 = s2 if s2 is not None else ms_per_lap // 3
        for i in range(n):
            d = i * step_m
            cur = int(ms_per_lap * d / sim.TRACK_LEN)
            num = lap_num
            if counter_at_line is not None and i < counter_at_line[0]:
                num = counter_at_line[1]
            self.step(num, d, cur, last_ms,
                      s1=s1 if d > sim.TRACK_LEN / 3 else 0,
                      s2=s2 if d > 2 * sim.TRACK_LEN / 3 else 0)


def struct_lap(lap_num, dist, cur_ms, last_ms, s1=0, s2=0):
    import struct
    return struct.pack(pk.LAP_DATA_FMT, last_ms, cur_ms, s1 % 60000, s1 // 60000,
                       s2 % 60000, s2 // 60000, 0, 0, float(dist),
                       float(dist), 0.0, 1, lap_num, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                       1, 1, 2, 0, 0, 0, 0)


def test_lap_counter_lagging_the_distance_reset():
    """Coming out of the pits the counter can stay on N for seconds after the
    distance has reset. The saved lap must still be numbered correctly."""
    records = []
    car = Car(LapRecorder(on_lap_complete=records.append))
    car.lap(1)
    # lap 2: the counter still says 1 for the first 60 packets (600 m)
    car.lap(2, last_ms=90000, counter_at_line=(60, 1))
    car.lap(3, last_ms=90000)
    assert [r["lap_number"] for r in records] == [1, 2]


def test_lap_counter_leading_the_distance_reset():
    """If the counter ticks a frame before the distance resets, the lap that
    just finished must not be labelled with the new number."""
    records = []
    car = Car(LapRecorder(on_lap_complete=records.append))
    car.lap(1)
    car.step(2, sim.TRACK_LEN - 1, 89990, 0)     # counter ticks early, still near the line
    car.lap(2, last_ms=90000)
    car.lap(3, last_ms=90000)
    assert [r["lap_number"] for r in records] == [1, 2]


def test_sector_times_from_the_closing_lap_data_packet():
    """Session history arrives late; the last LapData packet before the line
    still carries sectors 1 and 2, and sector 3 is the remainder."""
    records = []
    car = Car(LapRecorder(on_lap_complete=records.append))
    car.lap(1, s1=28000, s2=31000)
    car.lap(2, last_ms=90000)
    assert len(records) == 1
    r = records[0]
    assert (r["sector1_ms"], r["sector2_ms"], r["sector3_ms"]) == (28000, 31000, 31000)


def test_stale_lap_time_is_replaced_by_the_trace_clock():
    records = []
    car = Car(LapRecorder(on_lap_complete=records.append))
    car.lap(1)
    car.lap(2, last_ms=75000)      # the game reports the previous lap's time
    assert records[0]["lap_time_estimated"] is True
    assert abs(records[0]["lap_time_ms"] - 90000) < 500


def test_flashback_within_a_lap_keeps_the_trace_monotonic():
    records = []
    rec = LapRecorder(on_lap_complete=records.append)
    car = Car(rec)
    car.lap(1)
    # lap 2: drive to 2000 m, rewind to 1700 m, drive on
    n = int(sim.TRACK_LEN / 10)
    for i in range(n):
        d = i * 10.0
        if i == 200:
            for j in range(170, 200):
                car.step(2, j * 10.0, int(90000 * j * 10 / sim.TRACK_LEN), 90000)
        car.step(2, d, int(90000 * d / sim.TRACK_LEN), 90000)
    assert rec.cars[0].flashbacks == 1
    car.lap(3, last_ms=90000)
    lap2 = records[1]
    assert lap2["flashbacks"] == 1
    dist = [s[0] for s in lap2["samples"]]
    assert dist == sorted(dist) and len(dist) == len(set(dist))
    assert lap2["lap_number"] == 2


def test_flashback_across_the_line_discards_the_partial_lap():
    records = []
    rec = LapRecorder(on_lap_complete=records.append)
    car = Car(rec)
    car.lap(1)
    for i in range(30):                                   # 300 m into lap 2
        car.step(2, i * 10.0, i * 166, 90000)
    # rewind to 4500 m of lap 1 (the game also winds the counter back)
    for i in range(450, int(sim.TRACK_LEN / 10)):
        car.step(1, i * 10.0, int(90000 * i * 10 / sim.TRACK_LEN), 0)
    car.lap(2, last_ms=90000)
    car.lap(3, last_ms=90000)
    assert [r["lap_number"] for r in records] == [1, 2]
    assert rec.last_finalize["saved"] is True


def test_restart_discards_the_buffer():
    records = []
    rec = LapRecorder(on_lap_complete=records.append)
    car = Car(rec)
    car.lap(1)
    for i in range(200):
        car.step(2, i * 10.0, i * 166, 90000)
    car.lap(1)                                             # session restarted
    car.lap(2, last_ms=90000)
    assert [r["lap_number"] for r in records] == [1, 1]


def test_partial_lap_is_rejected_with_a_reason():
    records = []
    rec = LapRecorder(on_lap_complete=records.append)
    car = Car(rec)
    for i in range(300, int(sim.TRACK_LEN / 10)):          # join at 3000 m
        car.step(1, i * 10.0, i * 166, 0)
    car.lap(2, last_ms=90000)
    assert records == []
    assert rec.last_finalize["saved"] is False
    assert "covered" in rec.last_finalize["reason"]


def test_wrong_packet_format_is_reported_not_parsed():
    import struct
    rec = LapRecorder(on_lap_complete=lambda r: None)
    bad = struct.pack(pk.HEADER_FMT, 2022, 22, 1, 0, 1, pk.PID_SESSION,
                      1, 0.0, 0, 0, 0, 255) + b"\x00" * 600
    rec.feed(bad)
    assert rec.wrong_format_seen == 2022
    assert rec.session is None


def test_second_session_on_the_port_is_ignored_until_the_first_goes_quiet():
    import struct
    rec = LapRecorder(on_lap_complete=lambda r: None)
    rec.feed(sim.session_packet(0.0, 1))
    other = struct.pack(pk.HEADER_FMT, 2023, 23, 1, 0, 1, pk.PID_SESSION,
                        999, 0.0, 0, 0, 0, 255)
    other += sim.session_packet(0.0, 1)[pk.HEADER_SIZE:]
    rec.feed(other)
    assert rec.session_uid == sim.SESSION_UID and rec.ignored_packets == 1
    rec.session_seen_at -= 10.0                            # the first went quiet
    rec.feed(other)
    assert rec.session_uid == 999


def test_live_status_shape(recorded, traces):
    rec = LapRecorder(on_lap_complete=lambda r: None)
    for _d, dg in sim.session_datagrams(*traces, laps=1):
        rec.feed(dg)
    live = rec.live_status()
    assert live["session"]["track"] == "Bahrain"
    assert live["session_uid"] == str(sim.SESSION_UID)
    assert live["car"]["tyre"] == "Soft"
    assert live["last_finalize"]["saved"] is True
    assert live["last_finalize"]["lap_number"] == 1
