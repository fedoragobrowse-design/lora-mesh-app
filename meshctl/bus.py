"""Async CDC event bus: one background reader per board, Tk polls a queue.

Single-owner serial stays intact: the bus thread owns each port
(``SerialSession`` lock held) and the GUI never touches pyserial
directly. ``meshctl chat``/``listen`` are separate processes and will
correctly report PORT_BUSY while the app runs — stop the app first.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field


@dataclass
class BusEvent:
    """One item for the GUI poll loop."""

    board: str  # USB serial (stable identity)
    kind: str  # "received" | "reply" | "notice" | "error"
    text: str
    contact_id: int = 0
    epoch: int = 0
    sequence: int = 0


@dataclass
class BoardLink:
    """Owned session state for one board on the bus."""

    serial: str
    device: str
    session: object = None
    next_id: int = 1
    pending: dict = field(default_factory=dict)  # id -> (deadline, queue)
    label: str = ""


class EventBus:
    """Background threads read CDC lines; GUI polls ``poll()`` from Tk."""

    def __init__(self) -> None:
        self.events: queue.Queue[BusEvent] = queue.Queue()
        self._links: dict[str, BoardLink] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def attach(self, serial: str, device: str, label: str = "") -> None:
        """Open + own one port on a reader thread (idempotent per serial)."""
        from . import serial_link

        with self._lock:
            if serial in self._links:
                return
            link = BoardLink(serial=serial, device=device, label=label)
            try:
                link.session = serial_link.SerialSession(device).open()
            except serial_link.PortBusyError:
                self.events.put(BusEvent(serial, "error", "PORT_BUSY: port owned elsewhere"))
                return
            except (RuntimeError, OSError) as exc:
                self.events.put(BusEvent(serial, "error", f"serial error: {exc}"))
                return
            self._links[serial] = link
        thread = threading.Thread(target=self._reader, args=(serial,), daemon=True)
        thread.start()
        self.events.put(BusEvent(serial, "notice", f"attached {device} ({label or serial[:8]})"))

    def detach(self, serial: str) -> None:
        """Release one board (reader thread exits on next timeout)."""
        with self._lock:
            link = self._links.pop(serial, None)
        if link is not None and link.session is not None:
            try:
                link.session.close()
            except OSError:
                pass
            self.events.put(BusEvent(serial, "notice", "detached"))

    def detach_all(self) -> None:
        """Release every board (app shutdown path)."""
        for serial in list(self._links):
            self.detach(serial)

    def request(self, serial: str, op: str, params: dict | None = None,
                timeout: float = 5.0) -> None:
        """Queue one request on the reader thread; reply lands on the bus."""
        with self._lock:
            link = self._links.get(serial)
        if link is None or link.session is None:
            self.events.put(BusEvent(serial, "error", "not attached"))
            return
        cmd_id = link.next_id
        link.next_id = cmd_id + 1 if cmd_id < 2**31 - 1 else 1
        reply_queue: queue.Queue = queue.Queue(maxsize=1)
        link.pending[cmd_id] = (time.monotonic() + timeout, reply_queue)
        try:
            from . import serial_link

            line = serial_link.encode_request(cmd_id, op, **dict(params or {}))
            link.session._ser.write(line)
            link.session._ser.flush()
        except OSError as exc:
            link.pending.pop(cmd_id, None)
            self.events.put(BusEvent(serial, "error", f"write error: {exc}"))

    def poll(self) -> list[BusEvent]:
        """Drain everything queued (call from Tk ``after`` loop)."""
        out: list[BusEvent] = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def _reader(self, serial: str) -> None:
        """Own the port: read lines, route matched replies, post events."""
        import json

        while not self._stop.is_set():
            with self._lock:
                link = self._links.get(serial)
            if link is None:
                return
            try:
                obj = link.session.read_next(timeout=1.0)
            except OSError as exc:
                self.events.put(BusEvent(serial, "error", f"serial error: {exc}"))
                return
            if obj is None:
                self._expire(serial, link)
                continue
            if obj.get("event") == "received":
                try:
                    contact_id = int(obj.get("contact_id", 0))
                except (TypeError, ValueError):
                    contact_id = 0
                try:
                    epoch = int(obj.get("epoch", 0))
                except (TypeError, ValueError):
                    epoch = 0
                try:
                    sequence = int(obj.get("sequence", 0))
                except (TypeError, ValueError):
                    sequence = 0
                self.events.put(BusEvent(
                    serial, "received", str(obj.get("text", "")),
                    contact_id=contact_id, epoch=epoch, sequence=sequence,
                ))
                continue
            if type(obj.get("id")) is int and obj.get("id") in link.pending:
                _, reply_queue = link.pending.pop(obj["id"])
                try:
                    reply_queue.put_nowait(obj)
                except queue.Full:
                    pass
                self.events.put(BusEvent(
                    serial, "reply", json.dumps(obj, ensure_ascii=False)))
                continue
            if "_noise" in obj:
                self.events.put(BusEvent(serial, "notice", f"noise: {obj['_noise'][:80]}"))
            elif "event" in obj:
                self.events.put(BusEvent(serial, "notice", json.dumps(obj)[:160]))

    def _expire(self, serial: str, link: BoardLink) -> None:
        """Time out stale pending requests on quiet polls."""
        now = time.monotonic()
        for cmd_id, (deadline, reply_queue) in list(link.pending.items()):
            if now >= deadline:
                link.pending.pop(cmd_id, None)
                try:
                    reply_queue.put_nowait({"ok": False, "error": "TIMEOUT"})
                except queue.Full:
                    pass
                self.events.put(BusEvent(serial, "error", f"no reply (id {cmd_id})"))
