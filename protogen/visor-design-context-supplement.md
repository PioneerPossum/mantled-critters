# Claude Design context supplement: protogen visor (2026-11-05)

Paste this AFTER the main prompt (visor-electronics-design-prompt.md). Dimensions marked ~ are typical
values from memory, not measured. Gil should correct them with a ruler or caliper where it matters.
Items marked GIL need his input.

```
CONTEXT FOR VIEW 2 (physical layout)

Project: a protogen fursuit head with a two-panel LED visor face. Worn on the head, battery-powered from a
USB-C bank (carried on the body, cable runs up to the head). Goals: clean internal wiring, serviceable, cool
(fan + servo-actuated vent flaps), fuse block mounted on the OUTSIDE of the head for access.

Component sizes (approximate, ~):
- HUB75 P2.5 LED panel, 64x32 px, 1/16 scan: ~160 x 80 mm, ~15 mm deep with the module back; 16-pin IDC IN and OUT
  headers on the rear. Two of them side by side form 128x32 (~320 x 80 mm total) and sit in a V.
- ESP32-S3 DevKit (N16R8): ~70 x 26 mm, pin headers (Gil plans to desolder headers and use flying leads).
- EPICFLAME 12V->5V buck, x2: small board, ~ 40 x 20 mm each, one per panel.
- NULLLAB 5V/servo supply boards, x2: GIL to measure.
- HiLetgo PCA9685 servo driver: ~ 62 x 25 mm.
- MG90S micro servo, x4: ~ 23 x 12 x 29 mm each (plus horn).
- Noctua NF-A4x10 fan: 40 x 40 x 10 mm.
- 6-way ATC/ATO fuse block (ELECTOP): GIL to measure.
- Small boards: VL53L1X (boop sensor), LSM6DS3 IMU, GY-BME280 x2, MAX4466 mic, INMP441 mic (~ 14 x 14 mm),
  SSD1306 OLED (inside the helmet).
- Wago 221 connector for the GND star point.

Placement hints:
- Boop sensor (VL53L1X) faces forward at the muzzle/nose area.
- Mic(s) need an opening toward the mouth/front, away from the fan.
- Inside BME280 in the dead-air space of the head, outside BME280 on the exterior near a vent.
- Fan and 4 vent flaps (one servo each) set up a through-flow path. Draw intake and exhaust.
- Keep 12V fused wiring (18 AWG) grouped and separate from the HUB75 ribbon and the signal wires (22 AWG).
- Panels run the most current, so keep the buck outputs short to the panels.

OPEN ITEMS the diagram should leave room for:
- Panel orientation: the faces currently appear rotated ~90 degrees in the visor. Final panel orientation is
  undecided (a rotation setting will be applied in firmware once the panels are mounted).
- Brightness will be capped in firmware (not final).
- GIL: head/shell interior dimensions or a photo, where the power bank cable exits, panel V angle.
```

Attach with the prompt if the tool accepts images:
1. A photo of the visor with the panels lit (the lit-visor photo from earlier).
2. A photo of the head shell interior, or any dimensions of it.
3. Optionally a photo of the NULLLAB boards and the fuse block, so it can size them.
