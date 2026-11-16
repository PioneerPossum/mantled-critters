/* ============================================================================
 * PROTOGEN CONTROL SURFACE  (ESP32-S3 N16R8)             v1  written 2026-11-08
 * ============================================================================
 * Keypad + rotary encoder + 1.3" OLED + 4-digit display + buzzer. Sends commands
 * over ESP-NOW to the visor controller (face, boop, brightness, volume, mute) and
 * shows the visor's status. Wire format: sensor_packet.h (identical copy on all sides).
 *
 *   UI_PINS_TEST 1  screw-terminal test board: full 4x4 keypad + encoder + buzzer + OLED + TM1637.
 *                   (see ui-board-pin-table.md)
 *   UI_PINS_TEST 0  full wiring: 4x4 keypad, TM1637.
 *
 * Serial 115200 (plain lines, no dashboard). Commands:
 *   cmd face <n> | next | boop [idx] | bright <0-255> | vol <0-100> | mute <0|1>
 *   buzz <hz> <ms>   buzzer test (passive: pitch changes; active: same sound)
 *   oled 1106|1306   switch the OLED driver if the picture looks shifted or garbled
 *   s                print state
 * ============================================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <Adafruit_GFX.h>
#include <stdarg.h>
#include "sensor_packet.h"

// 2026-11-15: UI_WRIST_V2 1 = wrist v2 board (bare S3, MAC …:03, wrist-v2-pin-table.md). The 4x4 keypad is gone and its pins
// (8, 9, 10, 17, 39, 40, 41, 42) now carry the LCD and the CO2 sensor UART, so keypad scanning is OFF and those pins are never driven.
// Working in this build: encoder 6/7/15, buzzer 16, TM1637 4/5, TP223 touch 21, ESP-NOW link, serial commands.
// 16x2 LCD menu on the encoder: implemented 2026-11-15. NOT implemented yet: lid servo, MOSFET, T6615 CO2, INMP441 mic. Set 0 to rebuild for the old screw-terminal wrist board.
#ifndef UI_WRIST_V2
  #define UI_WRIST_V2 1
#endif
#ifndef UI_PINS_TEST
  #define UI_PINS_TEST 1
#endif

// ---------------------------------------------------------------------------
// Pins
// ---------------------------------------------------------------------------
#if UI_WRIST_V2
  // 2026-11-15: on the wrist v2 board Gil wired LCD D4 to GPIO11 and D5 to GPIO18, so I2C SDA moves from 11 to 10 (SCL stays 12).
  static const int PIN_SDA = 10, PIN_SCL = 12;
#else
  static const int PIN_SDA = 11, PIN_SCL = 12;
#endif
#if UI_PINS_TEST
  // Screw-terminal test board. Free terminals: 8, 9, 10, 17, 21, 38, 47, 48, 2 (19/20 are USB: never use).
  // Keypad columns C1..C4 are already wired to 39..42. Rows go on 8, 9, 10, 17 (8-9-10 are neighbours).
  // Encoder and buzzer are wired per the FIRST pin table (found with `watch` on 2026-11-08):
  static const int PIN_ENC_CLK = 6, PIN_ENC_DT = 7, PIN_ENC_SW = 15;
  static const int PIN_BUZZ = 16;                 // assumed from the same table; confirm with `buzzfind` (GPIO48 is left alone: onboard RGB LED)
  static const int KP_ROWS[] = {8, 9, 10, 17};    // keypad R1..R4
  static const int KP_COLS[] = {39, 40, 41, 42};  // keypad C1..C4
  static const int PIN_TM_CLK = 4, PIN_TM_DIO = 5; // TM1637 4-digit display: already wired (Gil, 2026-11-08)
  #define ENABLE_TM1637 1
#else
  static const int PIN_ENC_CLK = 6, PIN_ENC_DT = 7, PIN_ENC_SW = 15;
  static const int PIN_BUZZ = 16;
  static const int KP_ROWS[] = {8, 13, 14, 47};  // keypad R1..R4 (GPIO18 is not on the screw base)
  static const int KP_COLS[] = {39, 40, 41, 42}; // keypad C1..C4
  static const int PIN_TM_CLK = 4, PIN_TM_DIO = 5;
  #define ENABLE_TM1637 1
#endif
static const int KP_NR = sizeof(KP_ROWS) / sizeof(KP_ROWS[0]);
static const int KP_NC = sizeof(KP_COLS) / sizeof(KP_COLS[0]);
static const int PIN_TOUCH = 21;                  // TP223 touch module OUT (high while touched): toggles audiolink

// The visor controller (S3 #1) station MAC. Commands go unicast there.
static const uint8_t VISOR_MAC[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x01};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
static String noteMsg = "ready";
static bool   uiDirty = true;

static void logf(const char* fmt, ...) {
  char b[140];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  Serial.print(String("[") + (millis() / 1000) + "s] " + b + "\r\n");
}

static void setNote(const char* fmt, ...) {
  char b[60];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  noteMsg = b;
  uiDirty = true;
  logf("%s", b);
}

// ---------------------------------------------------------------------------
// Buzzer (non-blocking). Passive buzzers follow the frequency; active ones just sound.
// ---------------------------------------------------------------------------
static uint32_t buzzUntil = 0;

static void buzz(uint32_t hz, uint32_t ms) {
  ledcWriteTone(PIN_BUZZ, hz);
  buzzUntil = millis() + ms;
}

static void buzzTick() {
  if (buzzUntil && millis() >= buzzUntil) { ledcWriteTone(PIN_BUZZ, 0); buzzUntil = 0; }
}

// ---------------------------------------------------------------------------
// 1.3" OLED, 128x64. SH1106 (most 1.3" modules) or SSD1306; both in page mode.
// ---------------------------------------------------------------------------
class OledCanvas : public Adafruit_GFX {
 public:
  uint8_t addr = 0x3C;
  bool sh1106 = true;
  bool present = false;
  OledCanvas() : Adafruit_GFX(128, 64) { memset(buf, 0, sizeof buf); }
  void drawPixel(int16_t x, int16_t y, uint16_t color) override {
    if (x < 0 || x >= 128 || y < 0 || y >= 64) return;
    uint16_t i = x + (y >> 3) * 128;
    if (color) buf[i] |= (1 << (y & 7)); else buf[i] &= ~(1 << (y & 7));
  }
  void clear() { memset(buf, 0, sizeof buf); }
  void cmd(uint8_t c) { Wire.beginTransmission(addr); Wire.write(0x00); Wire.write(c); Wire.endTransmission(); }
  void begin() {
    Wire.beginTransmission(addr);
    present = (Wire.endTransmission() == 0);
    if (!present) return;
    static const uint8_t common[] = {0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40};
    for (uint8_t c : common) cmd(c);
    if (sh1106) { cmd(0xAD); cmd(0x8B); }                   // SH1106: internal DC-DC on
    else        { cmd(0x8D); cmd(0x14); cmd(0x20); cmd(0x02); } // SSD1306: charge pump on, page mode
    static const uint8_t tail[] = {0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF};
    for (uint8_t c : tail) cmd(c);
  }
  void display() {
    if (!present) return;
    uint8_t colOff = sh1106 ? 2 : 0;                        // SH1106 RAM is 132 wide, the glass starts at column 2
    for (uint8_t page = 0; page < 8; page++) {
      cmd(0xB0 | page);
      cmd(0x00 | (colOff & 0x0F));
      cmd(0x10 | (colOff >> 4));
      for (uint8_t chunk = 0; chunk < 8; chunk++) {
        Wire.beginTransmission(addr);
        Wire.write(0x40);
        Wire.write(&buf[page * 128 + chunk * 16], 16);
        Wire.endTransmission();
      }
    }
  }
 private:
  uint8_t buf[1024];
};
static OledCanvas oled;

// ---------------------------------------------------------------------------
// TM1637 4-digit display (bit-banged), final wiring only
// ---------------------------------------------------------------------------
#if ENABLE_TM1637
static const uint8_t SEG[10] = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F};
static void tmDly() { delayMicroseconds(6); }
static void tmStart() { digitalWrite(PIN_TM_CLK, HIGH); digitalWrite(PIN_TM_DIO, HIGH); tmDly(); digitalWrite(PIN_TM_DIO, LOW); tmDly(); digitalWrite(PIN_TM_CLK, LOW); tmDly(); }
static void tmStop()  { digitalWrite(PIN_TM_CLK, LOW); digitalWrite(PIN_TM_DIO, LOW); tmDly(); digitalWrite(PIN_TM_CLK, HIGH); tmDly(); digitalWrite(PIN_TM_DIO, HIGH); tmDly(); }
static void tmWrite(uint8_t b) {
  for (int i = 0; i < 8; i++) {
    digitalWrite(PIN_TM_CLK, LOW); digitalWrite(PIN_TM_DIO, (b >> i) & 1); tmDly();
    digitalWrite(PIN_TM_CLK, HIGH); tmDly();
  }
  digitalWrite(PIN_TM_CLK, LOW); pinMode(PIN_TM_DIO, INPUT_PULLUP); tmDly();   // ACK slot
  digitalWrite(PIN_TM_CLK, HIGH); tmDly();
  digitalWrite(PIN_TM_CLK, LOW); pinMode(PIN_TM_DIO, OUTPUT);
}
static void tmShow(const uint8_t d[4]) {
  tmStart(); tmWrite(0x40); tmStop();
  tmStart(); tmWrite(0xC0); for (int i = 0; i < 4; i++) tmWrite(d[i]); tmStop();
  tmStart(); tmWrite(0x88 | 0x04); tmStop();                                     // display on, brightness 4
}
static void tmNumber(int v, bool dashes) {
  uint8_t d[4];
  if (dashes) { for (auto& x : d) x = 0x40; }
  else { v = constrain(v, 0, 9999); d[0] = v >= 1000 ? SEG[v / 1000 % 10] : 0; d[1] = v >= 100 ? SEG[v / 100 % 10] : 0; d[2] = v >= 10 ? SEG[v / 10 % 10] : 0; d[3] = SEG[v % 10]; }
  tmShow(d);
}
#endif

// ---------------------------------------------------------------------------
// Radio: commands out (queued, retried, confirmed by the visor's status), status in
// ---------------------------------------------------------------------------
static StatusPacket vis;
static portMUX_TYPE visMux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t visRx = 0, visRxMs = 0;
static bool radioOk = false;

struct Cmd { uint8_t type, arg; };
static Cmd     q[8];
static uint8_t qh = 0, qt = 0;
static CommandPacket pend;
static bool     pendActive = false;
static uint8_t  pendTries = 0;
static uint32_t pendSentMs = 0, cmdOk = 0, cmdFail = 0, cmdDropped = 0;
static uint16_t cmdSeq = 0;

static void onRx(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len != (int)sizeof(StatusPacket)) return;
  if (memcmp(info->src_addr, VISOR_MAC, 6) != 0) return;
  StatusPacket st;
  memcpy(&st, data, sizeof st);
  if (st.magic != ST_MAGIC || st.ver != SP_VER) return;
  portENTER_CRITICAL(&visMux);
  vis = st;
  visRxMs = millis();
  portEXIT_CRITICAL(&visMux);
  visRx++;
}

static void radioInit() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_channel(SP_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_err_t e = esp_now_init();
  if (e != ESP_OK) { setNote("ESP-NOW init FAILED %d", (int)e); return; }
  esp_now_register_recv_cb(onRx);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, VISOR_MAC, 6);
  peer.channel = SP_CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  e = esp_now_add_peer(&peer);
  if (e != ESP_OK) { setNote("ESP-NOW add_peer FAILED %d", (int)e); return; }
  radioOk = true;
  logf("radio ready: ESP-NOW ch%d, my MAC %s", SP_CHANNEL, WiFi.macAddress().c_str());
}

// Queue a command. Volume and brightness are absolute values, so a newer one replaces a
// queued older one (a spinning encoder sends the latest value, not every step).
static void enqueue(uint8_t type, uint8_t arg) {
  if (qh != qt) {
    uint8_t last = (qh + 7) & 7;
    if (q[last].type == type && (type == CMD_VOLUME || type == CMD_BRIGHTNESS || type == CMD_BPM)) { q[last].arg = arg; return; }
  }
  uint8_t next = (qh + 1) & 7;
  if (next == qt) { cmdDropped++; return; }
  q[qh] = {type, arg};
  qh = next;
}

static void commandTick() {
  uint32_t now = millis();
  if (!pendActive) {
    if (qt == qh) return;
    memset(&pend, 0, sizeof pend);
    pend.magic = CP_MAGIC; pend.ver = SP_VER;
    pend.type = q[qt].type; pend.arg = q[qt].arg;
    pend.seq = ++cmdSeq;
    qt = (qt + 1) & 7;
    pendActive = true; pendTries = 0; pendSentMs = 0;
  }
  StatusPacket st; uint32_t stMs;
  portENTER_CRITICAL(&visMux);
  st = vis; stMs = visRxMs;
  portEXIT_CRITICAL(&visMux);
  if (pendSentMs && visRx && stMs >= pendSentMs && st.lastCmdSeq == pend.seq) {
    pendActive = false; cmdOk++;
    buzz(2600, 40);
    setNote("cmd #%u ok", (unsigned)pend.seq);
    return;
  }
  if (pendTries >= 4 && now - pendSentMs > (pend.type == CMD_BOOP ? 4000u : 300u)) {   // a boop animation blocks the visor's loop, so its confirmation is late
    pendActive = false; cmdFail++;
    buzz(250, 250);
    setNote("cmd #%u FAILED (no reply)", (unsigned)pend.seq);
    return;
  }
  if (radioOk && pendTries < 4 && (pendSentMs == 0 || now - pendSentMs >= 250)) {
    esp_now_send(VISOR_MAC, (const uint8_t*)&pend, sizeof pend);
    pendTries++;
    pendSentMs = now;
  }
}

// ---------------------------------------------------------------------------
// Local UI state (mirrors the visor; the visor is the authority)
// ---------------------------------------------------------------------------
static uint8_t uiVol = 50, uiBright = 255, uiMute = 0;
static uint8_t uiAudio = 0, uiBpm = 120;             // audiolink on/off and its BPM
static bool    synced = false;

static void syncFromVisor() {
  if (pendActive || qt != qh || !visRx) return;
  StatusPacket st; uint32_t stMs;
  portENTER_CRITICAL(&visMux);
  st = vis; stMs = visRxMs;
  portEXIT_CRITICAL(&visMux);
  if (millis() - stMs > 2000) return;
  if (uiVol != st.volume || uiBright != st.brightness || uiMute != st.mute || uiAudio != st.audiolink || (st.bpm && uiBpm != st.bpm)) {
    uiVol = st.volume; uiBright = st.brightness; uiMute = st.mute; uiAudio = st.audiolink; if (st.bpm) uiBpm = st.bpm; uiDirty = true;
  }
  synced = true;
}

// ---------------------------------------------------------------------------
// Keypad: rows are driven low one at a time, columns read with pull-ups
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Audiolink controls: tap tempo (key S12) and the TP223 touch pad (toggle)
// ---------------------------------------------------------------------------
static uint32_t tapMs[6];
static uint8_t  tapN = 0;

static void setAudiolink(uint8_t on) {
  uiAudio = on ? 1 : 0;
  enqueue(CMD_AUDIOLINK, uiAudio);
}

static void tapTempo() {
  uint32_t now = millis();
  if (tapN > 0 && now - tapMs[tapN - 1] > 2000) tapN = 0;            // a pause starts a new count
  if (tapN == 6) { for (int i = 1; i < 6; i++) tapMs[i - 1] = tapMs[i]; tapN = 5; }
  tapMs[tapN++] = now;
  if (tapN >= 3) {
    uint32_t avg = (tapMs[tapN - 1] - tapMs[0]) / (tapN - 1);
    uiBpm = (uint8_t)constrain((int)(60000UL / avg), 40, 220);
    if (!uiAudio) setAudiolink(1);                                    // tapping turns audiolink on
    enqueue(CMD_BPM, uiBpm);                                          // also re-aligns the beat on the visor
    setNote("tap tempo %u BPM", uiBpm);
  } else {
    setNote("tap %u of 3", tapN);
  }
}

static int touchPrev = 0;
static uint32_t touchMs = 0;
static void touchPoll() {
  int v = digitalRead(PIN_TOUCH);
  if (v != touchPrev && millis() - touchMs > 40) {
    touchMs = millis();
    touchPrev = v;
    if (v) { setAudiolink(!uiAudio); buzz(2200, 30); setNote("touch: audiolink %s", uiAudio ? "ON" : "off"); }
  }
}

static uint16_t kpPrev = 0, kpCand = 0;
static uint8_t  kpStable = 0;

static void keyAction(int r, int c) {
  int s = r * 4 + c + 1;                                   // S1..S16 assuming rows run along the printed S-rows
  logf("key raw row %d col %d = S%d", r + 1, c + 1, s);
  buzz(1800, 20);
  if (s >= 1 && s <= 4)       { enqueue(CMD_FACE_SET, s); setNote("S%d: face %d", s, s); }
  else if (s >= 5 && s <= 9)  { enqueue(CMD_BOOP, s - 5); setNote("S%d: boop %d", s, s - 5); }
  else if (s == 10)           { enqueue(CMD_BOOP, 255); setNote("S10: random boop"); }
  else if (s == 11)           { enqueue(CMD_FACE_NEXT, 0); setNote("S11: next face"); }
  else if (s == 12)           { tapTempo(); }
  else if (s == 13)           { uiBright = (uint8_t)max(0, (int)uiBright - 32); enqueue(CMD_BRIGHTNESS, uiBright); setNote("S13: bright %u", uiBright); }
  else if (s == 14)           { uiBright = (uint8_t)min(255, (int)uiBright + 32); enqueue(CMD_BRIGHTNESS, uiBright); setNote("S14: bright %u", uiBright); }
  else if (s == 15)           { uiVol = (uint8_t)max(0, (int)uiVol - 5); enqueue(CMD_VOLUME, uiVol); setNote("S15: vol %u", uiVol); }
  else if (s == 16)           { uiVol = (uint8_t)min(100, (int)uiVol + 5); enqueue(CMD_VOLUME, uiVol); setNote("S16: vol %u", uiVol); }
}

static void keypadScan() {
  uint16_t now = 0;
  for (int r = 0; r < KP_NR; r++) {
    pinMode(KP_ROWS[r], OUTPUT);
    digitalWrite(KP_ROWS[r], LOW);
    delayMicroseconds(30);
    for (int c = 0; c < KP_NC; c++) if (digitalRead(KP_COLS[c]) == LOW) now |= (1u << (r * 4 + c));
    pinMode(KP_ROWS[r], INPUT);
  }
  if (now == kpCand) { if (kpStable < 255) kpStable++; } else { kpCand = now; kpStable = 0; }
  if (kpStable == 2 && kpCand != kpPrev) {                  // stable for 2 scans (about 10 ms)
    uint16_t pressed = kpCand & ~kpPrev;
    for (int r = 0; r < KP_NR; r++) for (int c = 0; c < KP_NC; c++) if (pressed & (1u << (r * 4 + c))) keyAction(r, c);
    kpPrev = kpCand;
  }
}

// ---------------------------------------------------------------------------
// Rotary encoder (gray-code state table) and its push switch
// ---------------------------------------------------------------------------

#if UI_WRIST_V2
// ---------------------------------------------------------------------------
// Wrist v2: 16x2 HD44780 LCD (keypad shield), 4-bit, write-only (the shield's R/W is tied low).
// Pins as wired: RS 8, E 9, D4 11, D5 18, D6 39, D7 40. One changed character is
// written per loop pass, so the encoder is never starved by a full-screen refresh.
// ---------------------------------------------------------------------------
static const int LCD_RS = 8, LCD_E = 9;
static const int LCD_D[4] = {11, 18, 39, 40};     // AS WIRED 2026-11-15 (the first proposal said 10, 17, 39, 40)
static char   lcdWant[2][17] = {"                ", "                "};
static char   lcdHave[2][16];
static int8_t lcdRow = -1, lcdCol = -1;            // where the LCD's own cursor is (-1 = unknown)

static void lcdNibble(uint8_t n) {
  for (int i = 0; i < 4; i++) digitalWrite(LCD_D[i], (n >> i) & 1);
  digitalWrite(LCD_E, HIGH); delayMicroseconds(2);
  digitalWrite(LCD_E, LOW);  delayMicroseconds(60);
}
static void lcdByte(uint8_t v, bool data) { digitalWrite(LCD_RS, data ? HIGH : LOW); lcdNibble(v >> 4); lcdNibble(v & 15); }

static void lcdInit() {
  pinMode(LCD_RS, OUTPUT); pinMode(LCD_E, OUTPUT);
  digitalWrite(LCD_RS, LOW); digitalWrite(LCD_E, LOW);
  for (int i = 0; i < 4; i++) { pinMode(LCD_D[i], OUTPUT); digitalWrite(LCD_D[i], LOW); }
  delay(50);
  lcdNibble(3); delay(5); lcdNibble(3); delay(5); lcdNibble(3); delay(1); lcdNibble(2);   // into 4-bit mode from any state
  lcdByte(0x28, false);            // 2 lines, 5x8
  lcdByte(0x0C, false);            // display on, cursor off
  lcdByte(0x01, false); delay(3);  // clear
  lcdByte(0x06, false);            // left to right
  memset(lcdHave, ' ', sizeof lcdHave);
  lcdRow = lcdCol = -1;
}

static int lcdHoldPin = -1;                       // >= 0 while the `lcdhold` wiring check owns the LCD pins
static void lcdTick() {
  if (lcdHoldPin >= 0) return;
  for (int r = 0; r < 2; r++) for (int c = 0; c < 16; c++) {
    if (lcdWant[r][c] == lcdHave[r][c]) continue;
    if (lcdRow != r || lcdCol != c) lcdByte(0x80 | (r ? 0x40 : 0x00) | c, false);
    lcdByte((uint8_t)lcdWant[r][c], true);
    lcdHave[r][c] = lcdWant[r][c];
    lcdRow = r; lcdCol = c + 1;
    return;
  }
}

// ---------------------------------------------------------------------------
// Wrist v2 menu on the encoder. Turn = move between items; press = act:
//   Face / Boop / Brightness / Volume / BPM: press to start editing (marker '*'), turn to change, press to finish
//     (Boop: the second press plays the chosen animation).
//   Mute / Audiolink: press toggles.   Tap tempo: press on the beat, 3 or more times.
// Hold the knob for 1.5 s to restart the LCD (after a brown-out or a loose plug).
// ---------------------------------------------------------------------------
enum { MI_FACE, MI_BOOP, MI_BRIGHT, MI_VOL, MI_MUTE, MI_AUDIO, MI_BPM, MI_TAP, MI_N };
static const char* const MI_NAME[MI_N] = {"Face", "Boop", "Brightness", "Volume", "Mute", "Audiolink", "BPM", "Tap tempo"};
static uint8_t menuItem = 0, menuFace = 1, menuBoop = 0;
static bool    menuEdit = false;

static void menuStatus(StatusPacket& st, bool& up) {
  uint32_t stMs;
  portENTER_CRITICAL(&visMux);
  st = vis; stMs = visRxMs;
  portEXIT_CRITICAL(&visMux);
  up = visRx && millis() - stMs < 2000;
}

static void menuTurn(int dir) {
  StatusPacket st; bool up; menuStatus(st, up);
  int nf = (up && st.numFaces) ? st.numFaces : 4;
  int nb = (up && st.numBoops) ? st.numBoops : 5;             // index nb = "random"
  buzz(1200, 8);
  uiDirty = true;
  if (!menuEdit) { menuItem = (uint8_t)((menuItem + MI_N + dir) % MI_N); return; }
  switch (menuItem) {
    case MI_FACE:
      menuFace = (uint8_t)(((int)menuFace - 1 + nf + dir) % nf + 1);
      enqueue(CMD_FACE_SET, menuFace); setNote("menu: face %u", menuFace); break;
    case MI_BOOP:
      menuBoop = (uint8_t)(((int)menuBoop + (nb + 1) + dir) % (nb + 1)); break;
    case MI_BRIGHT:
      uiBright = (uint8_t)constrain((int)uiBright + dir * 5, 0, 255);
      enqueue(CMD_BRIGHTNESS, uiBright); break;
    case MI_VOL:
      uiVol = (uint8_t)constrain((int)uiVol + dir * 2, 0, 100);
      if (uiMute) { uiMute = 0; enqueue(CMD_MUTE, 0); }
      enqueue(CMD_VOLUME, uiVol); break;
    case MI_BPM:
      uiBpm = (uint8_t)constrain((int)uiBpm + dir, 40, 220);
      enqueue(CMD_BPM, uiBpm); break;
    default: break;
  }
}

static void menuPress() {
  StatusPacket st; bool up; menuStatus(st, up);
  int nb = (up && st.numBoops) ? st.numBoops : 5;
  uiDirty = true;
  switch (menuItem) {
    case MI_MUTE:  uiMute = !uiMute; enqueue(CMD_MUTE, uiMute); buzz(900, 40); setNote("menu: mute %d", uiMute); return;
    case MI_AUDIO: setAudiolink(!uiAudio); buzz(2200, 30); setNote("menu: audiolink %s", uiAudio ? "ON" : "off"); return;
    case MI_TAP:   buzz(1800, 15); tapTempo(); return;
    case MI_BOOP:
      if (menuEdit) { enqueue(CMD_BOOP, menuBoop >= nb ? 255 : menuBoop); setNote("menu: boop %s", menuBoop >= nb ? "random" : String(menuBoop).c_str()); }
      break;
    case MI_FACE:
      if (!menuEdit && up && st.face) menuFace = st.face;      // start from the face that is showing
      break;
    default: break;
  }
  menuEdit = !menuEdit;
  buzz(menuEdit ? 1600 : 1000, 25);
}

static void lcdCompose() {
  StatusPacket st; bool up; menuStatus(st, up);
  int nb = (up && st.numBoops) ? st.numBoops : 5;
  char l1[24], l2[24], v[20];
  switch (menuItem) {
    case MI_FACE:
      if (menuEdit)  snprintf(v, sizeof v, "%u of %u", menuFace, (up && st.numFaces) ? st.numFaces : 4);
      else if (up)   snprintf(v, sizeof v, "%u of %u", st.face, st.numFaces);
      else           snprintf(v, sizeof v, "no visor");
      break;
    case MI_BOOP:   if (menuBoop >= nb) snprintf(v, sizeof v, "random"); else snprintf(v, sizeof v, "animation %u", menuBoop); break;
    case MI_BRIGHT: snprintf(v, sizeof v, "%u", uiBright); break;
    case MI_VOL:    snprintf(v, sizeof v, "%u%s", uiVol, uiMute ? " MUTE" : ""); break;
    case MI_MUTE:   snprintf(v, sizeof v, "%s", uiMute ? "ON" : "off"); break;
    case MI_AUDIO:  if (uiAudio) snprintf(v, sizeof v, "ON %u bpm", uiBpm); else snprintf(v, sizeof v, "off"); break;
    case MI_BPM:    snprintf(v, sizeof v, "%u", uiBpm); break;
    default:        snprintf(v, sizeof v, "press x3: %u", uiBpm); break;
  }
  snprintf(l1, sizeof l1, "%c%-10s  %s", menuEdit ? '*' : '>', MI_NAME[menuItem], up ? "LNK" : "---");
  snprintf(l2, sizeof l2, " %-15s", v);
  memcpy(lcdWant[0], l1, 16); memcpy(lcdWant[1], l2, 16);
}
#endif  // UI_WRIST_V2

static uint8_t encPrev = 0;
static int8_t  encAcc = 0;
static int swPrev = HIGH;
static uint32_t swMs = 0;

static void encoderPoll() {
  static const int8_t table[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
  uint8_t cur = (digitalRead(PIN_ENC_CLK) << 1) | digitalRead(PIN_ENC_DT);
  encAcc += table[(encPrev << 2) | cur];
  encPrev = cur;
  int dir = 0;
  if (encAcc >= 4) { dir = +1; encAcc = 0; }
  else if (encAcc <= -4) { dir = -1; encAcc = 0; }
#if UI_WRIST_V2
  if (dir) menuTurn(dir);
  {
    static uint32_t downMs = 0;
    int sw2 = digitalRead(PIN_ENC_SW);
    if (sw2 != swPrev && millis() - swMs > 30) {
      swMs = millis();
      swPrev = sw2;
      if (sw2 == LOW) downMs = millis();
      else if (downMs) {                                      // act on release: short = menu press, long = restart the LCD
        if (millis() - downMs >= 1500) { lcdInit(); uiDirty = true; buzz(600, 120); setNote("LCD restarted"); }
        else menuPress();
        downMs = 0;
      }
    }
  }
  return;
#endif
  if (dir) {
    uiVol = (uint8_t)constrain((int)uiVol + dir * 2, 0, 100);
    if (uiMute) { uiMute = 0; enqueue(CMD_MUTE, 0); }       // turning the knob un-mutes
    enqueue(CMD_VOLUME, uiVol);                             // a newer value replaces a queued older one
    buzz(1200, 8);
    setNote("encoder: volume %u", uiVol);
  }
  int sw = digitalRead(PIN_ENC_SW);
  if (sw != swPrev && millis() - swMs > 30) {
    swMs = millis();
    swPrev = sw;
    if (sw == LOW) { uiMute = !uiMute; enqueue(CMD_MUTE, uiMute); buzz(900, 40); setNote("encoder press: mute %d", uiMute); }
  }
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------
static void drawScreens() {
  StatusPacket st; uint32_t stMs;
  portENTER_CRITICAL(&visMux);
  st = vis; stMs = visRxMs;
  portEXIT_CRITICAL(&visMux);
  bool up = visRx && millis() - stMs < 2000;

  oled.clear();
  oled.setTextColor(1);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.printf("CTRL %s ok%lu f%lu", up ? "UP" : "DOWN", (unsigned long)cmdOk, (unsigned long)cmdFail);
  oled.setCursor(0, 12);
  if (up) oled.printf("face %u/%u  boops %u", st.face, st.numFaces, st.boopsPlayed); else oled.print("face -  (no visor)");
  oled.setCursor(0, 22);
  oled.printf("bright %u", uiBright);
  oled.setCursor(0, 32);
  oled.printf("volume %u %s", uiVol, uiMute ? "MUTE" : "");
  oled.setCursor(0, 44);
  oled.printf("audiolink %s %u bpm", uiAudio ? "ON" : "off", uiBpm);
  oled.setCursor(0, 56);
  oled.print(noteMsg.substring(0, 21));
  oled.display();
#if UI_WRIST_V2
  lcdCompose();
#endif

#if ENABLE_TM1637
  tmNumber(uiAudio ? uiBpm : uiVol, uiMute && !uiAudio);          // BPM while audiolink is on, else the volume
#endif
}

// ---------------------------------------------------------------------------
// Wiring diagnostics: `watch` shows which GPIO changes when you touch something, `buzzfind`
// plays a tone on each candidate pin in turn. Candidates exclude the keypad, TM1637, OLED,
// USB (19/20), strapping and flash/PSRAM pins.
// ---------------------------------------------------------------------------
static const int CAND[] = {2, 6, 7, 13, 14, 15, 16, 21, 38, 47, 48};   // GPIO1 excluded: the MAX4466 mic is wired there and swings constantly
static const int NCAND = sizeof(CAND) / sizeof(CAND[0]);
static bool    watching = false;
static int8_t  candPrev[NCAND];
static uint32_t candMs[NCAND];

static void restorePins() {
  pinMode(PIN_ENC_CLK, INPUT_PULLUP);
  pinMode(PIN_ENC_DT, INPUT_PULLUP);
  pinMode(PIN_ENC_SW, INPUT_PULLUP);
  ledcAttach(PIN_BUZZ, 2000, 10);
  ledcWriteTone(PIN_BUZZ, 0);
}

static void watchSet(bool on) {
  if (on == watching) return;
  watching = on;
  if (on) {
    ledcDetach(PIN_BUZZ);
    for (int i = 0; i < NCAND; i++) { pinMode(CAND[i], INPUT_PULLUP); candPrev[i] = digitalRead(CAND[i]); candMs[i] = 0; }
    logf("WATCH on: touching/turning/pressing will print which GPIO changed (type `watch off` to stop)");
  } else {
    restorePins();
    logf("WATCH off");
  }
}

static void watchPoll() {
  if (!watching) return;
  uint32_t now = millis();
  for (int i = 0; i < NCAND; i++) {
    int v = digitalRead(CAND[i]);
    if (v != candPrev[i] && now - candMs[i] > 4) {
      candMs[i] = now;
      candPrev[i] = v;
      logf("WATCH GPIO%d -> %s", CAND[i], v ? "HIGH" : "LOW");
    }
  }
}

static void buzzFind() {
  watchSet(false);
  logf("BUZZFIND: 2 kHz on each candidate pin for 1 s. Listen and tell me which GPIO number sounds.");
  ledcDetach(PIN_BUZZ);
  for (int i = 0; i < NCAND; i++) {
    logf("BUZZFIND now GPIO%d", CAND[i]);
    tone(CAND[i], 2000, 900);
    delay(1100);
    noTone(CAND[i]);
    pinMode(CAND[i], INPUT_PULLUP);
  }
  restorePins();
  logf("BUZZFIND done");
}

// ---------------------------------------------------------------------------
// Serial commands
// ---------------------------------------------------------------------------
static void handleLine(String s) {
  s.trim();
  if (!s.length()) return;
  if (s.startsWith("cmd ")) {
    String a = s.substring(4); a.trim();
    String w = a, v = "";
    int sp = a.indexOf(' ');
    if (sp > 0) { w = a.substring(0, sp); v = a.substring(sp + 1); v.trim(); }
    if (w == "face")        enqueue(CMD_FACE_SET, (uint8_t)constrain(v.toInt(), 1, 255));
    else if (w == "next")   enqueue(CMD_FACE_NEXT, 0);
    else if (w == "boop")   enqueue(CMD_BOOP, v.length() ? (uint8_t)constrain(v.toInt(), 0, 254) : 255);
    else if (w == "bright") { uiBright = (uint8_t)constrain(v.toInt(), 0, 255); enqueue(CMD_BRIGHTNESS, uiBright); }
    else if (w == "vol")    { uiVol = (uint8_t)constrain(v.toInt(), 0, 100); enqueue(CMD_VOLUME, uiVol); }
    else if (w == "mute")   { uiMute = v.toInt() ? 1 : 0; enqueue(CMD_MUTE, uiMute); }
    else if (w == "audiolink") setAudiolink(v.toInt() ? 1 : 0);
    else if (w == "bpm")    { uiBpm = (uint8_t)constrain(v.toInt(), 40, 220); enqueue(CMD_BPM, uiBpm); }
    else if (w == "beat")   enqueue(CMD_BEAT, 0);
    else logf("cmd: face <n> | next | boop [idx] | bright <0-255> | vol <0-100> | mute <0|1> | audiolink <0|1> | bpm <40-220> | beat");
    return;
  }
#if UI_WRIST_V2
  if (s == "lcd") { lcdHoldPin = -1; lcdInit(); uiDirty = true; logf("LCD restarted, menu redrawn"); return; }
  if (s.startsWith("lcdhold")) {                     // wiring check with a meter: one LCD line HIGH (3.3 V), the other five LOW, until `lcd`
    static const char* const NM[6] = {"RS (shield D8) GPIO8", "E (shield D9) GPIO9", "D4 (shield D4) GPIO11", "D5 (shield D5) GPIO18", "D6 (shield D6) GPIO39", "D7 (shield D7) GPIO40"};
    static const int PN[6] = {8, 9, 11, 18, 39, 40};
    int n = constrain((int)s.substring(7).toInt(), 0, 5);
    lcdHoldPin = n;
    for (int i = 0; i < 6; i++) { pinMode(PN[i], OUTPUT); digitalWrite(PN[i], i == n ? HIGH : LOW); }
    logf("LCD HOLD %d: %s is HIGH (3.3 V), the other five LCD lines are LOW. Send `lcd` to resume.", n, NM[n]);
    return;
  }
#endif
  if (s == "watch" || s == "watch on") { watchSet(true); return; }
  if (s == "watch off") { watchSet(false); return; }
  if (s == "buzzfind") { buzzFind(); return; }
  if (s.startsWith("buzz")) {
    int sp = s.indexOf(' ');
    uint32_t hz = 1000, ms = 300;
    if (sp > 0) { String r = s.substring(sp + 1); r.trim(); hz = r.toInt(); int sp2 = r.indexOf(' '); if (sp2 > 0) ms = r.substring(sp2 + 1).toInt(); }
    logf("buzz %lu Hz %lu ms", (unsigned long)hz, (unsigned long)ms);
    buzz(hz, ms);
    return;
  }
  if (s.startsWith("oled")) {
    oled.sh1106 = !s.endsWith("1306");
    oled.begin();
    uiDirty = true;
    logf("OLED driver %s, panel %s", oled.sh1106 ? "SH1106" : "SSD1306", oled.present ? "found at 0x3C" : "NOT FOUND");
    return;
  }
  if (s == "s") {
    logf("vol %u bright %u mute %u | cmds ok %lu fail %lu dropped %lu | link %s | oled %s | radio %s q %u/%u pend %d tries %u",
         uiVol, uiBright, uiMute, (unsigned long)cmdOk, (unsigned long)cmdFail, (unsigned long)cmdDropped,
         (visRx && millis() - visRxMs < 2000) ? "UP" : "DOWN", oled.present ? (oled.sh1106 ? "SH1106" : "SSD1306") : "absent",
         radioOk ? "OK" : "NOT READY", (unsigned)qt, (unsigned)qh, (int)pendActive, (unsigned)pendTries);
    logf("encoder pins now: CLK GPIO%d=%d  DT GPIO%d=%d  SW GPIO%d=%d  (all 1 when idle with pull-ups)", PIN_ENC_CLK, digitalRead(PIN_ENC_CLK), PIN_ENC_DT, digitalRead(PIN_ENC_DT), PIN_ENC_SW, digitalRead(PIN_ENC_SW));
    return;
  }
  logf("unknown: cmd ... | buzz <hz> <ms> | buzzfind | watch [off] | oled 1106|1306 | s");
}

void setup() {
  Serial.setTxBufferSize(1024);
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);                              // never block on an unread USB port
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 2000) delay(10);
  logf("CONTROL SURFACE %s", UI_WRIST_V2 ? "wrist v2 (LCD menu, no keypad)" : (UI_PINS_TEST ? "v1 (TEST pins)" : "v1 (FINAL pins)"));

  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Wire.setTimeOut(50);
  oled.begin();
  logf("OLED %s", oled.present ? "found at 0x3C (SH1106 driver; use `oled 1306` if the picture is shifted/garbled)" : "NOT found at 0x3C");

  pinMode(PIN_ENC_CLK, INPUT_PULLUP);
  pinMode(PIN_ENC_DT, INPUT_PULLUP);
  pinMode(PIN_ENC_SW, INPUT_PULLUP);
  encPrev = (digitalRead(PIN_ENC_CLK) << 1) | digitalRead(PIN_ENC_DT);
  pinMode(PIN_TOUCH, INPUT_PULLDOWN);
  touchPrev = digitalRead(PIN_TOUCH);
#if !UI_WRIST_V2
  for (int c = 0; c < KP_NC; c++) pinMode(KP_COLS[c], INPUT_PULLUP);
  for (int r = 0; r < KP_NR; r++) pinMode(KP_ROWS[r], INPUT);
#endif
  ledcAttach(PIN_BUZZ, 2000, 10);
  ledcWriteTone(PIN_BUZZ, 0);
#if ENABLE_TM1637
  pinMode(PIN_TM_CLK, OUTPUT); pinMode(PIN_TM_DIO, OUTPUT);
#endif

#if UI_WRIST_V2
  lcdInit();
  memcpy(lcdWant[0], "Protogen wrist  ", 16); memcpy(lcdWant[1], "v2 starting...  ", 16);
  for (int i = 0; i < 32; i++) lcdTick();
#endif
  cmdSeq = (uint16_t)esp_random();
  radioInit();
  buzz(1500, 60);
}

void loop() {
  static uint32_t lastKp = 0, lastDraw = 0;
  static String line;
  uint32_t now = millis();

  if (watching) watchPoll(); else encoderPoll();
#if !UI_WRIST_V2
  if (now - lastKp >= 5) { lastKp = now; keypadScan(); }
#else
  (void)lastKp;
#endif
#if UI_WRIST_V2
  lcdTick();
#endif
  touchPoll();
  commandTick();
  buzzTick();
  syncFromVisor();

  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') { handleLine(line); line = ""; }
    else if (line.length() < 60) line += ch;
  }

  if ((uiDirty && now - lastDraw >= 120) || now - lastDraw >= 1000) {
    lastDraw = now;
    uiDirty = false;
    drawScreens();
  }
  delay(1);
}
