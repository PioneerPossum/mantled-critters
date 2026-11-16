/* ============================================================================
 * PROTOGEN VISOR BRING-UP TEST  (ESP32-S3 N16R8)       v2  written 2026-11-08
 * ============================================================================
 * Purpose: validate the visor wiring BEFORE the real firmware goes back on.
 * Pins come from protogen/visor-pin-table.md (HUB75 is NOT driven here; the
 * panels are tested by the normal proot-s3 sketch).
 *
 * DISPLAY (v2): one block of ~13 lines, each under 80 columns, redrawn IN PLACE
 * once a second with ANSI cursor codes (picocom, screen and minicom handle
 * them). The I2C bus is rescanned every second, so sensors show up as you wire
 * them, no `r` needed. Use `d` to switch to plain scrolling output if your
 * serial monitor shows stray escape characters (Arduino IDE monitor does).
 *
 * Needs NO extra libraries: sensors are read over raw I2C. If Pololu's
 * "VL53L1X" library is installed, ranging switches on automatically.
 *
 * Commands (type then Enter):
 *   h            show the command list
 *   r            force an immediate redraw / rescan
 *   a            pause / resume the once-per-second refresh
 *   d            toggle in-place dashboard / plain scrolling output
 *   c 100|400    set I2C clock in kHz
 *   f <0-100>    fan PWM duty in percent (25 kHz on GPIO13)
 *   v <ch> <us>  PCA9685 servo channel pulse in microseconds (600-2400)
 *   v off        turn every PCA9685 channel off
 * SAFETY: do not use `v` until the servo supply (V+) is connected, 5 V only
 * (6 V absolute max, never 12 V). Servos can stall-draw about 0.5 A each.
 * ============================================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <stdarg.h>

#if __has_include(<VL53L1X.h>)
  #include <VL53L1X.h>
  #define HAVE_POLOLU_VL53 1
#else
  #define HAVE_POLOLU_VL53 0
#endif

#ifndef ENABLE_INMP441
  #define ENABLE_INMP441 1   // set to 0 if the INMP441 is not wired yet
#endif
#if ENABLE_INMP441
  #include <ESP_I2S.h>
#endif

// ---------------------------------------------------------------------------
// Pins (visor-pin-table.md). IMPLEMENTED = in the real sketch, PROPOSED = plan.
// ---------------------------------------------------------------------------
static const int PIN_SDA       = 11;  // IMPLEMENTED (OLED)
static const int PIN_SCL       = 12;  // IMPLEMENTED (OLED)
static const int PIN_MIC       = 1;   // IMPLEMENTED  MAX4466 OUT, ADC1
static const int PIN_BTN       = 10;  // IMPLEMENTED  button between 3V3 and GPIO10
static const int PIN_BOOP      = 9;   // IMPLEMENTED  boop input, HIGH on boop
static const int PIN_FAN_PWM   = 13;  // PROPOSED     Noctua PWM, 25 kHz
static const int PIN_FAN_TACH  = 14;  // PROPOSED     Noctua tach (open collector)
static const int PIN_I2S_BCLK  = 17;  // PROPOSED     INMP441
static const int PIN_I2S_WS    = 21;  // PROPOSED     INMP441
static const int PIN_I2S_SD    = 38;  // PROPOSED     INMP441

// BME280 / BMP280 state. Defined before any function so the Arduino
// auto-prototype step can see the type.
struct Bme {
  uint8_t  addr;
  const char* label;
  bool     ok;
  uint8_t  id;
  uint16_t T1; int16_t T2, T3;
  uint8_t  H1, H3; int16_t H2, H4, H5; int8_t H6;
};

// ---------------------------------------------------------------------------
// Output plumbing: every status line is appended to one buffer (blk), then
// drawn in a single write. `note` shows the last command result or input event.
// ---------------------------------------------------------------------------
static String   blk;
static String   noteMsg;
static bool     dash        = true;    // in-place ANSI dashboard
static bool     autoPrint   = true;
static bool     forceRedraw = true;
static uint32_t lastStatus  = 0;

static void P(const char* fmt, ...) {
  char b[160];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  blk += b;
}

static void note(const char* fmt, ...) {
  char b[100];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  noteMsg = b;
  if (!dash) Serial.println(b);
  forceRedraw = true;
}

// ---------------------------------------------------------------------------
// I2C helpers
// ---------------------------------------------------------------------------
static bool i2cPing(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

static bool wrN(uint8_t a, uint8_t reg, const uint8_t* d, size_t n) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  for (size_t i = 0; i < n; i++) Wire.write(d[i]);
  return Wire.endTransmission() == 0;
}

static bool wr8(uint8_t a, uint8_t reg, uint8_t v) { return wrN(a, reg, &v, 1); }

static bool rdN(uint8_t a, uint8_t reg, uint8_t* b, size_t n) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint16_t)a, (size_t)n, true) != n) return false;
  for (size_t i = 0; i < n; i++) b[i] = Wire.read();
  return true;
}

static bool rd16(uint8_t a, uint16_t reg, uint8_t* b, size_t n) {  // 16-bit register index (VL53L1X)
  Wire.beginTransmission(a);
  Wire.write((uint8_t)(reg >> 8));
  Wire.write((uint8_t)(reg & 0xFF));
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint16_t)a, (size_t)n, true) != n) return false;
  for (size_t i = 0; i < n; i++) b[i] = Wire.read();
  return true;
}

// ---------------------------------------------------------------------------
// I2C scan (runs every redraw, so hot-wired sensors appear on their own)
// ---------------------------------------------------------------------------
static bool    found[128];
static uint8_t imuAddr = 0;

static void scanQuick() {
  for (uint8_t a = 1; a < 127; a++) found[a] = i2cPing(a);
  imuAddr = found[0x6A] ? 0x6A : (found[0x6B] ? 0x6B : 0);
}

static void scanLine() {
  P("I2C:");
  int n = 0;
  for (uint8_t a = 1; a < 127; a++) if (found[a]) { P(" %02X", a); n++; }
  if (!n) P(" none (check SDA11 SCL12 3V3 GND)");
  P("   miss:");
  int m = 0;
  if (!found[0x29]) { P(" VL53"); m++; }
  if (!found[0x3C]) { P(" OLED"); m++; }
  if (!found[0x40]) { P(" PCA"); m++; }
  if (!imuAddr)     { P(" IMU");  m++; }
  if (!found[0x76]) { P(" BMEin"); m++; }
  if (!found[0x77]) { P(" BMEout"); m++; }
  if (!m) P(" -");
  P("\n");
}

// ---------------------------------------------------------------------------
// BME280 / BMP280 (temperature and humidity; pressure is not decoded here)
// ---------------------------------------------------------------------------
static Bme bme[2] = {{0x76, "in"}, {0x77, "out"}};
static int32_t t_fine;

static void bmeInit(Bme& s) {
  s.ok = false;
  if (!found[s.addr]) return;
  if (!rdN(s.addr, 0xD0, &s.id, 1)) return;
  if (s.id != 0x60 && s.id != 0x58) return;        // 0x60 BME280, 0x58 BMP280 (no humidity!)
  uint8_t b[6], e[7], h1;
  if (!rdN(s.addr, 0x88, b, 6) || !rdN(s.addr, 0xA1, &h1, 1) || !rdN(s.addr, 0xE1, e, 7)) return;
  s.T1 = (uint16_t)((b[1] << 8) | b[0]);
  s.T2 = (int16_t)((b[3] << 8) | b[2]);
  s.T3 = (int16_t)((b[5] << 8) | b[4]);
  s.H1 = h1;
  s.H2 = (int16_t)((e[1] << 8) | e[0]);
  s.H3 = e[2];
  s.H4 = (int16_t)(((int8_t)e[3] << 4) | (e[4] & 0x0F));
  s.H5 = (int16_t)(((int8_t)e[5] << 4) | (e[4] >> 4));
  s.H6 = (int8_t)e[6];
  s.ok = true;
}

static int32_t bmeCompT(const Bme& s, int32_t adc_T) {
  int32_t v1 = ((((adc_T >> 3) - ((int32_t)s.T1 << 1))) * ((int32_t)s.T2)) >> 11;
  int32_t v2 = (((((adc_T >> 4) - ((int32_t)s.T1)) * ((adc_T >> 4) - ((int32_t)s.T1))) >> 12) * ((int32_t)s.T3)) >> 14;
  t_fine = v1 + v2;
  return (t_fine * 5 + 128) >> 8;                   // 0.01 degC
}

static uint32_t bmeCompH(const Bme& s, int32_t adc_H) {
  int32_t v = t_fine - 76800;
  v = (((((adc_H << 14) - (((int32_t)s.H4) << 20) - (((int32_t)s.H5) * v)) + 16384) >> 15) *
       (((((((v * (int32_t)s.H6) >> 10) * (((v * (int32_t)s.H3) >> 11) + 32768)) >> 10) + 2097152) *
         (int32_t)s.H2 + 8192) >> 14));
  v = v - (((((v >> 15) * (v >> 15)) >> 7) * (int32_t)s.H1) >> 4);
  if (v < 0) v = 0;
  if (v > 419430400) v = 419430400;
  return (uint32_t)(v >> 12);                       // %RH * 1024
}

static void bmeLine(Bme& s) {
  P("BME %-3s 0x%02X: ", s.label, s.addr);
  if (!found[s.addr]) { s.ok = false; P("not found\n"); return; }
  if (!s.ok) bmeInit(s);
  if (!s.ok) { P("chip id 0x%02X is not a BME280/BMP280\n", s.id); return; }
  wr8(s.addr, 0xF2, 0x01);                          // humidity x1
  wr8(s.addr, 0xF4, 0x25);                          // temp x1, press x1, forced mode
  delay(12);
  uint8_t d[8];
  if (!rdN(s.addr, 0xF7, d, 8)) { s.ok = false; P("read failed\n"); return; }
  int32_t rawT = ((int32_t)d[3] << 12) | ((int32_t)d[4] << 4) | (d[5] >> 4);
  int32_t t = bmeCompT(s, rawT);
  if (s.id == 0x60) {
    int32_t rawH = ((int32_t)d[6] << 8) | d[7];
    uint32_t h = bmeCompH(s, rawH);
    P("%.2f C  %.1f %%RH\n", t / 100.0, h / 1024.0);
  } else {
    P("%.2f C  chip 0x58 = BMP280, NO humidity\n", t / 100.0);
  }
}

// ---------------------------------------------------------------------------
// LSM6DS3 IMU (raw registers)
// ---------------------------------------------------------------------------
static bool imuReady = false;

static void imuLine() {
  if (!imuAddr) { imuReady = false; P("IMU      : not found\n"); return; }
  P("IMU 0x%02X: ", imuAddr);
  uint8_t who = 0;
  if (!rdN(imuAddr, 0x0F, &who, 1)) { P("WHO_AM_I read failed\n"); return; }
  if (who != 0x69 && who != 0x6A) { P("WHO_AM_I 0x%02X (want 69/6A)\n", who); return; }
  if (!imuReady) {
    wr8(imuAddr, 0x12, 0x44);                        // BDU + auto-increment
    wr8(imuAddr, 0x10, 0x40);                        // accel 104 Hz, +/-2 g
    wr8(imuAddr, 0x11, 0x40);                        // gyro 104 Hz, 250 dps
    delay(30);
    imuReady = true;
  }
  uint8_t t[2], g[6], a[6];
  if (!rdN(imuAddr, 0x20, t, 2) || !rdN(imuAddr, 0x22, g, 6) || !rdN(imuAddr, 0x28, a, 6)) {
    P("data read failed\n"); return;
  }
  auto s16 = [](const uint8_t* p) { return (int16_t)((p[1] << 8) | p[0]); };
  float ax = s16(a) * 0.000061f, ay = s16(a + 2) * 0.000061f, az = s16(a + 4) * 0.000061f;
  float gx = s16(g) * 0.00875f,  gy = s16(g + 2) * 0.00875f,  gz = s16(g + 4) * 0.00875f;
  float tc = 25.0f + s16(t) / 16.0f;
  P("%.0fC a=%.2f %.2f %.2f g=%.1f |a|=%.2f\n", tc, ax, ay, az, gx, sqrtf(ax * ax + ay * ay + az * az));
  (void)gy; (void)gz;
}

// ---------------------------------------------------------------------------
// VL53L1X
// ---------------------------------------------------------------------------
#if HAVE_POLOLU_VL53
static VL53L1X vl53;
static bool vlStarted = false;
#endif

static void vlLine() {
  if (!found[0x29]) {
#if HAVE_POLOLU_VL53
    vlStarted = false;
#endif
    P("VL53 0x29: not found\n");
    return;
  }
  P("VL53 0x29: ");
  uint8_t id[2] = {0, 0};
  if (!rd16(0x29, 0x010F, id, 2)) { P("model-id read failed\n"); return; }
  uint16_t model = (id[0] << 8) | id[1];
  P("id %04X %s", model, model == 0xEACC ? "ok" : "UNEXPECTED (want EACC)");
#if HAVE_POLOLU_VL53
  if (!vlStarted) {
    vl53.setBus(&Wire);
    vl53.setTimeout(200);
    if (vl53.init()) {
      vl53.setDistanceMode(VL53L1X::Short);
      vl53.setMeasurementTimingBudget(50000);
      vl53.startContinuous(50);
      vlStarted = true;
    } else {
      P("  library init failed\n");
      return;
    }
  }
  uint16_t mm = vl53.read(false);
  if (vl53.timeoutOccurred()) P("  range: timeout\n");
  else P("  range %u mm\n", mm);
#else
  P("  (no lib: ID only)\n");
#endif
}

// ---------------------------------------------------------------------------
// PCA9685 servo driver
// ---------------------------------------------------------------------------
static bool pcaReady = false;

static void pcaInit() {
  if (!found[0x40]) return;
  wr8(0x40, 0x00, 0x30);        // sleep + auto-increment
  wr8(0x40, 0xFE, 121);         // prescale for ~50 Hz at 25 MHz
  wr8(0x40, 0x00, 0x20);        // wake
  delay(5);
  wr8(0x40, 0x00, 0xA0);        // restart
  pcaReady = true;
}

static void pcaServo(int ch, int us) {
  if (!found[0x40]) { note("PCA9685 not found"); return; }
  if (!pcaReady) pcaInit();
  if (ch < 0 || ch > 15) { note("channel must be 0-15"); return; }
  us = constrain(us, 600, 2400);
  uint16_t off = (uint32_t)us * 4096 / 20000;
  uint8_t d[4] = {0, 0, (uint8_t)(off & 0xFF), (uint8_t)(off >> 8)};
  wrN(0x40, 0x06 + 4 * ch, d, 4);
  note("PCA9685 ch%d -> %d us", ch, us);
}

static void pcaAllOff() {
  if (!found[0x40]) { note("PCA9685 not found"); return; }
  if (!pcaReady) pcaInit();
  wr8(0x40, 0xFD, 0x10);        // ALL_LED_OFF_H: full off
  note("PCA9685 all channels off");
}

static void pcaLine() {
  if (!found[0x40]) { pcaReady = false; P("PCA  0x40: not found\n"); return; }
  uint8_t m1 = 0;
  if (!rdN(0x40, 0x00, &m1, 1)) { P("PCA  0x40: MODE1 read failed\n"); return; }
  P("PCA  0x40: MODE1=%02X  (v <ch> <us> moves a servo)\n", m1);
}

// ---------------------------------------------------------------------------
// Analog mic MAX4466 on GPIO1
// ---------------------------------------------------------------------------
static void micLine() {
  const int N = 400;
  int mn = 4095, mx = 0;
  uint32_t sum = 0;
  for (int i = 0; i < N; i++) {
    int v = analogRead(PIN_MIC);
    sum += v;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
    delayMicroseconds(200);
  }
  P("MIC1 GPIO1: mean %.0f min %d max %d p2p %d (base 1945)\n", (float)sum / N, mn, mx, mx - mn);
}

// ---------------------------------------------------------------------------
// INMP441 I2S mic
// ---------------------------------------------------------------------------
#if ENABLE_INMP441
static I2SClass i2s;
static bool i2sOk = false;
#endif

static void i2sLine() {
#if ENABLE_INMP441
  if (!i2sOk) { P("MIC2 I2S  : driver not started\n"); return; }
  static int32_t buf[256];
  size_t got = i2s.readBytes((char*)buf, sizeof(buf)) / 4;
  if (!got) { P("MIC2 I2S  : no data (17 21 38, 3V3, L/R to GND)\n"); return; }
  int64_t acc = 0; int32_t peak = 0;
  for (size_t i = 0; i < got; i++) {
    int32_t s = buf[i] >> 14;                        // 32-bit slot, 24-bit data -> ~18-bit
    acc += (int64_t)s * s;
    if (abs(s) > peak) peak = abs(s);
  }
  P("MIC2 I2S  : rms %.0f peak %ld %s\n", sqrt((double)acc / got), (long)peak, peak == 0 ? "(all zero: SD/LR?)" : "");
#else
  P("MIC2 I2S  : disabled\n");
#endif
}

// ---------------------------------------------------------------------------
// Button and boop input (polled, edges go to the note line)
// ---------------------------------------------------------------------------
static int  lastBtn = -1, lastBoop = -1;
static uint32_t btnEvents = 0, boopEvents = 0;

static void pollInputs() {
  int b = digitalRead(PIN_BTN), p = digitalRead(PIN_BOOP);
  if (lastBtn >= 0 && b != lastBtn)   { btnEvents++;  note("%lus button GPIO10 %s", millis() / 1000, b ? "HIGH (pressed)" : "LOW"); }
  if (lastBoop >= 0 && p != lastBoop) { boopEvents++; note("%lus boop GPIO9 %s", millis() / 1000, p ? "HIGH (boop)" : "LOW"); }
  lastBtn = b; lastBoop = p;
}

static void inputsLine() {
  P("BTN GPIO10=%d ev%lu   BOOP GPIO9=%d ev%lu\n",
    digitalRead(PIN_BTN), (unsigned long)btnEvents, digitalRead(PIN_BOOP), (unsigned long)boopEvents);
}

// ---------------------------------------------------------------------------
// Fan: 25 kHz PWM on GPIO13, tach on GPIO14
// ---------------------------------------------------------------------------
static volatile uint32_t tachPulses = 0;
static int fanDuty = 50;
static uint32_t tachWindowStart = 0;
static uint32_t lastRpm = 0;

static void IRAM_ATTR tachISR() { tachPulses++; }

static void fanSet(int pct) {
  fanDuty = constrain(pct, 0, 100);
  ledcWrite(PIN_FAN_PWM, (fanDuty * 255) / 100);
  note("fan duty -> %d %%", fanDuty);
}

static void fanLine() {
  uint32_t now = millis();
  uint32_t p;
  noInterrupts(); p = tachPulses; tachPulses = 0; interrupts();
  uint32_t dt = now - tachWindowStart;
  tachWindowStart = now;
  if (dt) lastRpm = (uint32_t)((uint64_t)p * 30000ULL / dt);   // 2 pulses per revolution
  P("FAN GPIO13/14: %d%%  %lu rpm%s\n", fanDuty, (unsigned long)lastRpm,
    (fanDuty > 0 && lastRpm == 0) ? "  (check 12V, tach wire)" : "");
}

// ---------------------------------------------------------------------------
// Status block, commands, setup, loop
// ---------------------------------------------------------------------------
static void render() {
  blk = "";
  P("BRING-UP t=%lus heap=%uK chip=%.0fC  (picocom quit: Ctrl-A Ctrl-X)\n",
    millis() / 1000, (unsigned)(ESP.getFreeHeap() / 1024), temperatureRead());
  scanQuick();
  scanLine();
  bmeLine(bme[0]);
  bmeLine(bme[1]);
  imuLine();
  vlLine();
  pcaLine();
  micLine();
  i2sLine();
  inputsLine();
  fanLine();
  P("> %s\n", noteMsg.length() ? noteMsg.c_str() : "ready");
  P("r redraw  a pause  d dash  c 100|400  f 0-100  v ch us | v off  h help");  // no final newline: keeps the screen from scrolling

  // Terminals in raw mode (picocom, screen) do NOT turn a bare LF into CR+LF,
  // so every line break must be sent as "\r\n" or lines drift to the right.
  if (dash) {
    blk.replace("\n", "\033[K\r\n");                // clear to end of each line, then CR+LF
    blk += "\033[K";
    Serial.print("\033[H");                         // cursor home, redraw in place
    Serial.print(blk);
    Serial.print("\033[J");                         // clear anything left below
  } else {
    blk.replace("\n", "\r\n");
    Serial.print(blk);
    Serial.print("\r\n\r\n");
  }
}

static void handleLine(String s) {
  s.trim();
  if (!s.length()) return;
  char c = s.charAt(0);
  String rest = s.substring(1); rest.trim();
  if (c == 'h') note("commands are listed on the bottom line");
  else if (c == 'r') { memset(found, 0, sizeof found); imuReady = false; pcaReady = false; bmeInit(bme[0]); bmeInit(bme[1]); forceRedraw = true; note("rescanned"); }
  else if (c == 'a') { autoPrint = !autoPrint; note("refresh %s", autoPrint ? "running" : "PAUSED (press a to resume)"); }
  else if (c == 'd') { dash = !dash; if (dash) Serial.print("\033[2J\033[H"); note("display: %s", dash ? "in-place dashboard" : "plain scrolling"); }
  else if (c == 'c') { int k = rest.toInt(); if (k == 100 || k == 400) { Wire.setClock(k * 1000UL); note("I2C clock %d kHz", k); } else note("use: c 100 or c 400"); }
  else if (c == 'f') fanSet(rest.toInt());
  else if (c == 'v') {
    if (rest.startsWith("off")) pcaAllOff();
    else { int sp = rest.indexOf(' '); if (sp > 0) pcaServo(rest.substring(0, sp).toInt(), rest.substring(sp + 1).toInt()); else note("use: v <ch> <us>  or  v off"); }
  } else note("unknown command, try h");
}

void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);
  Serial.print("\033[2J\033[H");
  Serial.println("PROTOGEN VISOR BRING-UP TEST v2 (HUB75 panels are NOT driven here)");
  Serial.println("3V3 pin should read about 3.3 V on a meter.");
  delay(800);

  analogReadResolution(12);
  pinMode(PIN_MIC, INPUT);
  pinMode(PIN_BTN, INPUT_PULLDOWN);
  pinMode(PIN_BOOP, INPUT_PULLDOWN);
  pinMode(PIN_FAN_TACH, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_FAN_TACH), tachISR, FALLING);
  ledcAttach(PIN_FAN_PWM, 25000, 8);
  ledcWrite(PIN_FAN_PWM, (fanDuty * 255) / 100);
  tachWindowStart = millis();

  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Wire.setTimeOut(50);

#if ENABLE_INMP441
  i2s.setPins(PIN_I2S_BCLK, PIN_I2S_WS, -1, PIN_I2S_SD);
  i2sOk = i2s.begin(I2S_MODE_STD, 16000, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
  if (!i2sOk) noteMsg = "INMP441: i2s.begin() failed";
#endif
}

void loop() {
  static String line;

  pollInputs();

  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') { handleLine(line); line = ""; }
    else if (line.length() < 40) line += ch;
  }

  if (forceRedraw || (autoPrint && millis() - lastStatus >= 1000)) {
    forceRedraw = false;
    lastStatus = millis();
    render();
  }
  delay(5);
}
