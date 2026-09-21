# meshctl wiki

Operator docs for the Python host tools
([lora-mesh-app](https://github.com/fedoragobrowse-design/lora-mesh-app)).
Crypto and firmware internals live in the
[radio wiki](https://github.com/fedoragobrowse-design/lora-mesh-radio/wiki).

- [[CLI-reference]] — every command, flags, single-owner ports.
- [[Messaging]] — send/receive/chat/TUI/meshchat operator flow.
- [[Error-catalog]] — exact error strings and fixes.
- [[Glossary]] — A/B/C, slots, fingerprints, BOOTSEL, V3…

## The one rule

The host holds **no keys**. It moves opaque `LMESH1:` text between firmware
and QR PNGs and types plaintext. Boards are USB serials, never ACM numbers.
`dialout` group required: `sg dialout -c '…'`.
