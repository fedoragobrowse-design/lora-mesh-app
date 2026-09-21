# meshctl host package

USB terminal interface for the three-node Pico LoRa mesh. The host holds
**no keys**: it transports opaque `LMESH1:` pairing records between
firmware and QR PNG files, and types/reads plaintext UI text. Key
agreement and message encryption belong in Rust, not this Python package.

## Install (editable)

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ./host
.venv/bin/python -m meshctl ports
```

`meshctl ports` needs no `--port`. Every other command takes an explicit
serial path (`--port PORT`, one process per port):

```sh
.venv/bin/python -m meshctl --port "$A" status
.venv/bin/python -m meshctl --port "$A" provision --label A
.venv/bin/python -m meshctl --port "$A" time set --utc now
.venv/bin/python -m meshctl --port "$A" time status
.venv/bin/python -m meshctl --port "$A" radio on
.venv/bin/python -m meshctl --port "$A" radio off
.venv/bin/python -m meshctl --port "$A" radio arm-next-boot
.venv/bin/python -m meshctl --port "$A" ping --count 1
.venv/bin/python -m meshctl --port "$A" send --contact B --text "hello"
.venv/bin/python -m meshctl --port "$B" listen
.venv/bin/python -m meshctl --port "$B" chat --contact A
.venv/bin/python -m meshctl --port "$A" contacts
.venv/bin/python -m meshctl --port "$A" block NAME
.venv/bin/python -m meshctl --port "$A" unblock NAME
.venv/bin/python -m meshctl --port "$A" contact-delete NAME
.venv/bin/python -m meshctl --port "$A" pair offer --out offer-A.png
.venv/bin/python -m meshctl --port "$A" pair proof --out proof-A.png
.venv/bin/python -m meshctl --port "$A" pair confirm --out confirm-A.png
.venv/bin/python -m meshctl --port "$B" pair import --file offer-A.png --name Alice
```
## Station console (`meshctl tui`)

Terminal TUI for all boards at once (curses, stdlib only). Left column is
the station roster (USB serial + label), center is the traffic log
(inbound green, outbound amber, faults red), bottom is the key line.
Boards attach on start and stay owned until `/quit`.

```sh
sg dialout -c '.venv/bin/python -m meshctl tui'
```

Keys: `Tab` cycles boards, `F2` cycles contacts, plain typing sends to the
current target. `/to NAME` targets, `/block` + `/unblock` + `/delete NAME`
manage slots, `/radio on|off`, `/status`, `/contacts`, `/quit`.
Contact names are arbitrary operator labels (1–32 chars, never on-air
identity); mappings live in `.mesh-local/host-contacts.json` keyed by USB
serial. Needs a real terminal (fails under pipes); `dialout` group required
like every other live-USB command.

## Contacts: 16 slots (V3 code, V2 boards live)

Firmware source holds up to 16 paired contacts (`contact_id` 1–16, persist
V3, 3469 B record fits one 4 KiB flash page). V1/V2 2-slot records migrate
on load via canonical re-encode (empty replay windows are `e=1`, never
zero-padded); RAM-level migration is proven by
`v2_record_migrates_to_v3_and_pair_survives` (7/7 mesh-node tests pass).
Live boards still run V2 2-slot firmware (A slot 1 present, slot 2 empty;
regression PASS 2026-09-21) — no V3 image has been flashed yet, so on-board
V2→V3 migration is unverified. `contact-delete NAME` frees a slot on the
board and drops the local mapping; a full store pairs nothing new until a
slot is deleted. Worst-case RX tries 16 contacts × 3 epoch windows; the
`contacts` reply lists all 16 slots (~4 KiB USB buffer).

 ## Control app (`meshchat`)
```

- Boards pane: `Scan` probes every CDC node with one matched-ID status;
  `Attach`/`Detach` own ports on a background event bus (chat shows
  interleaved traffic live; `meshctl chat`/`listen` correctly report
  `PORT_BUSY` while the app holds a port).
- Chat pane: pick a board + contact target, send 1–160 byte texts, see
  `ACKNOWLEDGED`/`received` inline; history recorded per port.
- Debug pane: `Status`, `Contacts`, `Time sync`, `Radio on/off` (on asks
  first), `Ping`, pair offer/proof/confirm export + import (file dialogs,
  fingerprints shown), contact `Block`/`Unblock`/`Delete` (delete asks
  first, frees the slot on board + mapping), `Check ELF`, `UF2
  convert`, `Flash UF2` (exactly one BOOTSEL volume), `Reboot app` /
  `Reboot BOOTSEL` (live picotool; PERMISSION reported, never sudo).
- Boards and the serial ports need the `dialout` group
  (`sg dialout -c '.venv/bin/meshchat'`); without it, scan lists serials
  with permission errors instead of misidentifying boards.

## Extended CLI (sudo-free)

