"""Terminal TUI for the mesh: board roster, contacts, live traffic, send box.

Radio-shack logbook, not a chat app: left column is the station roster
(boards by USB serial with label/image), center is the traffic log
(inbound moss green, outbound amber, faults red), bottom is the key line.
Boards attach on start and stay owned until quit; `/quit` detaches all.

Keys: Tab cycles boards, F2 cycles contacts, `/to NAME` targets,
`/block NAME` + `/unblock NAME` + `/delete NAME` manage slots,
`/radio on|off`, `/status`, `/contacts`, `/quit`.
Plain typing sends to the current target.
"""
from __future__ import annotations

import argparse
import curses
import time

from . import boards as _boards
from . import bus as _bus
from . import contacts as _contacts
from . import history as _history
from . import local as _local

PALETTE = {
    "desk": 232,      # near-black bench top
    "panel": 235,     # panel felt
    "brass": 178,     # brass dial markings
    "amber": 214,     # outbound / active target
    "moss": 107,      # inbound traffic
    "fault": 167,     # faults red
    "paper": 230,     # logbook paper
    "dim": 243,       # quiet annotations
}

HELP_LINES = [
    "Tab boards · F2 contacts · type to send · /to NAME · /block /unblock /delete NAME",
    "/radio on|off · /status · /contacts · /quit",
]


class Tui:
    """Curses state: roster, target, log, input. Bus pumps events in."""
    def __init__(self, stdscr: object, known: dict[str, object]) -> None:
        self.stdscr = stdscr
        self.bus = _bus.EventBus()
        self.known: dict[str, _boards.Board] = dict(known)
        self.order: list[str] = []
        self.current = 0
        self.targets: dict[str, str] = {}
        self.contact_idx: dict[str, int] = {}
        self.log: list[tuple[str, str]] = []
        self.input = ""
        self.status = "scanning…"
        for serial, board in self.known.items():
            assert isinstance(board, _boards.Board)
            self.bus.attach(board.serial, board.device, board.label)
            self.order.append(serial)
        if not self.order:
            self.status = "no boards attached (check USB)"

    def board(self) -> _boards.Board | None:
        """Current board or None."""
        if not self.order:
            return None
        return self.known.get(self.order[self.current % len(self.order)])

    def names(self, board: _boards.Board) -> list[str]:
        """Mapped names for this board (serial-keyed, arbitrary labels)."""
        return _contacts.names_for(board.serial)

    def target(self, board: _boards.Board) -> str:
        """Current send target name for this board."""
        saved = self.targets.get(board.serial, "")
        if saved and _contacts.valid_name(saved):
            return saved
        names = self.names(board)
        idx = self.contact_idx.get(board.serial, 0) % max(len(names), 1)
        return names[idx] if names else ""

    def say(self, text: str, style: str = "paper") -> None:
        """Append one log line, capped."""
        self.log.append((style, text[:300]))
        del self.log[:-500]

    def pump(self) -> None:
        """Drain the bus into the log with contact names resolved."""
        for event in self.bus.poll():
            board = self.known.get(event.board)
            tag = (board.label if board else event.board[:8]) or event.board[:8]
            if event.kind == "received":
                name = None
                if board is not None:
                    try:
                        name = _contacts.name_for_id(board.serial, event.contact_id)
                    except (ValueError, OSError):
                        name = None
                who = name or f"id{event.contact_id}"
                self.say(f"[{tag} ← {who}] {event.text}", "moss")
                if board is not None:
                    try:
                        conn = _history.open_history(_local.history_path_for(board.device))
                        try:
                            _history.record_message(conn, contact=who, direction="in",
                                                    epoch=event.epoch, sequence=event.sequence,
                                                    text=event.text)
                        finally:
                            conn.close()
                    except (ValueError, TypeError, OSError):
                        pass
            elif event.kind == "reply":
                self.say(f"[{tag}] {event.text[:160]}", "dim")
            elif event.kind == "error":
                self.say(f"[{tag}] {event.text[:160]}", "fault")
            else:
                self.say(f"[{tag}] {event.text[:160]}", "dim")

    def send_current(self) -> None:
        """Send the input line to the current target."""
        text = self.input
        self.input = ""
        if not text.strip():
            return
        board = self.board()
        if board is None:
            self.say("no board selected", "fault")
            return
        name = self.target(board)
        if not name:
            self.say("no target: /to NAME or pair-import first", "fault")
            return
        cid = _contacts.resolve_contact(board.serial, name)
        if cid is None:
            self.say(f"unknown contact '{name}'", "fault")
            return
        self.bus.request(board.serial, "send",
                         {"contact_id": cid, "text": text}, timeout=60.0)
        self.say(f"[{board.label or '?'} → {name}] {text}", "amber")
        try:
            conn = _history.open_history(_local.history_path_for(board.device))
            try:
                _history.record_message(conn, contact=name, direction="out",
                                        epoch=0, sequence=0, text=text)
            finally:
                conn.close()
        except (ValueError, TypeError, OSError):
            pass

    def command(self, line: str) -> bool:
        """Run one `/` command. Returns False to quit."""
        cmd, _, rest = line[1:].partition(" ")
        board = self.board()
        if cmd == "quit":
            return False
        if cmd == "to":
            if board is None or not rest.strip():
                self.say("usage: /to NAME", "fault")
            elif not _contacts.valid_name(rest.strip()):
                self.say("name must be 1-32 non-blank chars", "fault")
            else:
                self.targets[board.serial] = rest.strip()
                self.say(f"target: {rest.strip()}", "amber")
        elif cmd in ("block", "unblock", "delete"):
            if board is None or not rest.strip():
                self.say(f"usage: /{cmd} NAME", "fault")
            else:
                cid = _contacts.resolve_contact(board.serial, rest.strip())
                if cid is None:
                    self.say(f"unknown contact '{rest.strip()}'", "fault")
                else:
                    op = "contact_delete" if cmd == "delete" else cmd
                    self.bus.request(board.serial, op, {"contact_id": cid}, timeout=10.0)
                    self.say(f"{cmd}: {rest.strip()} (id {cid})", "amber")
        elif cmd == "radio" and rest.strip() in ("on", "off"):
            if board is not None:
                self.bus.request(board.serial, "radio_set",
                                 {"enabled": rest.strip() == "on"}, timeout=10.0)
        elif cmd in ("status", "contacts"):
            if board is not None:
                self.bus.request(board.serial, cmd, None, timeout=10.0)
        else:
            self.say(f"unknown command /{cmd}", "fault")
        return True

    def draw(self) -> None:
        """Paint roster, log, input. Plain curses, no flicker tricks."""
        stdscr = self.stdscr
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        # Roster column (left, 24 wide).
        for i, serial in enumerate(self.order[: h - 4]):
            board = self.known.get(serial)
            label = (board.label if board else serial[:8]) or serial[:8]
            mark = "●" if i == self.current % len(self.order) else "○"
            line = f"{mark} {label}"[:23]
            try:
                stdscr.addstr(1 + i, 1, line,
                              curses.color_pair(3) if i == self.current % len(self.order)
                              else curses.color_pair(6))
            except curses.error:
                pass
        # Traffic log (center).
        board = self.board()
        target = self.target(board) if board is not None else ""
        header = f"{board.label if board else '—'} → {target or 'no target'} · {self.status}"
        try:
            stdscr.addstr(0, 26, header[: w - 27], curses.color_pair(2))
            stdscr.vline(0, 24, curses.ACS_VLINE, h - 2)
            stdscr.hline(h - 3, 25, curses.ACS_HLINE, w - 26)
        except curses.error:
            pass
        visible = self.log[-(h - 5):]
        styles = {"amber": 3, "moss": 4, "fault": 5, "paper": 1, "dim": 6}
        for i, (style, text) in enumerate(visible):
            try:
                stdscr.addstr(1 + i, 26, text[: w - 27], curses.color_pair(styles.get(style, 1)))
            except curses.error:
                pass
        # Input + help.
        try:
            stdscr.addstr(h - 2, 26, ("> " + self.input)[-(w - 27):], curses.color_pair(1))
            stdscr.addstr(h - 1, 1, HELP_LINES[0][: w - 2], curses.color_pair(6))
        except curses.error:
            pass
        stdscr.move(h - 2, min(28 + len(self.input), w - 1))
        stdscr.refresh()


