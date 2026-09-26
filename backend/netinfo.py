"""Which IP address to type into the game's telemetry settings.

The console needs this machine's LAN address. There may be several (Wi-Fi,
Ethernet, VPN, virtual adapters); the one that carries the default route is
almost always the right one, so it is listed first.
"""
from __future__ import annotations

import socket
from typing import Optional


def _is_lan(ip: str) -> bool:
    if ip.startswith(("127.", "169.254.", "0.")) or ":" in ip:
        return False
    return True


def primary_ip() -> Optional[str]:
    """The address of the interface with the default route. Connecting a UDP
    socket sends nothing; it just makes the OS pick a source address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))       # TEST-NET, never routed
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip if _is_lan(ip) else None
    except OSError:
        return None


def lan_ips() -> list:
    """All plausible LAN IPv4 addresses, primary first, no duplicates."""
    found = []
    prim = primary_ip()
    if prim:
        found.append(prim)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_lan(ip) and ip not in found:
                found.append(ip)
    except socket.gaierror:
        pass
    return found


def hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "this PC"
