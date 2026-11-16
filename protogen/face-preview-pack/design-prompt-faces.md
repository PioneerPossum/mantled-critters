# Claude Design prompt: proot face preview (step 1, preview only)

Attach to the Design session: `contact-sheet.png` (all faces at 4x, mirrored the way the visor shows them) and,
if it accepts multiple files, the individual 64x32 PNGs from `png/` (they are tiny; nearest-neighbour scaling only).

```
I have a protogen visor made of two 64x32 HUB75 LED panels (P2.5, 2.5 mm pixel pitch, so each panel is about
160 x 80 mm), chained into one 128x32 display. Each face is a single 64x32 sprite for ONE side; the code
mirrors it horizontally onto the second panel, so the full face is the sprite plus its mirror image. The attached
contact sheet shows every current face in that mirrored form, and the PNGs are the raw 64x32 sprites.

Please build an interactive LED-matrix PREVIEW so I can see how these faces look on the real hardware:

1. Render each face as a dot-matrix: round or rounded-square lit dots on a black background, one dot per
   pixel, with a soft glow/bloom and a subtle gap between dots so it reads as individual LEDs. Pure black
   pixels are OFF (no light). Keep colours faithful to the PNG data (the hardware uses 16-bit RGB565 colour).
2. Show the two panels as separate 64x32 modules side by side (128x32 total), with the right panel
   showing the mirrored sprite. Make a toggle for mirror on/off.
3. Layout views: (a) flat, front-on; (b) the two panels in a shallow V, each about 20 degrees off flat,
   as in the head shell; (c) a rotate control, because on the real visor the faces currently appear
   rotated about 90 degrees (panels are mounted portrait). Let me rotate the picture in 90-degree steps.
4. A brightness slider (the firmware currently runs at full brightness 255 and will likely be capped around
   64 to 128): show how a lower brightness changes the look.
5. State picker: idle (`normal`), blink (`blink`), talk (`talk1`..`talk5`), loud (`loud1`..`loud5`),
   yell (`yell1`..`yell5`), plus `smug`, `smug2`, `amogus`, and `bsod` (this one is 128x32 and is NOT mirrored).
   Add a "play talking" button that cycles the talk/loud/yell frames at roughly 5 to 10 frames per second so I can
   see the mouth animate.

Constraints to respect (so anything you propose can actually be put on the visor):
- Output is always 64x32 pixels per face half, 16-bit colour, no anti-aliasing, no transparency.
- Colours must survive being shown at reduced brightness on LEDs; keep contrast high and avoid very dark colours.
- Do not redesign anything yet. This is a preview-only pass to see how the current art renders.

After the preview works, I'll tell you which faces to modify.
```

Notes for Gil (not part of the prompt):
- The sprites were decoded from the compiled `images.h` of the S3 port (what the visor actually shows), so they
  include `smug2`, which is missing from the upstream PNG folder.
- Pure white on the LEDs is much harsher than on a screen. Expect Design's preview to look nicer than the real panels.
- The boop animations (GIFs) are not included in this pass.
