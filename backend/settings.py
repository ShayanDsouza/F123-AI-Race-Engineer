"""Small persisted settings file (data/settings.json): the UDP port and
anything else the user changes in the sidebar that should survive a restart."""
from __future__ import annotations

import json
import threading
from typing import Any

from .paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"
DEFAULTS = {"udp_port": 20777}
_lock = threading.Lock()


def load() -> dict:
    out = dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return out


def save(**changes: Any) -> dict:
    with _lock:
        current = load()
        current.update({k: v for k, v in changes.items() if k in DEFAULTS})
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current
