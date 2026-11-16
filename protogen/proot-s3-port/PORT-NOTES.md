# ProotOS -> ESP32-S3 N16R8 port (deck session, 2026-11-03)

Upstream: https://github.com/catocatocat0/ProotOS (MIT, (c) 2025 Katxe; PanickingLynx 2022 notice also in its LICENSE).
Upstream clone (pristine, commit fc81313): ../ProotOS-upstream. This port copies its src/ and lib/FacialSprites headers;
keep the upstream LICENSE with any redistribution.

STATUS: compiles only. NOT flashed, NOT run on hardware. No S3 board on hand yet (3-pack ordered 2026-11-03).

## Changes vs upstream (pins only; nothing else touched)
| Signal | Upstream (classic ESP32) | This port (S3) |
|---|---|---|
| R1 G1 B1 | 25 26 27 | 4 5 6 |
| R2 G2 B2 | 14 12 13 | 7 15 16 |
| A B C D | 23 19 5 17 | 18 8 3 42 |
| E | unused | unused (1/16 scan, 64x32) |
| CLK LAT OE | 16 4 15 | 41 40 39 (OE was 2 until 2026-11-14; the visor is wired to 39) |
| Mic (MAX4466 OUT) | 35 | 1 (ADC1) |
| Boop sensor | 33 | 9 |
| Face button (to 3.3 V) | 32 | 10 |
| OLED SDA / SCL | 21 / 22 | 11 / 12 |
HUB75 pins = the DMA library's own ESP32-S3 defaults (src/platforms/esp32s3/esp32s3-default-pins.hpp). Avoided: 35-37 (octal PSRAM),
26-32 (internal flash/PSRAM), 19/20 (USB), 0/45/46 (strapping). Check your devkit's silkscreen: GPIO3 is a strapping pin but is the lib default.

## HUB75 16-pin IDC (standard layout; confirm pin 1 on the real header before wiring)
1 R1, 2 G1, 3 B1, 4 GND, 5 R2, 6 G2, 7 B2, 8 E (leave open), 9 A, 10 B, 11 C, 12 D, 13 CLK, 14 LAT, 15 OE, 16 GND.
Panels need their own 5 V supply, grounds shared with the ESP32. Only the FIRST panel's input header connects to the ESP32; the second chains from the first's OUT.

## Build (arduino-cli, esp32 core 3.3.12, HUB75-DMA 3.1.0, Adafruit GFX)
arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,CDCOnBoot=cdc --build-path build proot-s3
Result: 440464 B flash (14% of 3 MB app), 52376 B RAM (15%). Upload only with Gil's say-so on the named port; back up the board first if it is not factory-fresh.

## Open
- IN vs OUT header per panel; ICN2037-marked panels may need mxconfig.driver set if colours/ghosting look wrong.
- Upstream sets min_refresh_rate 120 and 20 MHz I2S; verify on real panels, lower if flicker.
- Mic baseline/gain must be re-calibrated (see main.cpp comments); S3 ADC differs from classic ESP32.
