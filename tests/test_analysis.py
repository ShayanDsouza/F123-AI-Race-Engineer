"""Distance alignment, delta curve and corner detection on synthetic laps."""
import numpy as np
import pytest

from backend import analysis, coach
from tools import simulate_lap as sim


def _records(recorded):
    you = next(r for r in recorded if r["is_player"])
    ai = next(r for r in recorded if not r["is_player"])
    return you, ai


def test_compare_pins_the_delta_to_the_true_gap(recorded):
    you, ai = _records(recorded)
    cmp = analysis.compare(you, ai)
    true_gap = (you["lap_time_ms"] - ai["lap_time_ms"]) / 1000
    assert abs(cmp["delta"][-1] - true_gap) < 0.002
    assert cmp["delta"][0] == 0.0
    assert len(cmp["grid"]) == analysis.GRID_N == len(cmp["you"]["speed"])
    st = cmp["stats"]
    assert st["lap_time_ms_you"] == you["lap_time_ms"]
    assert st["ref_lap_len_m"] == int(sim.TRACK_LEN)
    assert st["top_speed_ref"] >= st["top_speed_you"]


def test_corners_land_near_the_simulated_apexes(recorded):
    you, ai = _records(recorded)
    cmp = analysis.compare(you, ai)
    found = [c["apex_m"] for c in cmp["corners"]]
    assert 8 <= len(found) <= 14
    # every slow corner in the simulator's list should have a detection within 60 m
    for pos, apex in sim.CORNERS:
        if apex < 150:
            assert min(abs(f - pos) for f in found) < 60, (pos, found)
    for c in cmp["corners"]:
        assert c["you"]["min_speed"] >= 0 and c["ref"]["apex_gear"] >= 1
        assert c["seg"][0] < c["seg"][1]


def test_sector_splits_are_exact_when_both_laps_have_sectors(recorded):
    you, ai = _records(recorded)
    cmp = analysis.compare(you, ai)
    assert all(s["exact"] for s in cmp["sectors"])
    total = sum(s["delta_ms"] for s in cmp["sectors"])
    assert total == you["lap_time_ms"] - ai["lap_time_ms"]


def test_sector_splits_fall_back_to_distance(recorded):
    you, ai = _records(recorded)
    ai = dict(ai, sector1_ms=None, sector2_ms=None, sector3_ms=None)
    cmp = analysis.compare(you, ai)
    assert not any(s["exact"] for s in cmp["sectors"])
    assert [s["from_frac"] for s in cmp["sectors"]] == [0.0, 0.3333, 0.6667]


def test_a_lap_against_itself_is_flat(recorded):
    you, _ = _records(recorded)
    cmp = analysis.compare(you, you)
    assert max(abs(v) for v in cmp["delta"]) < 0.01
    assert all(abs(c["time_delta_s"]) < 0.01 for c in cmp["corners"])


def test_short_trace_is_rejected():
    with pytest.raises(ValueError):
        analysis.to_arrays({"columns": ["dist", "time_ms", "speed", "throttle",
                                        "brake", "gear"], "samples": [[0, 0, 0, 0, 0, 1]] * 5})


def test_unsorted_and_duplicate_samples_are_cleaned():
    rng = np.random.default_rng(0)
    d = np.linspace(0, 1000, 200)
    samples = [[float(x), float(x * 20), 200.0, 1.0, 0.0, 6] for x in d]
    samples += samples[50:60]                      # duplicated stretch
    rng.shuffle(samples)
    arr = analysis.to_arrays({"columns": ["dist", "time_ms", "speed", "throttle",
                                          "brake", "gear"], "samples": samples,
                              "track_length": 1000})
    assert np.all(np.diff(arr["dist"]) > 0)
    assert len(arr["dist"]) == 200


def test_coaching_notes_rank_the_biggest_loss_first(recorded):
    you, ai = _records(recorded)
    cmp = analysis.compare(you, ai)
    notes = coach.driving_insights(cmp, "VERSTAPPEN")
    assert notes[0]["kind"] == "summary" and "off VERSTAPPEN" in notes[0]["text"]
    corner_notes = [n for n in notes if n["kind"] == "corner"]
    assert corner_notes and "biggest loss" in corner_notes[0]["text"]
    for n in notes:
        assert n["severity"] in (0, 1, 2, 3)


def test_condition_warnings_flag_a_wet_reference():
    ref = {"driver": "VER", "weather": {"wet": True, "compound": "Intermediate"}}
    warn = coach.condition_warnings({"weather": "Clear"}, ref)
    assert warn and warn[0]["severity"] == 3 and "Intermediate" in warn[0]["text"]
    assert coach.condition_warnings({"weather": "Clear"},
                                    {"weather": {"wet": False}}) == []
    both_dry_hot = coach.condition_warnings(
        {"weather": "Clear", "track_temp": 50},
        {"weather": {"wet": False, "track_temp": 30}})
    assert both_dry_hot and "Track temperature" in both_dry_hot[0]["text"]
