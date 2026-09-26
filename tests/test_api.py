"""The REST surface, driven through FastAPI's test client with the synthetic
session saved into a temporary store."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import engineer as eng
from backend import store


@pytest.fixture
def client(laps_dir):
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(eng, "SECRETS_PATH", tmp_path / "secrets.json")
    eng.engineer.forget_client()
    yield


def test_status_without_autostart(client):
    r = client.get("/api/status").json()
    assert r["listener"]["running"] is False
    assert r["live"]["receiving"] is False


def test_listener_start_and_stop(client):
    r = client.post("/api/listener/start", json={"port": 27777})
    assert r.status_code == 200 and r.json() == {"running": True, "port": 27777}
    assert client.get("/api/status").json()["listener"]["port"] == 27777
    assert client.post("/api/listener/stop").json() == {"running": False}


def test_laps_listing_scopes(client, stored):
    all_laps = client.get("/api/laps", params={"scope": "all"}).json()
    mine = client.get("/api/laps").json()
    opp = client.get("/api/laps", params={"scope": "opponent"}).json()
    assert len(all_laps) == 4 and len(mine) == 2 and len(opp) == 2
    assert all(l["is_player"] for l in mine)
    assert mine[0]["suggested_event"] == "Bahrain Grand Prix"
    assert "samples" not in mine[0]
    uid = mine[0]["session_uid"]
    assert len(client.get("/api/laps", params={"scope": "all", "session_uid": uid}).json()) == 4
    assert client.get("/api/laps", params={"scope": "nope"}).status_code == 422


def test_lap_detail_and_delete(client, stored):
    lap_id = stored[0]["id"]
    detail = client.get(f"/api/laps/{lap_id}").json()
    assert detail["id"] == lap_id and len(detail["samples"]) > 100
    assert client.get("/api/laps/does-not-exist").status_code == 404
    assert client.delete(f"/api/laps/{lap_id}").json() == {"deleted": lap_id}
    assert client.get(f"/api/laps/{lap_id}").status_code == 404


def test_compare_ingame(client, stored):
    mine = next(l for l in stored if l["is_player"])
    ai = next(l for l in stored if not l["is_player"])
    r = client.get("/api/compare_ingame", params={"lap_id": mine["id"],
                                                 "ref_lap_id": ai["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["ref"]["driver"] == "VERSTAPPEN"
    assert body["meta"]["ref"]["source"] == "f1-23"
    assert body["map_event"] == "Bahrain Grand Prix"
    assert body["corners"] and body["insights"] and body["strategy"]
    assert body["warnings"] == []                       # same session, same weather
    assert body["strategy"][0]["kind"] == "reference"


def test_compare_ingame_refuses_other_circuits(client, stored, laps_dir):
    mine = next(l for l in stored if l["is_player"])
    other = store.load_lap(next(l for l in stored if not l["is_player"])["id"])
    other.update(track_id=11, track_name="Monza", recorded_at=other["recorded_at"] + 5)
    other_id = store.save_lap(other)
    r = client.get("/api/compare_ingame", params={"lap_id": mine["id"],
                                                 "ref_lap_id": other_id})
    assert r.status_code == 400 and "Track mismatch" in r.json()["detail"]
    r = client.get("/api/compare_ingame", params={"lap_id": mine["id"], "ref_lap_id": "x"})
    assert r.status_code == 404


def test_compare_against_real_data_refuses_short_layouts(client, stored, laps_dir):
    mine = store.load_lap(next(l for l in stored if l["is_player"])["id"])
    mine.update(track_id=21, track_name="Bahrain Short", recorded_at=mine["recorded_at"] + 5)
    short_id = store.save_lap(mine)
    r = client.get("/api/compare", params={"lap_id": short_id, "event": "Bahrain Grand Prix",
                                           "session": "Q", "driver": "VER"})
    assert r.status_code == 400 and "short/alternate layout" in r.json()["detail"]
    r = client.get("/api/compare", params={"lap_id": stored[0]["id"], "event": "Monaco Grand Prix",
                                           "session": "Q", "driver": "VER"})
    assert r.status_code == 400 and "Track mismatch" in r.json()["detail"]


def test_briefing_includes_thermals_and_conditions(client, stored):
    b = client.get("/api/engineer/briefing").json()
    assert b["track"] == "Bahrain" and b["laps_recorded"] == 2
    assert b["thermals"] is not None and b["thermals"]["laps_averaged"] == 2
    assert b["track_temp_c"] == 31 and b["air_temp_c"] == 26
    assert b["by_compound"][0]["compound"] == "Soft"
    assert b["corner_analysis"]["reference"] == "VERSTAPPEN"
    assert b["qualifying"] is None                       # Time Trial, not qualifying
    assert client.get("/api/engineer/briefing", params={"track": "Monza"}).status_code == 404


def test_advice_needs_live_telemetry(client, stored):
    r = client.post("/api/engineer/advice", json={})
    assert r.status_code == 409


def test_engineer_key_lifecycle(client, no_key):
    st = client.get("/api/engineer/status").json()
    assert st["available"] is False and st["key_source"] is None
    assert client.post("/api/engineer/key", json={"key": "nope"}).status_code == 400
    r = client.post("/api/engineer/key", json={"key": "AIza" + "x" * 35})
    assert r.json() == {"available": True, "key_source": "file"}
    assert json.loads(eng.SECRETS_PATH.read_text())["gemini_api_key"].startswith("AIza")
    st = client.get("/api/engineer/status").json()
    assert st["available"] is True and "AIza" not in json.dumps(st)
    assert client.delete("/api/engineer/key").json()["available"] is False


def test_debrief_without_a_key_is_a_clear_503(client, stored, no_key):
    r = client.post("/api/engineer/debrief", json={})
    assert r.status_code == 503 and "Gemini API key" in r.json()["detail"]
    r = client.post("/api/engineer/ask", json={"question": "why?"})
    assert r.status_code == 503


def test_own_recent_laps_filter(stored):
    live = {"session_uid": stored[0]["session_uid"], "session": {"track": "Bahrain"}}
    mine = app_module._own_recent_laps(live)
    assert len(mine) == 2 and all(l["is_player"] for l in mine)
    other = app_module._own_recent_laps({"session_uid": "nope", "session": {"track": "Bahrain"}})
    assert len(other) == 2
    assert app_module._own_recent_laps({"session_uid": "nope", "session": {"track": "Monza"}}) == []


def test_index_page_is_served_uncached(client):
    r = client.get("/")
    assert r.status_code == 200 and "Sim vs Reality" in r.text
    assert r.headers["cache-control"] == "no-cache"
