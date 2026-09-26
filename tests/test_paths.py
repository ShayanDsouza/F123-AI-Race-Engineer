"""Data-directory resolution and LAN address discovery."""
import sys
from pathlib import Path

from backend import netinfo, paths


def test_source_checkout_uses_repo_data_dir():
    assert paths.resolve_data_dir() == Path(__file__).resolve().parent.parent / "data"


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMVSREALITY_DATA", str(tmp_path / "custom"))
    assert paths.resolve_data_dir() == tmp_path / "custom"


def test_frozen_build_uses_a_per_user_folder(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("SIMVSREALITY_DATA", raising=False)
    d = paths.resolve_data_dir()
    assert "site-packages" not in str(d)
    assert d.is_absolute() and "SimVsReality" in str(d) or "sim-vs-reality" in str(d)


def test_lan_ip_filtering():
    assert not netinfo._is_lan("127.0.0.1")
    assert not netinfo._is_lan("169.254.10.3")
    assert not netinfo._is_lan("fe80::1")
    assert netinfo._is_lan("192.168.1.20")
    ips = netinfo.lan_ips()
    assert all(netinfo._is_lan(ip) for ip in ips)
    assert len(ips) == len(set(ips))
