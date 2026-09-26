"""Packet-format-2023 parsing, round-tripped through the simulator's packers."""
import struct

from backend.f123 import packets as pk
from tools import simulate_lap as sim


def test_struct_sizes_match_the_spec():
    assert pk.HEADER_SIZE == 29
    assert pk.LAP_DATA_SIZE == 50
    assert pk.CAR_TELEMETRY_SIZE == 60
    assert pk.CAR_STATUS_SIZE == 55
    assert pk.CAR_DAMAGE_SIZE == 42
    assert pk.PARTICIPANT_SIZE == 58
    assert pk.LAP_HISTORY_SIZE == 14


def test_header_roundtrip():
    h = pk.parse_header(sim.header(pk.PID_SESSION, 12.5, 42))
    assert h.packet_format == 2023
    assert h.packet_id == pk.PID_SESSION
    assert h.session_uid == sim.SESSION_UID
    assert h.frame_identifier == 42
    assert h.player_car_index == 0
    assert pk.parse_header(b"\x00" * 10) is None


def test_session_packet():
    s = pk.parse_session(sim.session_packet(1.0, 1))
    assert s.track_id == sim.TRACK_ID and s.track_name == "Bahrain"
    assert s.track_length == int(sim.TRACK_LEN)
    assert s.session_name == "Time Trial"
    assert s.track_temperature == 31 and s.air_temperature == 26


def test_lap_data_per_car():
    cars = [(3, 1234.5, 61000, 90500, 12000.0, 1, 65500, 0),
            (2, 400.0, 20000, 91000, 5800.0, 7)]
    data = sim.lap_data_packet(1.0, 1, cars)
    a = pk.parse_lap_data(data, 0)
    b = pk.parse_lap_data(data, 1)
    assert (a.current_lap_num, a.lap_distance, a.current_lap_time_ms,
            a.last_lap_time_ms, a.car_position) == (3, 1234.5, 61000, 90500, 1)
    assert (a.sector1_time_ms, a.sector1_minutes, a.sector2_time_ms) == (5500, 1, 0)
    assert (b.current_lap_num, b.car_position) == (2, 7)
    assert a.driver_status == 1 and a.result_status == 2
    assert pk.parse_lap_data(data, pk.NUM_CARS) is None   # beyond the array


def test_car_telemetry_channels():
    data = sim.telemetry_packet(1.0, 1, [(287, 0.75, 0.0, 7, 11200, 1)])
    t = pk.parse_car_telemetry(data, 0)
    assert t.speed == 287 and t.gear == 7 and t.drs == 1
    assert abs(t.throttle - 0.75) < 1e-6 and t.brake == 0.0
    assert t.brakes_temperature == (400, 400, 400, 400)
    assert t.tyres_surface_temperature == (90, 90, 90, 90)
    assert t.tyres_pressure == (23.0, 23.0, 21.5, 21.5)


def test_car_status_and_damage():
    st = pk.parse_car_status(sim.status_packet(1.0, 1, 33.3, 2.2e6), 0)
    assert abs(st.fuel_in_tank - 33.3) < 1e-4
    assert st.tyre_name == "Soft" and st.tyres_age_laps == 3
    assert abs(st.ers_deployed_this_lap - 2.2e6) < 1
    dmg = pk.parse_car_damage(sim.damage_packet(1.0, 1, 10.0), 1)
    assert dmg.tyres_wear == (10.0, 10.0, 11.5, 11.5)


def test_participants_names_and_teams():
    parts = pk.parse_participants(sim.participants_packet(1.0, 1))
    assert [p.name for p in parts] == ["PLAYER", "VERSTAPPEN"]
    assert parts[1].ai_controlled and not parts[0].ai_controlled
    assert pk.TEAM_NAMES[parts[1].team_id] == "Red Bull Racing"
    assert parts[1].race_number == 1


def test_session_history_sectors_add_up():
    laps = [(91234, 30100, 30200), (90876, 29900, 30300)]
    hist = pk.parse_session_history(sim.history_packet(1.0, 1, laps))
    assert hist.car_index == 0 and hist.num_laps == 2
    for entry, (ms, s1, s2) in zip(hist.laps, laps):
        assert entry.lap_time_ms == ms
        assert (entry.sector1_ms, entry.sector2_ms) == (s1, s2)
        assert entry.sector1_ms + entry.sector2_ms + entry.sector3_ms == ms
        assert entry.valid


def test_minutes_field_extends_sector_times():
    # a 1:05.500 sector 1 is sent as 5500 ms + 1 minute
    fixed = struct.pack(pk.SESSION_HISTORY_FIXED_FMT, 0, 1, 0, 0, 0, 0, 0)
    lap = struct.pack(pk.LAP_HISTORY_FMT, 200000, 5500, 1, 30000, 0, 44500, 1, 1)
    hist = pk.parse_session_history(sim.header(pk.PID_SESSION_HISTORY, 0, 0) + fixed + lap)
    assert hist.laps[0].sector1_ms == 65500
    assert hist.laps[0].sector3_ms == 104500


def test_track_tables_are_consistent():
    for tid in pk.TRACK_TO_2023_EVENT:
        assert tid in pk.TRACK_NAMES
    for tid in pk.SHORT_LAYOUT_IDS:
        assert "Short" in pk.TRACK_NAMES[tid]
    assert pk.is_wet("Heavy rain") and not pk.is_wet("Clear")
