"""Shared fixtures: a synthetic two-car session driven straight into the
recorder (no sockets), and a temporary lap store."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# tests must never bind the real telemetry port
os.environ.setdefault("F1_UDP_AUTOSTART", "0")

from backend import settings, store            # noqa: E402
from backend.f123.recorder import LapRecorder  # noqa: E402
from tools import simulate_lap as sim          # noqa: E402


@pytest.fixture
def laps_dir(tmp_path, monkeypatch):
    """Point the lap store at an empty temporary directory."""
    d = tmp_path / "laps"
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "LAPS_DIR", d)
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    store._index.clear()
    yield d
    store._index.clear()


@pytest.fixture(scope="session")
def traces():
    """The simulator's player lap (skill 0.93) and quicker AI lap."""
    return sim.build_lap(), sim.build_lap(skill=0.955)


def drive(recorder: LapRecorder, traces, laps: int = 2):
    """Feed a whole synthetic session into a recorder."""
    lap, ai = traces
    for _delay, datagram in sim.session_datagrams(lap, ai, laps=laps):
        recorder.feed(datagram)


@pytest.fixture
def recorded(traces):
    """Lap records for a two-lap, two-car session, in completion order."""
    records: list = []
    rec = LapRecorder(on_lap_complete=records.append)
    drive(rec, traces, laps=2)
    return records


@pytest.fixture
def stored(recorded, laps_dir):
    """The same session, saved to the temporary store; returns the listing."""
    for r in recorded:
        store.save_lap(dict(r))
    return store.list_laps()
