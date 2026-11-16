# Claude Design prompt: protogen visor electronics (draft v2, 2026-11-04 night)

Status: reconciled against Gil's order screenshots on 2026-11-05 (05:13-05:16). Items marked (planned) are not wired or flashed yet. GPIO picks marked (proposed) are not implemented in the sketch. Fuse block being ordered (6-circuit block with negative bus, stud terminals, needs ring terminals). VL53L1X x4 (ACEIRMC TOF400C boards, 6-pin, XSHUT broken out) confirmed ordered.

```
Draw a clean wiring/block diagram for a protogen visor electronics build.
Style: schematic-like, color-coded rails (12V orange, 5V red, 3.3V yellow, GND black, signals blue), labeled wire gauges and fuse ratings. Mark anything tagged (planned) with a dashed outline.

POWER CHAIN
USB-C PD power bank -> PD decoy trigger module (DIP-set to 12V; plan on 3A / 36W max) -> 12V distribution: one external 6-way standard ATC/ATO blade fuse block with negative bus, M6 main studs and M4 stud terminals fed by ring terminals (planned, ordering; fallback: 6 inline blade holders) + Wago 221 for GND star point.
Fused 12V branches (18 AWG silicone):
 1. Buck A (EPICFLAME 12V->5V, 5A fixed) [3A fuse] -> 1000uF 16V cap -> LED panel 1 power
 2. Buck B (same) [3A fuse] -> 1000uF 16V cap -> LED panel 2 power
 3. NULLLAB board 1, "logic hub" (3-20V in, 5V out) [2A fuse] -> ESP32-S3 5V pin (note: do not feed 5V pin while S3 USB is plugged into a computer)
 4. NULLLAB board 2, "servo bus" + 470-1000uF cap [2A fuse] -> PCA9685 V+ screw terminal -> 4x MG90S micro servos (suit vents) (planned)
 5. Noctua NF-A4x10 PWM, 12V 4-pin fan [1A fuse] direct from 12V (pin1 GND, pin2 +12V, pin3 tach, pin4 PWM)
All grounds meet at one thick star point. Signals use 22 AWG silicone.

ESP32-S3 N16R8 SIGNALS
HUB75 16-pin IDC ribbon to panel 1 IN header (pin 1 = red-stripe end of the plug); panel 1 OUT -> panel 2 IN (chained, 128x32 total).
HUB75 pins: R1=4 G1=5 B1=6 R2=7 G2=15 B2=16 A=18 B=8 C=3 D=42 CLK=41 LAT=40 OE=2, E unused; ribbon pins 4 and 16 are GND.
I2C bus (GPIO11 SDA, GPIO12 SCL, 3.3V from the S3 3V3 pin): OLED (0x3C), LSM6DS3 IMU (0x6A/0x6B), VL53L1X distance sensor as boop sensor (0x29), HiLetgo PCA9685 16-channel servo driver, 2 bought (0x40, planned; logic VCC from S3 3V3, V+ servo supply from NULLLAB board 2 at 5V, absolute max 6V, never 12V; no Type-C input used; do not connect the I2C header's V+ pin to the S3) driving 4 MG90S vent servos on channels 0-3, 2x GY-BME280 3.3V 6-pin boards (VCC GND SCL SDA CSB SDO; CSB tied to 3V3 for I2C; SDO to GND = 0x76, SDO to 3V3 = 0x77): one inside the head, one outside, for vent control (planned).
Analog mic MAX4466 on 3.3V -> GPIO1. Face-select button: one leg to 3V3, the other to GPIO10 (firmware uses INPUT_PULLDOWN, so do NOT wire the other leg to GND). Optional boop input GPIO9 (existing pin; INPUT_PULLDOWN, output goes HIGH on boop; shared GND). Optional: VL53L1X INT/GPIO1 pin (if the board has it) to GPIO9 as a hardware boop trigger.
Fan PWM (25kHz) GPIO13 and tach GPIO14 (proposed). INMP441 I2S mic (planned): BCLK GPIO17, WS GPIO21, SD GPIO38 (proposed). GPIO39, 47, 48 spare. Servos are on the I2C PCA9685 (no GPIOs used).
Spare parts shown greyed: 5 spare MAX4466 (6 bought), 1 spare LSM6DS3 (2 bought), spare PD triggers (12 bought), one Lonely Binary 3.3V/5V USB-C breadboard supply (3 bought; bench use only, 500mA per rail). Also in cart 2026-11-05: 140-pc standard blade fuse kit, ELECTOP 6-way fuse block, Treedix USB-C PD trigger with V/A/W display (bench meter, not part of the permanent wiring), EGSCST INMP441 x5.

VIEW 3 (REQUIRED): ESP32-S3 pin assignment tables, as real tables a person can wire from, not just labels inside a box. The full authoritative data is in the attached visor-pin-table.md; use it exactly and do not invent or change any GPIO. Table A: the HUB75 16-pin ribbon, one row per ribbon pin (1-16) with signal name, ESP32 GPIO, and notes (pin 1 = red stripe; pins 4 and 16 GND; pin 8 E not connected). Table B: every other signal that runs to the ESP32 (I2C SDA/SCL, MAX4466 mic, face button, boop input, fan PWM and tach, INMP441 I2S, optional VL53L1X INT, spares) with GPIO, direction, voltage level, status (IMPLEMENTED vs PROPOSED), and wiring notes. Table C: the I2C device address list. Table D: the ESP32 power pins (5V, 3V3, GND) and what connects to each. Add a blank "wire colour" column to A and B so Gil can fill in the colours he uses. In the block diagram, each signal line into the ESP32 should be labeled with its GPIO number. Mark PROPOSED rows with a dashed outline.

VIEW 1: electrical block diagram as above.
VIEW 2: physical layout inside the visor shell: two panels in a V, S3, fuse block mounted on the OUTSIDE of the head for access, fan position with airflow arrows (intake/exhaust), vent flaps with servos, inside and outside BME280 positions, wire routing from the head to the power bank.
```
