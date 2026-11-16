/* ============================================================================
 * HELMET OLED: 1.3" 128x64 I2C (SH1106 by default, SSD1306 128x64 optional), CORE 0
 * ============================================================================
 * v2 (2026-11-10, wireless variant only). The original proot-s3 driver mirrored the
 * LED matrix onto a 0.91" SSD1306 128x32. This one drives a 1.3" 128x64 panel:
 *
 *   rows  0..31   mirror of the current face (the same 128x32 picture as the panels)
 *   rows 32..63   status page: 4 text lines supplied by the sketch (face number and
 *                 name, wireless link quality, brightness/volume, audiolink, ...)
 *
 * - Probes 0x3C at boot; if nothing answers, the Core 0 task is never started and
 *   drawFace()/playAnimation() skip OLED writes (zero overhead).
 * - SH1106 only supports PAGE addressing (not horizontal) and its RAM is 132 wide, so
 *   the glass starts at column 2. Set OLED_SH1106 0 for an SSD1306 128x64.
 * - Only the pages that changed are sent over I2C.
 * - The face mirror uses oledClearBuffer()/oledDrawPixel()/oledCommitFrame() exactly
 *   like before (called from drawfaces.h). The status page is built on Core 0 from a
 *   provider function registered with oledSetStatusProvider().
 * ============================================================================ */

#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>

// ============================================================================
// OLED HARDWARE & TASK CONFIGURATION
// ============================================================================

#define OLED_SDA_PIN     11
#define OLED_SCK_PIN     12
#define OLED_I2C_ADDR    0x3C
#define OLED_I2C_CLOCK   400000UL
#define OLED_SH1106      1            // 1.3" modules are almost always SH1106; 0 = SSD1306 128x64

#define OLED_WIDTH       128
#define OLED_HEIGHT      64
#define OLED_FACE_ROWS   32           // top half = face mirror
#define OLED_PAGES       (OLED_HEIGHT / 8)
#define OLED_FACE_BYTES  (OLED_WIDTH * OLED_FACE_ROWS / 8)   // 512
#define OLED_BUF_SIZE    (OLED_WIDTH * OLED_PAGES)           // 1024

#define OLED_REFRESH_MS  100          // face frames: at most 10 per second
#define OLED_STATUS_MS   250          // status page: 4 per second
#define OLED_CORE_ID     0
#define OLED_STATUS_LINES 4
#define OLED_STATUS_COLS  22          // 21 characters + terminator (6x8 font on 128 px)


// ============================================================================
// OLED STATE & BUFFERS
// ============================================================================

bool          oledConnected = false;
volatile bool oledFrameDirty = false;
uint8_t       oledBackBuffer[OLED_FACE_BYTES];   // written by Core 1 while the face is drawn (rows 0..31)
uint8_t       oledTxBuffer[OLED_BUF_SIZE];       // what the Core 0 task sends: face + status
uint8_t       oledSent[OLED_BUF_SIZE];           // what the panel already shows (so unchanged pages are skipped)
TaskHandle_t  oledTaskHandle = nullptr;

typedef void (*OledStatusFn)(char (*lines)[OLED_STATUS_COLS]);
OledStatusFn  oledStatusFn = nullptr;

inline void oledSetStatusProvider(OledStatusFn fn) { oledStatusFn = fn; }


// ============================================================================
// LOW-LEVEL I2C HELPERS
// ============================================================================

inline void oledSendCommand(uint8_t cmd) {
  Wire.beginTransmission(OLED_I2C_ADDR);
  Wire.write(0x00);   // command stream
  Wire.write(cmd);
  Wire.endTransmission();
}

inline void oledInitController() {
  static const uint8_t pre[] = {0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40};
  for (uint8_t c : pre) oledSendCommand(c);
#if OLED_SH1106
  oledSendCommand(0xAD); oledSendCommand(0x8B);                         // SH1106: internal DC-DC on
#else
  oledSendCommand(0x8D); oledSendCommand(0x14);                         // SSD1306: charge pump on
  oledSendCommand(0x20); oledSendCommand(0x02);                         // page addressing
#endif
  static const uint8_t post[] = {0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF};
  for (uint8_t c : post) oledSendCommand(c);
}

/** Sends oledTxBuffer page by page. `all` forces every page; otherwise only changed pages go out. */
inline void oledFlushBuffer(bool all) {
  const uint8_t colOff = OLED_SH1106 ? 2 : 0;
  for (uint8_t page = 0; page < OLED_PAGES; page++) {
    uint8_t* src  = &oledTxBuffer[page * OLED_WIDTH];
    uint8_t* sent = &oledSent[page * OLED_WIDTH];
    if (!all && memcmp(src, sent, OLED_WIDTH) == 0) continue;
    Wire.beginTransmission(OLED_I2C_ADDR);
    Wire.write(0x00);
    Wire.write(0xB0 | page);                    // page address
    Wire.write(0x00 | (colOff & 0x0F));         // column low nibble
    Wire.write(0x10 | (colOff >> 4));           // column high nibble
    Wire.endTransmission();
    for (uint16_t off = 0; off < OLED_WIDTH; off += 64) {
      Wire.beginTransmission(OLED_I2C_ADDR);
      Wire.write(0x40);                         // data stream
      Wire.write(src + off, 64);
      Wire.endTransmission();
    }
    memcpy(sent, src, OLED_WIDTH);
  }
}