```sh
.venv/bin/python -m meshctl boards            # discover + probe by USB serial
.venv/bin/python -m meshctl flash --uf2 F.uf2 # one BOOTSEL volume (or --dev)
.venv/bin/python -m meshctl elf check F.elf   # static RP2350 checks
.venv/bin/python -m meshctl elf info F.elf    # picotool file info
.venv/bin/python -m meshctl uf2 F.elf --out F.uf2
.venv/bin/python -m meshctl reboot [--bootsel]
.venv/bin/python -m meshctl --port P debug log [--seconds N]
.venv/bin/python -m meshctl --port P debug counters [--rounds N --interval S]
.venv/bin/python -m meshctl records list      # QR audit files
.venv/bin/python -m meshctl records prune --keep 20
.venv/bin/python -m meshctl history --port P [--limit N]
```

Flashing uses the BOOTSEL mass-storage volume (`udisksctl` mount + copy,
no root). Live `picotool` device commands need raw USB access and report
`PERMISSION` under stock udev instead of escalating. Every firmware USB
op from the original CLI (`status`, `send`, `listen`, `chat`, pairing,
block, time, radio, ping, provision, contacts) is unchanged.

The radio-free firmware contract requires `RADIO_UNAVAILABLE` for RF ops
(`radio on`, `ping`, `send`). The CLI surfaces that error and exits nonzero.
Pairing, contacts, time and provisioning do not require radios, but their
actual firmware integration must be verified separately.

The RP2350 firmware uses asynchronous TRNG reads with `sample_count = 200`
and all entropy health checks enabled. The pinned `embassy-rp` 0.10.0
default of 25 can cause repeated health-check failures and long pairing
delays; 200 matches the [corrected upstream default](https://github.com/embassy-rs/embassy/blob/81ec6e0ac62e7f778c6b3539c3146de4673974f9/embassy-rp/src/trng.rs).
This is a firmware correction, not a reason to increase host timeouts.

Radio-enabled firmware services CDC reads and writes independently. A
single command must receive its matching-ID reply without another host
write; received events must also arrive while the host is only listening.
An older image drained queued output only after the next input packet,
causing one-command-behind replies. Flushing the host input buffer or
increasing timeouts does not repair that firmware scheduling bug.

Use the USB serial number and a matching-ID `status` result to identify
each board after flashing. The corrected radio image advertises
`mesh-node radio-secure`; its `/dev/serial/by-id/` path can therefore differ
from the older `mesh-node radio-free` descriptor even on the same board.
Do not infer a board's identity from its current `ttyACM` number.

## Layout

- `meshctl/__main__.py` — argparse CLI, 1:1 onto firmware USB ops.
- `meshctl/serial_link.py` — owned-port framing: locks, monotonic ids,
  interleaved-event matching, bounded deadlines, no caller mutation.
- `meshctl/pairing.py` — strict canonical `LMESH1:` validation + local
  QR PNG encode/decode (`zxing-cpp`, Pillow, NumPy only).
- `meshctl/contacts.py` — local display-name map (written only on
  `pair_import` activation; firmware slots stay authoritative).
- `meshctl/history.py` — per-port user-only SQLite history (plaintext).
- `meshctl/local.py` — `.mesh-local/` private paths (0600/0700).

## Verification status

Host-only verification on Linux: editable installation succeeded; 25 PTY
checks and 7 real-PTY/history regression tests passed. These cover serial
cleanup and ownership, buffered and partial event records, oversized-line
resynchronization, matched replies amid unrelated traffic, firmware errors,
QR export/import rejection, and private history with full u64 sequences.

```sh
.venv/bin/python -m unittest discover -s host/tests -v
.venv/bin/python .mesh-local/pty_runner.py --meshctl '.venv/bin/python -m meshctl'
```

The PTY runner is a local development artifact, not part of the installed
package. Its device responses are emulated; a passing result is not proof
of firmware pairing, encryption, Pico boot, flash persistence, or RF.
The Linux port lock uses `flock`; other operating systems are not verified.
Radio scripts still require review of listener readiness, full retry-window
coverage, and sender/packet correlation before hardware acceptance.

Hardware check on C (`19e357db25fb1c77`) with the corrected radio-free
image: 20 fresh-session offers completed in 19–21 ms, each followed by
status; CLI QR export also succeeded. After a normal USB power cycle,
the first offer completed in 36 ms with a new record and the provisioned
identity intact. TX-attempt counters remained zero. The enumeration-time
USB banner was later removed because normal hosts clear their receive
buffer at open and its tail corrupted the first request; A and B confirmed
the silent-startup fix on their first post-flash requests. A/B completed a
full in-person pairing ceremony with matching transcript fingerprints and
contact activation on both ends; RF `send` still correctly returns
`RADIO_UNAVAILABLE`, so no over-air verification has occurred.

Radio-runtime software verification (2026-09-21): a native harness exercised
the production runtime/engine/USB parser and extracted USB scheduling loop
with simulated hardware boundaries. Regressions reproduced delayed USB
replies, missing engine-originated storage commits, an incorrect zero
CAD-clear count, and missing idle-clock advancement before their fixes.
Corrected code passed isolated-reply, durable send/receive/ACK and replay-
restore, failed-commit/no-TX, full-control-queue, zero-count, and authenticated
reception after three idle hours without USB input. This is not on-board or
over-air evidence; the corrected image still needs hardware acceptance.
Fresh matched-ID
status on B and C reported SX1276 version `0x12`, radios disabled, and zero
TX attempts before updating their firmware.
