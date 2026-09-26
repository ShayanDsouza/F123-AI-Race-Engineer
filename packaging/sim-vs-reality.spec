# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: one folder with a `sim-vs-reality` executable that needs
no Python install. Build from the repository root:

    pyinstaller packaging/sim-vs-reality.spec

The result is dist/sim-vs-reality/. Data (laps, cache, settings, key) goes to
the per-user folder — see backend/paths.py — never next to the executable.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

hidden = ["backend.app"]
# uvicorn's workers/loops and fastf1's plugins are imported by name at runtime
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("fastf1")
hidden += collect_submodules("google.genai")

datas = [(os.path.join(ROOT, "frontend", f), "frontend")
         for f in ("index.html", "app.js", "charts.js", "style.css")]
datas += collect_data_files("fastf1")
datas += collect_data_files("timple")
datas += collect_data_files("google.genai")

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython",
              "jupyter", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sim-vs-reality",
    debug=False,
    strip=False,
    upx=False,
    console=True,            # the console shows the IP to type into the game
    icon=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="sim-vs-reality",
)
