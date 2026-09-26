"""Command-line entry point: `sim-vs-reality` (or `python -m backend`).

Starts the web app, prints the address to type into the game, and opens the
dashboard in the default browser.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="sim-vs-reality",
        description="F1 23 telemetry coach: your laps vs real 2023 F1 and the in-game AI.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SIMVSREALITY_WEB_PORT", 8000)),
                    help="web dashboard port (default 8000)")
    ap.add_argument("--udp-port", type=int, default=None,
                    help="UDP telemetry port to listen on (default: saved setting, or 20777)")
    ap.add_argument("--host", default="0.0.0.0",
                    help="web bind address (default 0.0.0.0 so a phone can open the race HUD)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the dashboard")
    ap.add_argument("--data", default=None, help="data folder (laps, cache, settings)")
    args = ap.parse_args(argv)

    if args.data:
        os.environ["SIMVSREALITY_DATA"] = args.data
    if args.udp_port:
        os.environ["F1_UDP_PORT"] = str(args.udp_port)
    os.environ["SIMVSREALITY_WEB_PORT"] = str(args.port)

    import uvicorn
    from . import netinfo, paths, settings

    udp = args.udp_port or settings.load()["udp_port"]
    ips = netinfo.lan_ips()
    print("Sim vs Reality")
    print(f"  dashboard : http://localhost:{args.port}")
    for ip in ips:
        print(f"  on phone  : http://{ip}:{args.port}")
    print(f"  game      : UDP Telemetry ON, port {udp}, format 2023, "
          f"IP {ips[0] if ips else 'this PC'} (or Broadcast Mode ON)")
    print(f"  data      : {paths.DATA_DIR}")

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    try:
        uvicorn.run("backend.app:app", host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
