# meshctl + meshchat + TUI — USB host tools for the LoRa mesh

Python terminal interface for the three-node Pico LoRa mesh. The host holds
**no keys**: it transports opaque `LMESH1:` pairing records between firmware
and QR PNG files, and types/reads plaintext UI text. Key agreement and
message encryption live in the
[`lora-mesh-radio`](https://github.com/fedoragobrowse-design/lora-mesh-radio)
firmware.

## Install

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m meshctl ports
```

## Use

```sh
.venv/bin/python -m meshctl --port "$A" status
.venv/bin/python -m meshctl --port "$A" send --contact alice --text "hello"
.venv/bin/python -m meshctl --port "$B" chat --contact A
.venv/bin/python -m meshctl tui   # all boards, Tab to cycle
meshchat                          # Tk three-pane control surface
```

Boards need the `dialout` group: `sg dialout -c '.venv/bin/python -m meshctl tui'`.

## Layout

- `meshctl/__main__.py` — argparse CLI, 1:1 onto firmware USB ops.
- `meshctl/serial_link.py` — owned-port framing: locks, monotonic ids,
  interleaved-event matching, bounded deadlines.
- `meshctl/pairing.py` — strict canonical `LMESH1:` validation + QR PNG
  encode/decode (`zxing-cpp`, Pillow, NumPy only).
- `meshctl/contacts.py` — local display-name map, keyed by USB serial
  (written only on `pair_import` activation; firmware slots authoritative).
- `meshctl/boards.py`, `bus.py` — serial-stable discovery + background bus.
- `meshctl/tui.py` — curses station console (stdlib only).
- `meshctl/app.py` — Tk control surface (`meshchat`).
- `meshctl/history.py`, `local.py` — per-board SQLite history + private paths.
- `meshctl/cli_extra.py`, `device.py` — boards/flash/elf/uf2/reboot/debug.
- `tests/` — host unit tests.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
```

See the [wiki](../../wiki) for the full operator guide.
