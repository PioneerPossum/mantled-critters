# Control-surface board pin table (2026-11-08)

Board: the second ESP32-S3 N16R8 (MAC 02:00:00:00:00:02) on the GREEN SCREW-TERMINAL base. It talks to the visor controller
(S3 #1, 02:00:00:00:00:01, pluggable-terminal base, which keeps GPIO18 = HUB75 "A") over ESP-NOW.
GPIO18 is NOT broken out on the screw base, so it is not used here. GPIO19/20 are the USB data pins: using them as GPIO kills USB serial and flashing, so DO NOT wire them.
Free terminals Gil reported on the screw base: 8, 9, 10, 17, 21, 38, 47, 48, 2 (+ 19, 20, avoid). Firmware: `protogen/ui-node/ui-node.ino`, `UI_PINS_TEST 1`.

## TEST wiring (full 4x4 keypad + encoder + buzzer + OLED + TM1637)
Keypad columns C1..C4 are ALREADY wired to GPIO39, 40, 41, 42 (confirmed by Gil 2026-11-08).
| Part | Part pin | Wire to |
|---|---|---|
| Keypad rows | R1 / R2 / R3 / R4 | GPIO8 / GPIO9 / GPIO10 / GPIO17 |
| KY-040 encoder | GND / + / CLK / DT / SW | GND / 3V3 / GPIO6 / GPIO7 / GPIO15 (AS WIRED; found by `watch`) |
| Buzzer module | - / middle / S | GND / middle / GPIO16 (assumed as wired; check with `buzzfind`) |
| 1.3" OLED (128x64, 0x3C) | GND / VCC / SCL / SDA | GND / 3V3 / GPIO12 / GPIO11 (shared I2C bus) |
| TM1637 4-digit display | GND / VCC / CLK / DIO | GND / 3V3 / GPIO4 / GPIO5 (ALREADY WIRED per Gil) |
Why these rows: 8, 9, 10 are neighbouring terminals (and consecutive GPIOs) and 17 sits just past the missing 18, so R1..R4 = 8, 9, 10, 17 is the tidiest set. Any four free pins work: the firmware only needs the final list in R1..R4 order.
Left over: GPIO48 (kept off the scanned rows because it drives the onboard RGB LED; fine as a spare input). GPIO19/20 stay unused (USB).
TM1637: already on GPIO4/GPIO5, enabled in the test build.

## FINAL wiring (once sensors come off this board, or on a base that breaks out more pins) `UI_PINS_TEST 0`
| Part | Wire to |
|---|---|
| OLED | GPIO11 SDA / GPIO12 SCL, 3V3 |
| TM1637 | CLK GPIO4, DIO GPIO5, VCC 3V3 |
| KY-040 | CLK GPIO6, DT GPIO7, SW GPIO15, + 3V3 |
| Buzzer | S GPIO16, - GND |
| Keypad rows R1..R4 | GPIO8, 13, 14, 47 (not 18) |
| Keypad cols C1..C4 | GPIO39, 40, 41, 42 |
Check each of those terminals exists on the base you use before wiring.

## Key map (S1..S16, row by row)
S1-S4 face 1-4 | S5-S9 boop animation 0-4 | S10 random boop | S11 next face | S12 mute | S13/S14 brightness -/+ | S15/S16 volume -/+.
Encoder: turn = volume, press = mute. Buzzer = key tick, higher chirp on confirmed command, low buzz on failure.

## Buzzer active/passive test (serial: `buzz 1000 300` then `buzz 3000 300`)
Passive: pitch changes. Active: same sound either way.

## AS WIRED on 2026-11-08 (authoritative)
Keypad rows R1..R4 = 8, 9, 10, 17 | cols C1..C4 = 39, 40, 41, 42 | encoder CLK 6, DT 7, SW 15 | buzzer 16 (to confirm) | TM1637 CLK 4, DIO 5 | OLED I2C 11/12 (moves to the visor).
Free: 21, 38, 47, 48, 2. Still wired from the old sensor-node role: MAX4466 on GPIO1.
Planned wrist extras: TP223 touch (one free GPIO), external air-quality sensor, second IMU (I2C), small mic.
Diagnostics: serial `watch` (prints which GPIO changed), `buzzfind` (tone on each candidate pin), `s` (state + encoder pin levels).

## Additions 2026-11-08 night
TP223 touch module OUT -> GPIO21 (VCC 3V3, GND). Touch = toggle audiolink (CONFIRMED WORKING by Gil). Free after that: 38, 47, 48, 2.
Key S12 = tap tempo (3+ taps set the BPM and turn audiolink on). Encoder press = mute.
Outside BME280 (0x77) and the second IMU (0x6A/0x6B) go on the wrist module's I2C (11/12) when wired. INMP441 pair goes on the visor (S3 #1), not here.