// ============================================================================
// CORE 1 PIXEL MIRRORING HELPERS (CALLED FROM DRAWFACES.H, unchanged API)
// ============================================================================

inline void oledClearBuffer() {
  if (!oledConnected) return;
  memset(oledBackBuffer, 0, OLED_FACE_BYTES);   // only the face half; the status half is owned by Core 0
}

inline void oledDrawPixel(int x, int y, uint16_t rgb565) {
  if (!oledConnected) return;
  if ((unsigned int)x >= OLED_WIDTH || (unsigned int)y >= OLED_FACE_ROWS) return;
  uint16_t index = x + ((y >> 3) << 7);
  uint8_t  mask  = (1 << (y & 7));
  if (rgb565 != 0) oledBackBuffer[index] |= mask; else oledBackBuffer[index] &= ~mask;
}

inline void oledCommitFrame() {
  if (!oledConnected) return;
  oledFrameDirty = true;
}


// ============================================================================
// STATUS PAGE (text drawn on Core 0 into the lower half of oledTxBuffer)
// ============================================================================

class OledStatusCanvas : public Adafruit_GFX {
public:
  uint8_t* base = nullptr;
  OledStatusCanvas() : Adafruit_GFX(OLED_WIDTH, OLED_HEIGHT - OLED_FACE_ROWS) {}
  void drawPixel(int16_t x, int16_t y, uint16_t color) override {
    if (!base || x < 0 || x >= OLED_WIDTH || y < 0 || y >= (OLED_HEIGHT - OLED_FACE_ROWS)) return;
    uint16_t i = x + (y >> 3) * OLED_WIDTH;
    if (color) base[i] |= (1 << (y & 7)); else base[i] &= ~(1 << (y & 7));
  }
};
OledStatusCanvas oledStatusCanvas;

inline void oledRenderStatus() {
  char lines[OLED_STATUS_LINES][OLED_STATUS_COLS];
  for (int i = 0; i < OLED_STATUS_LINES; i++) lines[i][0] = 0;
  if (oledStatusFn) oledStatusFn(lines);
  uint8_t* base = &oledTxBuffer[OLED_FACE_BYTES];
  memset(base, 0, OLED_BUF_SIZE - OLED_FACE_BYTES);
  oledStatusCanvas.base = base;
  oledStatusCanvas.setTextSize(1);
  oledStatusCanvas.setTextColor(1);
  oledStatusCanvas.setTextWrap(false);
  for (int i = 0; i < OLED_STATUS_LINES; i++) {
    oledStatusCanvas.setCursor(0, i * 8);
    oledStatusCanvas.print(lines[i]);
  }
}


// ============================================================================
// CORE 0 BACKGROUND TASK & BOOT INITIALIZATION
// ============================================================================

inline void oledCore0Task(void* parameter) {
  const TickType_t delayTicks = pdMS_TO_TICKS(OLED_REFRESH_MS);
  uint32_t lastStatus = 0;
  for (;;) {
    bool push = false;
    if (oledFrameDirty) {
      oledFrameDirty = false;
      memcpy(oledTxBuffer, oledBackBuffer, OLED_FACE_BYTES);
      push = true;
    }
    uint32_t now = millis();
    if (oledStatusFn && now - lastStatus >= OLED_STATUS_MS) {
      lastStatus = now;
      oledRenderStatus();
      push = true;
    }
    if (push) oledFlushBuffer(false);
    vTaskDelay(delayTicks);
  }
}

inline void setupOLED() {
  Wire.begin(OLED_SDA_PIN, OLED_SCK_PIN, OLED_I2C_CLOCK);

  Wire.beginTransmission(OLED_I2C_ADDR);
  uint8_t error = Wire.endTransmission();
  if (error != 0) {
    oledConnected = false;
    Serial.println("[OLED] nothing at I2C 0x3C. Core 0 OLED task disabled.");
    return;
  }

  oledConnected = true;
  Serial.printf("[OLED] 128x64 %s found at 0x3C. Starting Core 0 task (face on top, status below)...\r\n", OLED_SH1106 ? "SH1106" : "SSD1306");

  memset(oledBackBuffer, 0, sizeof oledBackBuffer);
  memset(oledTxBuffer, 0, sizeof oledTxBuffer);
  memset(oledSent, 0xFF, sizeof oledSent);       // differs from the zeroed buffer, so the first flush sends everything

  oledInitController();
  oledFlushBuffer(true);

  xTaskCreatePinnedToCore(
    oledCore0Task,   // task
    "OLED_Core0",
    4096,            // stack bytes (text drawing needs more than the old 2048)
    nullptr,
    1,               // low priority: Wi-Fi keeps precedence on Core 0
    &oledTaskHandle,
    OLED_CORE_ID
  );
}
