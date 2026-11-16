# Protogen visor: ESP32-S3 pin table (authoritative, 2026-11-06)

Source of truth: `proot-s3-port/proot-s3/functions/drawfaces.h` (HUB75 pins), `oled_display.h` (I2C), `proot-s3.ino`
(mic, boop, button), `PORT-NOTES.md`. Board: ESP32-S3 N16R8 devkit (octal PSRAM). Status column: IMPLEMENTED = in the
current sketch; PROPOSED = Gil's plan, not in the sketch yet. Check pin names against YOUR board's silkscreen before wiring.

## 1. HUB75 LED matrix: ribbon pin -> signal -> ESP32 GPIO (all IMPLEMENTED)
Only panel 1's IN header connects to the ESP32. Panel 1 OUT -> panel 2 IN (second ribbon, straight through).
Ribbon pin 1 = red-stripe end of the plug.

| Ribbon pin | HUB75 signal | ESP32-S3 GPIO | Notes |
|---|---|---|---|
| 1 | R1 | 4 | upper half red |
| 2 | G1 | 5 | upper half green |
| 3 | B1 | 6 | upper half blue |
| 4 | GND | GND | ground |
| 5 | R2 | 7 | lower half red |
| 6 | G2 | 15 | lower half green |
| 7 | B2 | 16 | lower half blue |
| 8 | E | not connected | unused (1/16 scan, 64x32); leave open |
| 9 | A | 18 | row address |
| 10 | B | 8 | row address |
| 11 | C | 3 | row address; GPIO3 is a strapping pin but is the DMA library's default |
| 12 | D | 42 | row address |
| 13 | CLK | 41 | clock |
| 14 | LAT | 40 | latch |
| 15 | OE | 39 | output enable. AS BUILT 2026-11-14: Gil's wire is on the terminal labelled 39; this table said GPIO2 until then (nothing was ever wired to GPIO2, which left the panels dark) |
| 16 | GND | GND | ground |

Panel power is separate: 5 V from the bucks (see power chain), ground shared with the ESP32.

## 2. Everything else on the ESP32

| Function | ESP32-S3 pin | Direction | Level / power | Status | Wiring notes |
|---|---|---|---|---|---|
| I2C SDA | GPIO11 | bidir | 3.3 V | IMPLEMENTED (OLED) | bus shared by all I2C parts below; 400 kHz |
| I2C SCL | GPIO12 | out | 3.3 V | IMPLEMENTED (OLED) | |
| Mic MAX4466 OUT | GPIO1 (ADC1) | in, analog | 3.3 V supply | IMPLEMENTED | VCC from S3 3V3, GND; `baseline` in sketch must be re-tuned (currently 1945) |
| Face button | GPIO10 | in | 3.3 V | IMPLEMENTED | button between 3V3 and GPIO10. The sketch uses INPUT_PULLDOWN, FALLING interrupt (fires on release). Do NOT wire the other leg to GND. |
| Boop input (optional IR/sensor) | GPIO9 | in | 3.3 V logic | IMPLEMENTED | INPUT_PULLDOWN, RISING interrupt: sensor output goes HIGH on boop. Share GND. |
| Fan PWM (Noctua, 25 kHz) | GPIO13 | out | 3.3 V logic to fan PWM pin | PROPOSED | fan power is 12 V, fused, NOT from the S3 |
| Fan tach | GPIO14 | in | 3.3 V logic (open-collector, needs pull-up) | PROPOSED | fan pin 3 |
| INMP441 BCLK | GPIO17 | out | 3.3 V | PROPOSED | I2S mic, VDD 3.3 V |
| INMP441 WS | GPIO21 | out | 3.3 V | PROPOSED | |
| INMP441 SD | GPIO38 | in | 3.3 V | PROPOSED | L/R pin to GND (left channel) |
| VL53L1X INT (optional) | GPIO9 (or GPIO47; NOT 39: since 2026-11-14 it is HUB75 OE) | in | 3.3 V | OPTIONAL | only if the board's 6th pin is GPIO1/INT (verify the labels); could drive GPIO9 as a hardware boop trigger. XSHUT can stay unconnected. |
| Servos (PCA9685) | none | | | PLANNED | I2C only; no GPIOs |
| Spare | GPIO47, 48 (GPIO39 = HUB75 OE since 2026-11-14; GPIO2 is now free; the DAC data line needs a new pin) | | | FREE | 47/48 may drive an onboard LED on some devkits |

## 3. I2C devices on SDA 11 / SCL 12 (3.3 V, shared bus)

| Device | Address |
|---|---|
| VL53L1X distance (boop) | 0x29 |
| SSD1306 OLED 128x32 | 0x3C |
| PCA9685 servo driver | 0x40 |
| LSM6DS3 IMU | 0x6A (or 0x6B) |
| BME280 inside | 0x76 (SDO to GND) |
| BME280 outside | 0x77 (SDO to 3V3) |

Pull-ups: several breakout boards carry their own, so six devices can end up with parallel pull-ups. Check the bus; remove or desolder pull-ups if the combined value is too low.

## 4. Power pins on the ESP32 board
- **5V pin** <- NULLLAB board 1 (5 V). Never feed it while the S3's USB is plugged into a computer.
- **3V3 pin** -> I2C parts, MAX4466, INMP441, VL53L1X, BME280 x2, LSM6DS3, OLED, PCA9685 logic VCC. Keep within the 3V3 regulator's current.
- **GND** -> star point (fuse block neg bus). Several GND pins on the devkit, all the same net.
- Never connect 12 V to the ESP32 or to any 5 V/3.3 V part.

## 5. Pins to avoid on the S3 N16R8
- GPIO35, 36, 37: octal PSRAM. GPIO26-32: internal flash/PSRAM. GPIO19/20: USB. GPIO0, 45, 46: strapping.
- GPIO3 (HUB75 C) is a JTAG-source strapping pin; it works as the library default.