def _scan() -> dict[str, _boards.Board]:
    """Probe attached boards into serial-keyed map."""
    try:
        found = _boards.probe_all()
    except OSError:
        return {}
    return {b.serial: b for b in found}


def _run(stdscr: object, known: dict) -> int:
    curses.start_color()
    curses.use_default_colors()
    for n, name in ((1, "paper"), (2, "brass"), (3, "amber"),
                    (4, "moss"), (5, "fault"), (6, "dim")):
        fg = {"paper": PALETTE["paper"], "brass": PALETTE["brass"],
              "amber": PALETTE["amber"], "moss": PALETTE["moss"],
              "fault": PALETTE["fault"], "dim": PALETTE["dim"]}[name]
        try:
            curses.init_pair(n, fg, PALETTE["desk"])
        except curses.error:
            pass
    try:
        stdscr.bkgd(" ", curses.color_pair(1))
    except curses.error:
        pass
    tui = Tui(stdscr, known)
    # `known` already holds the pre-entry scan; status counts stations.
    tui.status = f"{len(tui.order)} station(s)" if tui.order else "no boards"
    stdscr.nodelay(True)
    stdscr.keypad(True)
    running = True
    while running:
        tui.pump()
        tui.draw()
        try:
            key = stdscr.getch()
        except curses.error:
            key = -1
        if key == -1:
            time.sleep(0.05)
            continue
        if key in (curses.KEY_F2,):
            board = tui.board()
            if board is not None:
                names = tui.names(board)
                if names:
                    idx = (tui.contact_idx.get(board.serial, 0) + 1) % len(names)
                    tui.contact_idx[board.serial] = idx
                    tui.targets[board.serial] = names[idx]
        elif key == 9:  # Tab
            if tui.order:
                tui.current = (tui.current + 1) % len(tui.order)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            tui.input = tui.input[:-1]
        elif key in (curses.KEY_ENTER, 10, 13):
            line = tui.input
            tui.input = ""
            if line.startswith("/"):
                running = tui.command(line)
            else:
                tui.input = line
                tui.send_current()
        elif 32 <= key <= 126:
            if len(tui.input) < 160:
                tui.input += chr(key)
    tui.bus.detach_all()
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    """Entry: scan boards, enter curses, own ports until quit."""
    known = _scan()
    if not known:
        print("meshctl: no boards found (check USB)", flush=True)
        return 1
    import functools

    try:
        return curses.wrapper(functools.partial(_run, known=known))
    except KeyboardInterrupt:
        # Ctrl-C is a normal exit from the console, not a crash.
        return 0
