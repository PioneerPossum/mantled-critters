# protogen: visor and wrist firmware (ESP32-S3)

Source and docs for the protogen visor: two chained 64x32 HUB75 panels (128x32) driven by an ESP32-S3 N16R8, a wrist control surface on a second S3, and a small sensor/mic path. Everything here is source only: no build output, no `.bin`/`.elf` images, no flash backups, no logs.

| Folder | What it is |
|---|---|
| `proot-s3-port/proot-s3-wireless/` | Visor face firmware: ESP32-S3 port of ProotOS with ESP-NOW input from the wrist and sensor boards, OLED status page, local mic. HUB75 OE on GPIO39, 20 MHz with flipped clock edge. |
| `proot-s3-port/proot-s3/` | Earlier, non-wireless port. `hub75-scope-test/` is a HUB75 wire/pattern test sketch. |
| `ui-node/` | Wrist control surface. `UI_WRIST_V2 1` selects the wrist v2 board (16x2 LCD + encoder menu). |
| `sensor-node/`, `bringup-test/`, `shared/` | Older sensor stream node, bring-up sketch, shared packet header (`sensor_packet.h`). |
| `tools/` | Host-side serial test scripts (soak, stream, command, watch). |
| `hardware/` | Wiring YAML, generators and the generated netlist/diagrams. |
| `face-preview-pack/` | Face PNGs, the design prompt and a zip of the face/animation source files. |
| `*/releases/*/ROLLBACK.txt` | Release records and restore commands (the binaries themselves are not published). |
| `visor-pin-table.md`, `wrist-v2-pin-table.md`, `ui-board-pin-table.md` | Pin tables. |

## Placeholder MAC addresses (read before building)
The device MAC addresses in this public copy are placeholders in the locally-administered range, so the firmware will not talk to real boards until you put your own back:

| Placeholder | Role |
|---|---|
| `02:00:00:00:00:01` | visor controller (receiver) |
| `02:00:00:00:00:02` | wrist board v1 |
| `02:00:00:00:00:03` | wrist board v2 |

Replace them in `proot-s3-wireless.ino` (`WL_SENDER_MACS`, `wl_peerMac`), `ui-node.ino` (`VISOR_MAC`), `sensor-node.ino` (`RX_MAC`), and the `tools/*.py` serial-port globs. Those scripts also match `/dev/serial/by-id/usb-Espressif*<MAC>*`, so the MAC must be the one your board reports. The ESP-NOW link needs no Wi-Fi network, SSID or password; none are used.

## Known stale files
Some design documents were not updated after the as-built changes of 2026-11-14/15:
- `visor-electronics-design-prompt.md` and everything in `hardware/` (yaml, netlist, diagrams) still show HUB75 OE = GPIO2. **As built it is GPIO39** (see `visor-pin-table.md` and `drawfaces.h`). `hardware.yaml` has no wrist v2 section.
- `wrist-v2-pin-table.md`: section 1 is the original proposal; the "AS WIRED correction" section at the end is the truth for the LCD pins (D4 = GPIO11, D5 = GPIO18) and I2C.

## Licence and attribution
The visor firmware is derived from **ProotOS** (catocatocat0/ProotOS), which itself builds on ProtOpenSource (PanickingLynx). Both are MIT-licensed; the two notices ("Copyright (c) 2025 Katxe" and "Copyright (c) 2022 PanickingLynx") are kept verbatim in `proot-s3-port/UPSTREAM-LICENSE` and must stay with any copy of the derived code. The face artwork zip includes the upstream `LICENSE` as well. The rest of this repository is under the licence in the repository root.
