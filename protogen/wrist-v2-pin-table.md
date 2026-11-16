# Wrist module v2: PROPOSED pin table (2026-11-13, deck session). NOT wired, NOT in hardware.yaml yet.

Proposal for Gil to solder the S3 end from. Every signal can move to another free pad; if you change one, tell me and I will update the table.
Source of the new parts: the wrist-mount (Blender) session's handoff. Existing pins are the tested ones from `ui-board-pin-table.md`.

## 0. Read first (safety)
1. **Backfeed.** The plan feeds the S3's 5V pad from the PD-trigger 5 V bus AND leaves the USB-C ports reachable for reflashing. Never have both at once:
   unplug the bank/5 V bus before plugging USB into a computer (unless you have confirmed this board has a VBUS blocking diode). Same rule as for S3 #1.
2. **PD trigger DIP must stay at 5 V.** 12 V on this bus kills the S3, LCD, servo and CO2 sensor.
3. **LCD shield A0** (button ladder) idles at 5 V: it must NEVER go straight to an S3 pin (see the divider below).
4. **LCD shield D10** (backlight) must never be driven high: leave it unconnected.
5. **T6615 CO2 UART level is unverified.** Before connecting its TX line, measure the idle voltage on that line with a meter (against GND, sensor powered). ~3.3 V: wire direct. ~5 V: put a divider (e.g. 1k top / 2k bottom) on that one wire. Do not fit a divider blindly: on a 3.3 V line it falls below the S3's input-high level.
6. Never use GPIO 0, 3, 45, 46 (strapping), 19, 20 (USB), 26-37 (flash/PSRAM), 48 (onboard RGB LED), 43/44 (boot ROM prints on UART0 TX at start).

