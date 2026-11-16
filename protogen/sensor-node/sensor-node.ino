/* ============================================================================
 * PROTOGEN SENSOR NODE  (ESP32-S3 N16R8)                 v1  written 2026-11-08
 * ============================================================================
 * Proof of concept: read the visor sensors on THIS board and beam them over
 * ESP-NOW (2.4 GHz, no router) to the visor controller, which drives the LED
 * matrix. Derived from bringup-test v3: same live in-place dashboard, same raw
 * I2C sensor code, plus the radio stream.
 *
 *   sensor node (this)  --ESP-NOW 20 Hz-->  visor controller (proot-s3-wireless)
 *
 * The packet format is in sensor_packet.h (identical copy on both sides).
 * Boop and button travel as COUNTERS: the receiver acts when the counter changes,
 * so a dropped packet cannot swallow a boop. Mic and boop are sampled
 * independently every tick, so a boop never starves the mic stream.
 *
 * Commands (type then Enter): r redraw | a pause | d dash/plain | w stream on/off
 *   b inject a synthetic boop | n inject a synthetic button press
 *   c 100|400 I2C kHz | f <0-100> fan duty | v <ch> <us> / v off  PCA9685 servo
 * SAFETY: do not use `v` until servo V+ is 5 V (never 12 V).
 * ============================================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <stdarg.h>
#include "sensor_packet.h"

#if __has_include(<VL53L1X.h>)
  #include <VL53L1X.h>
  #define HAVE_POLOLU_VL53 1
#else
  #define HAVE_POLOLU_VL53 0
#endif

#ifndef ENABLE_INMP441
  #define ENABLE_INMP441 1
#endif
#if ENABLE_INMP441
  #include <ESP_I2S.h>
#endif

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
// Visor controller (S3 #1) Wi-Fi station MAC. ESP-NOW sends unicast to it so the
// send callback reports real delivery (ack) results.
static const uint8_t RX_MAC[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x01};
static const int     MIC_BASELINE = 1890;   // quiet-room mean measured on this board 2026-11-08
static const uint32_t FAST_MS = 50;         // 20 Hz packets
static const uint32_t SLOW_MS = 1000;       // slow sensors + dashboard

static const int PIN_SDA       = 11;
static const int PIN_SCL       = 12;
static const int PIN_MIC       = 1;
static const int PIN_BTN       = 10;
static const int PIN_BOOP      = 9;
static const int PIN_FAN_PWM   = 13;
static const int PIN_FAN_TACH  = 14;
static const int PIN_I2S_BCLK  = 17;
static const int PIN_I2S_WS    = 21;
static const int PIN_I2S_SD    = 38;

struct Bme {
  uint8_t  addr;
  const char* label;
  bool     ok;
  uint8_t  id;
  uint16_t T1; int16_t T2, T3;
  uint8_t  H1, H3; int16_t H2, H4, H5; int8_t H6;
  bool     fresh;      // last read succeeded
  int16_t  t100;       // 0.01 degC
  uint16_t h10;        // 0.1 %RH
};

// ---------------------------------------------------------------------------
// Output plumbing: P() appends to whichever String `out` points at
// ---------------------------------------------------------------------------
static String   blk, lnScan, lnBmeIn, lnBmeOut, lnImu, lnVl, lnPca;
static String*  out = &blk;
static String   noteMsg;
static bool     dash        = true;
static bool     autoPrint   = true;
static bool     forceRedraw = true;

static void P(const char* fmt, ...) {
  char b[160];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  *out += b;
}

static void note(const char* fmt, ...) {
  char b[100];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  noteMsg = b;
  if (!dash) Serial.print(String(b) + "\r\n");
  forceRedraw = true;
}

// ---------------------------------------------------------------------------
// I2C helpers
// ---------------------------------------------------------------------------
static bool i2cPing(uint8_t a) { Wire.beginTransmission(a); return Wire.endTransmission() == 0; }

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

static bool rd16(uint8_t a, uint16_t reg, uint8_t* b, size_t n) {
  Wire.beginTransmission(a);
  Wire.write((uint8_t)(reg >> 8));
  Wire.write((uint8_t)(reg & 0xFF));
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint16_t)a, (size_t)n, true) != n) return false;
  for (size_t i = 0; i < n; i++) b[i] = Wire.read();
  return true;
}

// ---------------------------------------------------------------------------
// I2C scan
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
// BME280 / BMP280
// ---------------------------------------------------------------------------
static Bme bme[2] = {{0x76, "in"}, {0x77, "out"}};
static int32_t t_fine;

static void bmeInit(Bme& s) {
  s.ok = false;
  if (!found[s.addr]) return;
  if (!rdN(s.addr, 0xD0, &s.id, 1)) return;
  if (s.id != 0x60 && s.id != 0x58) return;
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
  return (t_fine * 5 + 128) >> 8;
}

static uint32_t bmeCompH(const Bme& s, int32_t adc_H) {
  int32_t v = t_fine - 76800;
  v = (((((adc_H << 14) - (((int32_t)s.H4) << 20) - (((int32_t)s.H5) * v)) + 16384) >> 15) *
       (((((((v * (int32_t)s.H6) >> 10) * (((v * (int32_t)s.H3) >> 11) + 32768)) >> 10) + 2097152) *
         (int32_t)s.H2 + 8192) >> 14));
  v = v - (((((v >> 15) * (v >> 15)) >> 7) * (int32_t)s.H1) >> 4);
  if (v < 0) v = 0;
  if (v > 419430400) v = 419430400;
  return (uint32_t)(v >> 12);
}

static void bmeLine(Bme& s) {
  s.fresh = false;
  P("BME %-3s 0x%02X: ", s.label, s.addr);
  if (!found[s.addr]) { s.ok = false; P("not found\n"); return; }
  if (!s.ok) bmeInit(s);
  if (!s.ok) { P("chip id 0x%02X is not a BME280/BMP280\n", s.id); return; }
  wr8(s.addr, 0xF2, 0x01);
  wr8(s.addr, 0xF4, 0x25);
  delay(12);
  uint8_t d[8];
  if (!rdN(s.addr, 0xF7, d, 8)) { s.ok = false; P("read failed\n"); return; }
  int32_t rawT = ((int32_t)d[3] << 12) | ((int32_t)d[4] << 4) | (d[5] >> 4);
  int32_t t = bmeCompT(s, rawT);
  s.t100 = (int16_t)t;
  if (s.id == 0x60) {
    int32_t rawH = ((int32_t)d[6] << 8) | d[7];
    uint32_t h = bmeCompH(s, rawH);
    s.h10 = (uint16_t)((h * 10) / 1024);
    s.fresh = true;
    P("%.2f C  %.1f %%RH\n", t / 100.0, h / 1024.0);
  } else {
    s.h10 = 0;
    s.fresh = true;
    P("%.2f C  chip 0x58 = BMP280, NO humidity\n", t / 100.0);
  }
}

// ---------------------------------------------------------------------------
// LSM6DS3 IMU
// ---------------------------------------------------------------------------
static bool imuReady = false, imuValid = false;
static int16_t imuMg[3] = {0, 0, 0};

static void imuLine() {
  imuValid = false;
  if (!imuAddr) { imuReady = false; P("IMU      : not found\n"); return; }
  P("IMU 0x%02X: ", imuAddr);
  uint8_t who = 0;
  if (!rdN(imuAddr, 0x0F, &who, 1)) { P("WHO_AM_I read failed\n"); return; }
  if (who != 0x69 && who != 0x6A) { P("WHO_AM_I 0x%02X (want 69/6A)\n", who); return; }
  if (!imuReady) {
    wr8(imuAddr, 0x12, 0x44);
    wr8(imuAddr, 0x10, 0x40);
    wr8(imuAddr, 0x11, 0x40);
    delay(30);
    imuReady = true;
  }
  uint8_t t[2], g[6], a[6];
  if (!rdN(imuAddr, 0x20, t, 2) || !rdN(imuAddr, 0x22, g, 6) || !rdN(imuAddr, 0x28, a, 6)) {
    P("data read failed\n"); return;
  }
  auto s16 = [](const uint8_t* p) { return (int16_t)((p[1] << 8) | p[0]); };
  float ax = s16(a) * 0.000061f, ay = s16(a + 2) * 0.000061f, az = s16(a + 4) * 0.000061f;
  float gx = s16(g) * 0.00875f;
  float tc = 25.0f + s16(t) / 16.0f;
  imuMg[0] = (int16_t)(ax * 1000); imuMg[1] = (int16_t)(ay * 1000); imuMg[2] = (int16_t)(az * 1000);
  imuValid = true;
  P("%.0fC a=%.2f %.2f %.2f g=%.1f |a|=%.2f\n", tc, ax, ay, az, gx, sqrtf(ax * ax + ay * ay + az * az));
}

// ---------------------------------------------------------------------------
// VL53L1X
// ---------------------------------------------------------------------------
static uint16_t vlRange = 0xFFFF;
static bool vlValid = false;
#if HAVE_POLOLU_VL53
static VL53L1X vl53;
static bool vlStarted = false;
#endif

static void vlLine() {
  vlValid = false; vlRange = 0xFFFF;
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
    } else { P("  library init failed\n"); return; }
  }
  uint16_t mm = vl53.read(false);
  if (vl53.timeoutOccurred()) P("  range: timeout\n");
  else { vlRange = mm; vlValid = true; P("  range %u mm\n", mm); }
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
  wr8(0x40, 0x00, 0x30);
  wr8(0x40, 0xFE, 121);
  wr8(0x40, 0x00, 0x20);
  delay(5);
  wr8(0x40, 0x00, 0xA0);
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
  wr8(0x40, 0xFD, 0x10);
  note("PCA9685 all channels off");
}

static void pcaLine() {
  if (!found[0x40]) { pcaReady = false; P("PCA  0x40: not found\n"); return; }
  uint8_t m1 = 0;
  if (!rdN(0x40, 0x00, &m1, 1)) { P("PCA  0x40: MODE1 read failed\n"); return; }
  P("PCA  0x40: MODE1=%02X  (v <ch> <us> moves a servo)\n", m1);
}

// ---------------------------------------------------------------------------
// Fast sensors (every FAST_MS): mic1 burst, I2S mic, boop/button counters
// ---------------------------------------------------------------------------
static uint16_t mic1Lvl = 0, mic1Pk = 0;
static uint32_t micStatSum = 0, micStatN = 0;
static int      micStatMin = 4095, micStatMax = 0;

static uint16_t mic2Rms = 0;
static bool     mic2Ok = false, mic2WasActive = false;
static uint32_t mic2Drops = 0, mic2DropsBoop = 0;

static int      lastBtn = 0, lastBoop = 0;
static uint8_t  boopCount = 0, btnCount = 0;
static uint32_t boopEdges = 0, btnEdges = 0;
static uint32_t synthBoopUntil = 0;
static uint32_t btnLastMs = 0;

#if ENABLE_INMP441
static I2SClass i2s;
static bool i2sOk = false;
#endif

static void sampleFast() {
  // --- MAX4466 burst: 32 samples over about 3 ms ---
  const int N = 32;
  uint32_t dev = 0; int pk = 0;
  for (int i = 0; i < N; i++) {
    int v = analogRead(PIN_MIC);
    int d = abs(v - MIC_BASELINE);
    dev += d;
    if (d > pk) pk = d;
    micStatSum += v;
    if (v < micStatMin) micStatMin = v;
    if (v > micStatMax) micStatMax = v;
    delayMicroseconds(60);
  }
  micStatN += N;
  mic1Lvl = (uint16_t)(dev / N);
  mic1Pk  = (uint16_t)min(pk, 65535);

  // --- boop and button: count rising edges, independent of the mics ---
  int p = digitalRead(PIN_BOOP);
  if (p != lastBoop) {
    boopEdges++;
    if (p) boopCount++;
    note("%lus boop GPIO9 %s", millis() / 1000, p ? "HIGH (boop)" : "LOW");
  }
  lastBoop = p;

  int b = digitalRead(PIN_BTN);
  if (b != lastBtn && millis() - btnLastMs > 40) {
    btnLastMs = millis();
    btnEdges++;
    if (b) btnCount++;
    note("%lus button GPIO10 %s", millis() / 1000, b ? "HIGH (pressed)" : "LOW");
    lastBtn = b;
  }

  // --- INMP441: never gated on boop; dropouts are counted, especially during a boop ---
#if ENABLE_INMP441
  if (i2sOk) {
    static int32_t ibuf[128];
    size_t got = i2s.readBytes((char*)ibuf, sizeof(ibuf)) / 4;
    if (got) {
      int64_t acc = 0;
      for (size_t i = 0; i < got; i++) { int32_t s = ibuf[i] >> 14; acc += (int64_t)s * s; }
      double rms = sqrt((double)acc / got);
      mic2Rms = (uint16_t)min(rms, 65535.0);
      mic2Ok = true;
      if (mic2Rms > 0) mic2WasActive = true;
    } else {
      mic2Rms = 0; mic2Ok = false;
      mic2Drops++;
      if ((lastBoop || millis() < synthBoopUntil) && mic2WasActive) mic2DropsBoop++;
    }
  }
#endif
}

// ---------------------------------------------------------------------------
// Radio: ESP-NOW unicast to the visor controller
// ---------------------------------------------------------------------------
static bool     streaming = true;
static uint16_t seqNo = 0;
static uint32_t txCalls = 0, txErr = 0, txAck = 0, txNack = 0;
static uint32_t lastSendMs = 0, maxGapMs = 0, windowMaxGap = 0;
static bool     radioOk = false;

static void onSent(const esp_now_send_info_t* info, esp_now_send_status_t status) {
  (void)info;
  if (status == ESP_NOW_SEND_SUCCESS) txAck++; else txNack++;
}

// ---- Visor status in, commands out ----
static StatusPacket visSt;
static portMUX_TYPE visMux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t visRx = 0, visRxMs = 0;
static CommandPacket pendCmd;
static bool     pendActive = false;
static uint8_t  pendTries = 0;
static uint32_t pendSentMs = 0;
static uint16_t cmdSeq = 0;
static uint32_t cmdOk = 0, cmdFail = 0;

static void onRx(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len != (int)sizeof(StatusPacket)) return;
  if (memcmp(info->src_addr, RX_MAC, 6) != 0) return;
  StatusPacket st;
  memcpy(&st, data, sizeof st);
  if (st.magic != ST_MAGIC || st.ver != SP_VER) return;
  portENTER_CRITICAL(&visMux);
  visSt = st;
  visRxMs = millis();
  portEXIT_CRITICAL(&visMux);
  visRx++;
}

// Queue one command; commandTick() sends it, retries up to 4 times, and waits for the
// visor's status to echo this seq (its lastCmdSeq) as the confirmation.
static void sendCommand(uint8_t type, uint8_t arg) {
  memset(&pendCmd, 0, sizeof pendCmd);
  pendCmd.magic = CP_MAGIC;
  pendCmd.ver = SP_VER;
  pendCmd.type = type;
  pendCmd.arg = arg;
  pendCmd.seq = ++cmdSeq;
  pendActive = true;
  pendTries = 0;
  pendSentMs = 0;
}

static void commandTick() {
  if (!pendActive) return;
  uint32_t now = millis();
  StatusPacket st; uint32_t stMs;
  portENTER_CRITICAL(&visMux);
  st = visSt; stMs = visRxMs;
  portEXIT_CRITICAL(&visMux);
  if (pendSentMs && visRx && stMs >= pendSentMs && st.lastCmdSeq == pendCmd.seq) {
    pendActive = false; cmdOk++;
    note("cmd #%u confirmed by the visor", (unsigned)pendCmd.seq);
    return;
  }
  if (pendTries >= 4 && now - pendSentMs > (pendCmd.type == CMD_BOOP ? 4000u : 300u)) {   // a boop animation blocks the visor's loop, so its confirmation is late
    pendActive = false; cmdFail++;
    note("cmd #%u FAILED: no confirmation from the visor", (unsigned)pendCmd.seq);
    return;
  }
  if (pendTries < 4 && (pendSentMs == 0 || now - pendSentMs >= 250)) {
    esp_now_send(RX_MAC, (const uint8_t*)&pendCmd, sizeof pendCmd);
    pendTries++;
    pendSentMs = now;
  }
}

static void handleCmd(String a) {
  a.trim();
  String w = a, v = "";
  int sp = a.indexOf(' ');
  if (sp > 0) { w = a.substring(0, sp); v = a.substring(sp + 1); v.trim(); }
  if (w == "face")        sendCommand(CMD_FACE_SET, (uint8_t)constrain(v.toInt(), 1, 255));
  else if (w == "next")   sendCommand(CMD_FACE_NEXT, 0);
  else if (w == "boop")   sendCommand(CMD_BOOP, v.length() ? (uint8_t)constrain(v.toInt(), 0, 254) : 255);
  else if (w == "bright") sendCommand(CMD_BRIGHTNESS, (uint8_t)constrain(v.toInt(), 0, 255));
  else if (w == "vol")    sendCommand(CMD_VOLUME, (uint8_t)constrain(v.toInt(), 0, 100));
  else if (w == "mute")   sendCommand(CMD_MUTE, v.toInt() ? 1 : 0);
  else note("cmd: face <n> | next | boop [idx] | bright <0-255> | vol <0-100> | mute <0|1>");
}

static void radioInit() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_channel(SP_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) { note("ESP-NOW init FAILED"); return; }
  esp_now_register_send_cb(onSent);
  esp_now_register_recv_cb(onRx);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, RX_MAC, 6);
  peer.channel = SP_CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) { note("ESP-NOW add_peer FAILED"); return; }
  radioOk = true;
}

static void sendPacket() {
  if (!streaming || !radioOk) return;
  SensorPacket p;
  memset(&p, 0, sizeof p);
  p.magic = SP_MAGIC;
  p.ver = SP_VER;
  uint8_t f = 0;
  if (lastBoop || millis() < synthBoopUntil) f |= SPF_BOOP;
  if (lastBtn)                                f |= SPF_BTN;
  if (vlValid)                                f |= SPF_VL;
  f |= SPF_MIC1;
  if (mic2Ok)                                 f |= SPF_MIC2;
  if (bme[0].fresh)                           f |= SPF_BMEIN;
  if (bme[1].fresh)                           f |= SPF_BMEOUT;
  if (imuValid)                               f |= SPF_IMU;
  p.flags = f;
  p.seq = seqNo++;
  p.ms = millis();
  p.boopCount = boopCount;
  p.btnCount = btnCount;
  p.mic1 = mic1Lvl;
  p.mic1pk = mic1Pk;
  p.mic2 = mic2Rms;
  p.rangeMm = vlValid ? vlRange : 0xFFFF;
  p.tIn = bme[0].fresh ? bme[0].t100 : 0;
  p.tOut = bme[1].fresh ? bme[1].t100 : 0;
  p.hIn = bme[0].fresh ? bme[0].h10 : 0;
  p.hOut = bme[1].fresh ? bme[1].h10 : 0;
  p.ax = imuMg[0]; p.ay = imuMg[1]; p.az = imuMg[2];

  uint32_t now = millis();
  if (lastSendMs) { uint32_t gap = now - lastSendMs; if (gap > windowMaxGap) windowMaxGap = gap; }
  lastSendMs = now;
  txCalls++;
  if (esp_now_send(RX_MAC, (const uint8_t*)&p, sizeof p) != ESP_OK) txErr++;
}

// ---------------------------------------------------------------------------
// Fan: 25 kHz PWM on GPIO13, tach on GPIO14
// ---------------------------------------------------------------------------
static volatile uint32_t tachPulses = 0;
static int fanDuty = 50;
static uint32_t tachWindowStart = 0, lastRpm = 0;

static void IRAM_ATTR tachISR() { tachPulses++; }

static void fanSet(int pct) {
  fanDuty = constrain(pct, 0, 100);
  ledcWrite(PIN_FAN_PWM, (fanDuty * 255) / 100);
  note("fan duty -> %d %%", fanDuty);
}

// ---------------------------------------------------------------------------
// Dashboard (drawn from cached values, never re-reads sensors)
// ---------------------------------------------------------------------------
static void sampleSlow() {
  scanQuick();
  lnScan = "";    out = &lnScan;    scanLine();
  lnBmeIn = "";   out = &lnBmeIn;   bmeLine(bme[0]);
  lnBmeOut = "";  out = &lnBmeOut;  bmeLine(bme[1]);
  lnImu = "";     out = &lnImu;     imuLine();
  lnVl = "";      out = &lnVl;      vlLine();
  lnPca = "";     out = &lnPca;     pcaLine();
  out = &blk;
  maxGapMs = windowMaxGap; windowMaxGap = 0;
}

static void draw() {
  blk = "";
  out = &blk;
  P("NODE t=%lus heap=%uK chip=%.0fC  picocom quit: Ctrl-A Ctrl-X\n",
    millis() / 1000, (unsigned)(ESP.getFreeHeap() / 1024), temperatureRead());
  uint32_t done = txAck + txNack;
  P("STREAM %s ch%d seq %u  ack %lu nack %lu err %lu  (%lu%% ok)  gap<=%lums\n",
    !radioOk ? "RADIO-FAIL" : (streaming ? "ON " : "OFF"), SP_CHANNEL, (unsigned)seqNo,
    (unsigned long)txAck, (unsigned long)txNack, (unsigned long)txErr,
    (unsigned long)(done ? (txAck * 100UL / done) : 0), (unsigned long)maxGapMs);
  {
    StatusPacket st; uint32_t stMs;
    portENTER_CRITICAL(&visMux);
    st = visSt; stMs = visRxMs;
    portEXIT_CRITICAL(&visMux);
    if (visRx && millis() - stMs < 2000)
      P("VISOR face %u/%u bright %u vol %u%s boops %u  cmds %lu ok %lu fail\n", st.face, st.numFaces, st.brightness,
        st.volume, st.mute ? " MUTE" : "", st.boopsPlayed, (unsigned long)cmdOk, (unsigned long)cmdFail);
    else
      P("VISOR: no status (visor off, link down, or older firmware)\n");
  }
  blk += lnScan; blk += lnBmeIn; blk += lnBmeOut; blk += lnImu; blk += lnVl; blk += lnPca;
  if (micStatN) {
    P("MIC1 GPIO1: mean %.0f min %d max %d p2p %d  lvl %u pk %u\n",
      (float)micStatSum / micStatN, micStatMin, micStatMax, micStatMax - micStatMin, (unsigned)mic1Lvl, (unsigned)mic1Pk);
  } else P("MIC1 GPIO1: waiting\n");
  micStatSum = 0; micStatN = 0; micStatMin = 4095; micStatMax = 0;
#if ENABLE_INMP441
  P("MIC2 I2S  : rms %u %s drops %lu (during boop %lu)\n", (unsigned)mic2Rms, mic2Ok ? "ok" : "NO DATA",
    (unsigned long)mic2Drops, (unsigned long)mic2DropsBoop);
#else
  P("MIC2 I2S  : disabled\n");
#endif
  P("BTN GPIO10=%d n%u  BOOP GPIO9=%d n%u  edges %lu/%lu\n", lastBtn, (unsigned)btnCount, lastBoop,
    (unsigned)boopCount, (unsigned long)btnEdges, (unsigned long)boopEdges);

  uint32_t now = millis(), p;
  noInterrupts(); p = tachPulses; tachPulses = 0; interrupts();
  uint32_t dt = now - tachWindowStart;
  tachWindowStart = now;
  if (dt > 500) lastRpm = (uint32_t)((uint64_t)p * 30000ULL / dt);
  P("FAN GPIO13/14: %d%%  %lu rpm\n", fanDuty, (unsigned long)lastRpm);

  P("> %s\n", noteMsg.length() ? noteMsg.c_str() : "ready");
  P("r a d w b n f v c | cmd face|next|boop|bright|vol|mute <n>");

  if (dash) {
    blk.replace("\n", "\033[K\r\n");
    blk += "\033[K";
    Serial.print("\033[H");
    Serial.print(blk);
    Serial.print("\033[J");
  } else {
    blk.replace("\n", "\r\n");
    Serial.print(blk);
    Serial.print("\r\n\r\n");
  }
}

static void handleLine(String s) {
  s.trim();
  if (!s.length()) return;
  if (s.startsWith("cmd ")) { handleCmd(s.substring(4)); return; }   // multi-letter commands first (f... would hit the fan)
  char c = s.charAt(0);
  String rest = s.substring(1); rest.trim();
  if (c == 'h') note("commands are listed on the bottom line");
  else if (c == 'r') { memset(found, 0, sizeof found); imuReady = false; pcaReady = false; bmeInit(bme[0]); bmeInit(bme[1]); sampleSlow(); note("rescanned"); }
  else if (c == 'a') { autoPrint = !autoPrint; note("refresh %s", autoPrint ? "running" : "PAUSED (press a to resume)"); }
  else if (c == 'd') { dash = !dash; if (dash) Serial.print("\033[2J\033[H"); note("display: %s", dash ? "in-place dashboard" : "plain scrolling"); }
  else if (c == 'w') { streaming = !streaming; note("radio stream %s", streaming ? "ON" : "OFF"); }
  else if (c == 'b') { boopCount++; synthBoopUntil = millis() + 1000; note("synthetic boop injected (count %u)", (unsigned)boopCount); }
  else if (c == 'n') { btnCount++; note("synthetic button press injected (count %u)", (unsigned)btnCount); }
  else if (c == 'c') { int k = rest.toInt(); if (k == 100 || k == 400) { Wire.setClock(k * 1000UL); note("I2C clock %d kHz", k); } else note("use: c 100 or c 400"); }
  else if (c == 'f') fanSet(rest.toInt());
  else if (c == 'v') {
    if (rest.startsWith("off")) pcaAllOff();
    else { int sp = rest.indexOf(' '); if (sp > 0) pcaServo(rest.substring(0, sp).toInt(), rest.substring(sp + 1).toInt()); else note("use: v <ch> <us>  or  v off"); }
  } else note("unknown command, try h");
}

void setup() {
  // The USB-serial/JTAG port blocks writes when the host is attached but nothing is
  // reading (e.g. picocom closed): with the default timeout each dashboard frame stalled
  // the loop ~2 s and the radio stream dropped to ~0.5 packets/s. So: never wait on the
  // port, and make the TX ring big enough to hold one whole frame when someone IS reading.
  Serial.setTxBufferSize(2048);     // must be set before begin()
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);
  Serial.print("\033[2J\033[H");
  Serial.print("PROTOGEN SENSOR NODE v1: streaming sensors to the visor over ESP-NOW\r\n");
  delay(500);

  analogReadResolution(12);
  pinMode(PIN_MIC, INPUT);
  pinMode(PIN_BTN, INPUT_PULLDOWN);
  pinMode(PIN_BOOP, INPUT_PULLDOWN);
  lastBtn = digitalRead(PIN_BTN);
  lastBoop = digitalRead(PIN_BOOP);
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
  i2s.setTimeout(15);
  if (!i2sOk) noteMsg = "INMP441: i2s.begin() failed";
#endif

  cmdSeq = (uint16_t)esp_random();      // random start so a rebooted sender is never mistaken for a repeat
  radioInit();
  sampleSlow();
}

void loop() {
  static uint32_t lastFast = 0, lastSlow = 0;
  static String line;
  uint32_t now = millis();

  if (now - lastFast >= FAST_MS) {
    lastFast = (now - lastFast > 4 * FAST_MS) ? now : lastFast + FAST_MS;   // skip, do not burst, after a long stall
    sampleFast();
    sendPacket();
  }

  if (autoPrint && now - lastSlow >= SLOW_MS) {
    lastSlow = now;
    sampleSlow();
    forceRedraw = true;
  }

  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') { handleLine(line); line = ""; }
    else if (line.length() < 40) line += ch;
  }

  commandTick();

  if (forceRedraw) {
    forceRedraw = false;
    draw();
  }
  delay(1);
}
