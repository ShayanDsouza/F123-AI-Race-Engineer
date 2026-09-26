"""Where the app keeps its data (recorded laps, FastF1 cache, settings, the
Gemini key).

Running from a source checkout keeps everything in `<repo>/data/` as before.
An installed copy (pip/pipx, or a packaged executable) must not write into
site-packages or next to the binary, so it uses a per-user folder instead.
`SIMVSREALITY_DATA` overrides both.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "sim-vs-reality"


def _source_root() -> Path | None:
    """The repository root when running from a checkout, else None."""
    root = Path(__file__).resolve().parent.parent
    if (root / "run.bat").exists() or (root / "pyproject.toml").exists():
        if not any(p in root.parts for p in ("site-packages", "dist-packages")):
            return root
    return None


def _user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") \
            or str(Path.home() / "AppData" / "Local")
        return Path(base) / "SimVsReality"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SimVsReality"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def resolve_data_dir() -> Path:
    override = os.environ.get("SIMVSREALITY_DATA")
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):          # PyInstaller bundle
        return _user_data_dir()
    src = _source_root()
    return src / "data" if src else _user_data_dir()


def frontend_dir() -> Path:
    """The dashboard files, wherever this copy is installed."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "frontend"
    return Path(__file__).resolve().parent.parent / "frontend"


DATA_DIR = resolve_data_dir()
