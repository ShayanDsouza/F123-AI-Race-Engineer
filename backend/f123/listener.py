"""Threaded UDP listener for F1 23 telemetry (default port 20777)."""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional


class UdpListener:
    def __init__(self, on_datagram: Callable[[bytes], None]):
        self._on_datagram = on_datagram
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._running = False
        self.port: Optional[int] = None
        self.error: Optional[str] = None
        self.rcvbuf: int = 0
        self.last_sender: Optional[str] = None   # where the telemetry comes from
        self.datagrams: int = 0

    @property
    def running(self) -> bool:
        return self._running

    def start(self, port: int = 20777) -> bool:
        self.stop()
        self.error = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # At 60 Hz the game sends ~500 KB/s across all packet types. The
            # default 64 KB kernel buffer holds only a fraction of a second, so
            # any stall in this process (a FastF1 session load holding the GIL)
            # would silently drop telemetry. 4 MB buys several seconds.
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
            except OSError:
                pass
            self.rcvbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            sock.bind(("0.0.0.0", port))
            sock.settimeout(1.0)
        except OSError as exc:
            self.error = f"Could not bind UDP port {port}: {exc}"
            return False
        self._sock = sock
        self.port = port
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"udp-listener-{port}")
        self._thread.start()
        return True

    def _run(self):
        while self._running and self._sock is not None:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self.datagrams += 1
            self.last_sender = addr[0]
            try:
                self._on_datagram(data)
            except Exception as exc:  # never let a parse error kill the thread
                self.error = f"packet handling error: {exc}"

    def stop(self):
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