## 1. Pin assignment
| Signal | S3 GPIO | Part pin | Status | Notes |
|---|---|---|---|---|
| Encoder CLK | 6 | KY-040 CLK | built | unchanged |
| Encoder DT | 7 | KY-040 DT | built | unchanged |
| Encoder SW | 15 | KY-040 SW | built | unchanged |
| Buzzer | 16 | buzzer S | built | unchanged |
| TM1637 CLK | 4 | TM1637 CLK | built | unchanged |
| TM1637 DIO | 5 | TM1637 DIO | built | unchanged |
| TP223 touch | 21 | TP223 OUT | built | unchanged |
| I2C SDA | 11 | BME280 + IMU #2 SDA | planned | OLED removed from this bus |
| I2C SCL | 12 | BME280 + IMU #2 SCL | planned | |
| LCD RS | 8 | shield D8 | new | old keypad row 1 pin |
| LCD E | 9 | shield D9 | new | old keypad row 2 pin |
| LCD D4 | 10 | shield D4 | new | |
| LCD D5 | 17 | shield D5 | new | |
| LCD D6 | 39 | shield D6 | new | |
| LCD D7 | 40 | shield D7 | new | |
| LCD buttons (optional) | 1 | shield A0 via divider | new, DROP FIRST | ADC1 only (Wi-Fi/ESP-NOW blocks ADC2). Replaces the old MAX4466 pin |
| Lid servo SG90 PWM | 2 | servo signal | new | 50 Hz LEDC on its own timer (not the buzzer's) |
| MOSFET module SIG | 13 | module SIG | new, unconfirmed | what does it switch? |
| CO2 T6615: S3 TX | 41 | T6615 RX | new | UART1 |
| CO2 T6615: S3 RX | 42 | T6615 TX | new | level check above |
| Mic I2S SCK/BCLK | 14 | mic SCK | new | INMP441 round breakout, confirmed from Gil's photos (2026-11-13, relayed by the Blender session) |
| Mic I2S WS | 38 | mic WS | new | |
| Mic I2S SD | 47 | mic SD | new | L/R pad to GND = left slot |
| Spare | 18 (only if this bare board has the pad) | | | the old base had none |

Count: the 8 keypad pins (8, 9, 10, 17, 39, 40, 41, 42) plus GPIO1 (old mic) and the never-assigned 2, 13, 14, 38, 47 give exactly the 14 new signals. It is TIGHT: if the bare board does not break out some of these pads, drop in this order: (1) LCD buttons on GPIO1, (2) MOSFET, (3) TM1637 (secondary readout), then tell me.

## 2. Power
- 5 V bus (PD trigger, DIP at 5 V): S3 5V pad, LCD shield 5 V, SG90, T6615, MOSFET module control side if it needs 5 V. Add a 470-1000 uF capacitor across the 5 V bus near the servo.
- 3V3 (from the S3): encoder +, TM1637 VCC, TP223, BME280, IMU #2, mic VDD (INMP441 is 3.3 V ONLY).
- Common GND everywhere.
- The LCD data lines are 3.3 V logic into a 5 V HD44780: works (VIH 2.2 V) as long as the shield's R/W is tied low (write only; no readback to the S3 so nothing 5 V can come back).

## 3. LCD shield button ladder (optional, GPIO1)
Nominal DFRobot-style ladder (2 k pull-up to 5 V): idle 5.0 V; Right 0 V, Up 0.71 V, Down 1.51 V, Left 2.44 V, Select 3.62 V. Clones differ: measure yours.
Put a divider on A0: 22 k in series to GPIO1, 33 k from GPIO1 to GND (scale 0.6). Resulting voltages: idle 3.0 V, Select 2.2 V, Left 1.5 V, Down 0.9 V, Up 0.4 V, Right 0 V; gaps are at least 0.4 V, enough to tell the five buttons apart. The encoder drives the menu anyway, so this is a convenience.

## 4. Questions for Gil (answers change the table)
1. Same S3W board (MAC 02:00:00:00:00:02) lifted off its green base, or a different board? Different = the visor's sender-MAC filter rejects it (visor reflash, your go needed) and I back up the new board before I flash anything.
2. Which pads does the bare Gold Edition really expose? Please read the silkscreen or send a photo, then I confirm every GPIO above.
3. ~~Round mic type~~ ANSWERED: INMP441 I2S (pads L/R, WS, SCK, SD, VDD, GND); wire VDD to 3V3 and L/R to GND (left slot). Pins stand.
4. What does the MOSFET module switch (servo power? something else)?
5. Does it have a VBUS diode so USB and the 5 V bus can overlap? (Assume no.)

## 5. Visor side
The 1.3 in OLED belongs to the visor controller S3V (hardware.yaml already has it as OLED13 on S3V, I2C GPIO11/12, 0x3C). OLED_W leaves the wrist nets. The v2 OLED firmware (face on top, status below) is written and compiled, not flashed.

## 2026-11-15 firmware status (deck session)
New wrist board confirmed by Gil: ESP32-S3 N16R8, MAC 02:00:00:00:00:03 (a third board; the old wrist …:02 is unchanged). Factory flash backed up.
Flashed: `protogen/releases/ui-node-20261115-wrist-v2-lcd/` = `ui-node.ino` with `UI_WRIST_V2 1`.
- IMPLEMENTED in firmware: encoder 6/7/15, buzzer 16, TM1637 4/5, TP223 21, LCD (RS 8, E 9, D4 10, D5 17, D6 39, D7 40) with an encoder menu.
- NOT implemented: LCD buttons (GPIO1), lid servo (2), MOSFET (13), T6615 (41/42), INMP441 (14/38/47). These pins are left undriven.
- Menu: turn = move (Face, Boop, Brightness, Volume, Mute, Audiolink, BPM, Tap tempo); press = edit/finish or toggle; Boop plays on the second press; hold 1.5 s = restart the LCD.
- Whether the board is physically wired to this table is NOT confirmed; the LCD/menu is untested on hardware.
- The visor must run `proot-s3-port/releases/20261115-rollout-2wrist/` (accepts both wrist boards) before the link comes up.

## 2026-11-15 AS WIRED correction (Gil)
LCD D4 is on GPIO11 and LCD D5 is on GPIO18 (not 10 and 17). Firmware follows the wiring, no resoldering:
LCD = RS 8, E 9, D4 11, D5 18, D6 39, D7 40. I2C moves to SDA GPIO10, SCL GPIO12 (BME280 + IMU #2 go there when wired). GPIO17 is now the spare (18 is used).
The section 1 table above still shows the original proposal for these four rows.
