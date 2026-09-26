"""Lap files, the listing index and id hygiene."""
import json
import time

from backend import store


def _record(**over):
    base = {"track_name": "Bahrain", "session_type": "Q1", "is_player": True,
            "lap_number": 3, "lap_time_ms": 91000, "recorded_at": time.time(),
            "track_temp": 31, "thermal": {"n": 1},
            "columns": ["dist"], "samples": [[0.0], [1.0]]}
    base.update(over)
    return base


def test_save_list_load_delete(laps_dir):
    lap_id = store.save_lap(_record())
    assert lap_id.endswith("-bahrain-q1-you-lap3-91000ms")
    listing = store.list_laps()
    assert len(listing) == 1
    entry = listing[0]
    assert entry["id"] == lap_id and entry["is_player"] is True
    assert entry["has_thermal"] is True and entry["track_temp"] == 31
    assert entry["sample_count"] == 2 and "samples" not in entry
    full = store.load_lap(lap_id)
    assert full["samples"] == [[0.0], [1.0]]
    assert store.delete_lap(lap_id) is True
    assert store.list_laps() == [] and store.load_lap(lap_id) is None
    assert store.delete_lap(lap_id) is False


def test_same_second_collisions_get_distinct_ids(laps_dir):
    a = store.save_lap(_record())
    b = store.save_lap(_record())
    assert a != b and b.endswith("-2")
    assert len(store.list_laps()) == 2


def test_listing_is_cached_until_the_file_changes(laps_dir, monkeypatch):
    lap_id = store.save_lap(_record())
    store.list_laps()
    reads = []
    real = store.summarize
    monkeypatch.setattr(store, "summarize", lambda r: reads.append(1) or real(r))
    store.list_laps()
    assert reads == []                                   # served from the index
    path = laps_dir / f"{lap_id}.json"
    rec = json.loads(path.read_text())
    rec["lap_time_ms"] = 90000
    rec["samples"] = rec["samples"] * 3                  # size changes too
    path.write_text(json.dumps(rec))
    assert store.list_laps()[0]["lap_time_ms"] == 90000
    assert reads == [1]


def test_index_drops_files_removed_behind_its_back(laps_dir):
    lap_id = store.save_lap(_record())
    store.list_laps()
    (laps_dir / f"{lap_id}.json").unlink()
    assert store.list_laps() == []
    assert lap_id + ".json" not in store._index


def test_listing_survives_a_corrupt_file(laps_dir):
    store.save_lap(_record())
    (laps_dir / "zzz-broken.json").write_text("{not json")
    assert len(store.list_laps()) == 1


def test_path_tricks_are_refused(laps_dir):
    assert store.load_lap("../secrets") is None
    assert store.load_lap("a/b") is None
    assert store.delete_lap("..\\x") is False
    assert store.load_lap("") is None


def test_legacy_records_without_player_flag_count_as_player(laps_dir):
    rec = _record()
    del rec["is_player"]
    store.save_lap(rec)
    assert store.list_laps()[0]["is_player"] is True
