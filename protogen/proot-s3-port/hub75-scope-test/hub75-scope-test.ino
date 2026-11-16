// HUB75 scope / test-pattern firmware for the visor S3 (2026-11-14). NOT the face firmware.
// Mode W (wire test, default): no display library. Each HUB75 pin is a plain GPIO square wave with its OWN frequency,
//   so a scope at the S3 terminal and at the panel end of the ribbon shows which wire is which and whether it arrives.
// Mode P (pattern): the normal HUB75 DMA driver shows red / green / blue / white / bars at low brightness.
// Mode H<n> (hold): pin index n held HIGH, all others LOW (for a multimeter). Serial: w, p, h0..h12, ? . Mode is saved and the board restarts.
#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

struct P { const char* name; uint8_t gpio; uint8_t hub; };
static const P PINS[] = {
  {"R1", 4, 1}, {"G1", 5, 2}, {"B1", 6, 3}, {"R2", 7, 5}, {"G2", 15, 6}, {"B2", 16, 7},
  {"A", 18, 9}, {"B", 8, 10}, {"C", 3, 11}, {"D", 42, 12}, {"CLK", 41, 13}, {"LAT", 40, 14}, {"OE", 39, 15}}   /* 2026-11-14 TEST: OE on GPIO39 (where Gil's wire is); the design says GPIO2 */;
static const int NP = sizeof(PINS) / sizeof(PINS[0]);
static const uint32_t TICK_US = 250;             // pin k toggles every (k+1) ticks: f = 2000/(k+1) Hz

Preferences prefs;
char mode = 'w'; int holdIdx = 0;
MatrixPanel_I2S_DMA* display = nullptr;
uint32_t lastPrint = 0, lastStep = 0; int step = 0;

void i2cScan() {                                  // I2C bus of the face firmware: SDA GPIO11, SCL GPIO12
  pinMode(11, INPUT); pinMode(12, INPUT); delay(2);
  Serial.printf("[I2C] idle levels SDA(GPIO11)=%d SCL(GPIO12)=%d (both must be 1 = pulled up)\r\n", digitalRead(11), digitalRead(12));
  Wire.begin(11, 12, 100000UL); Wire.setTimeOut(50); int n = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) { Wire.beginTransmission(a); if (Wire.endTransmission() == 0) { Serial.printf("[I2C] device at 0x%02X\r\n", a); n++; } }
  Serial.printf("[I2C] scan done, %d device(s)\r\n", n);
}

void printHelp() {
  Serial.printf("\r\n[TEST] mode=%c%s   commands: w (wire test)  p (pattern)  h<0-12> (hold one pin high)  ?\r\n", mode, mode == 'h' ? String(holdIdx).c_str() : "");
  if (mode == 'w') for (int k = 0; k < NP; k++)
    Serial.printf("[TEST]  %-3s GPIO%-2u HUB75 pin %-2u  %4.0f Hz square, 0-3.3 V\r\n", PINS[k].name, PINS[k].gpio, PINS[k].hub, 2000.0 / (k + 1));
  if (mode == 'h') Serial.printf("[TEST]  %s (GPIO%u, HUB75 pin %u) is HIGH, all other HUB75 pins LOW\r\n", PINS[holdIdx].name, PINS[holdIdx].gpio, PINS[holdIdx].hub);
}

void setMode(char m, int idx) { prefs.putChar("mode", m); prefs.putInt("idx", idx); Serial.println("[TEST] switching, restarting..."); Serial.flush(); delay(100); ESP.restart(); }

void setup() {
  Serial.setTxBufferSize(2048); Serial.begin(115200); Serial.setTxTimeoutMs(0);
  prefs.begin("hubtest", false);
  mode = prefs.getChar("mode", 'w'); holdIdx = constrain(prefs.getInt("idx", 0), 0, NP - 1);
  delay(300);
  if (mode == 'p') {
    HUB75_I2S_CFG::i2s_pins pins = {4, 5, 6, 7, 15, 16, 18, 8, 3, 42, -1, 40, 39, 41};
    HUB75_I2S_CFG cfg(64, 32, 2, pins);
    cfg.double_buff = false; cfg.i2sspeed = HUB75_I2S_CFG::HZ_20M; cfg.min_refresh_rate = 120;   // same as the face firmware
    int spd = prefs.getInt("spd", 20); bool ph = prefs.getBool("ph", true);
    cfg.i2sspeed = spd == 8 ? HUB75_I2S_CFG::HZ_8M : spd == 16 ? HUB75_I2S_CFG::HZ_16M : HUB75_I2S_CFG::HZ_20M; cfg.clkphase = ph;
    Serial.printf("[TEST] pixel clock = %d MHz, clkphase = %d (face firmware: 20 MHz, 1)\r\n", spd, (int)ph);
    int drv = prefs.getInt("drv", 0); cfg.driver = (HUB75_I2S_CFG::shift_driver)drv;   // 0 SHIFTREG (face firmware default), 1 FM6124, 2 FM6126A, 3 ICN2038S, 4 MBI5124
    Serial.printf("[TEST] panel driver chip setting = %d\r\n", drv);
    display = new MatrixPanel_I2S_DMA(cfg);
    bool ok = display->begin();
    display->setBrightness8(60);
    Serial.printf("[TEST] HUB75 driver begin() = %s, free heap %u\r\n", ok ? "OK" : "FAILED", (unsigned)ESP.getFreeHeap());
  } else if (mode == 'z') {                       // idle: every HUB75 pin high-impedance, nothing is driven into the panel
    for (int k = 0; k < NP; k++) pinMode(PINS[k].gpio, INPUT);
    Serial.println("[TEST] idle: all HUB75 pins are inputs (not driven). Send p to start the patterns.");
  } else {
    for (int k = 0; k < NP; k++) { pinMode(PINS[k].gpio, OUTPUT); digitalWrite(PINS[k].gpio, (mode == 'h' && k == holdIdx) ? HIGH : LOW); }
  }
  if (mode == 'm') {                              // static test: all data HIGH, slow clock, latch, row 0, OE LOW. Everything is DC afterwards.
    auto pin = [](const char* n) { for (int k = 0; k < NP; k++) if (!strcmp(PINS[k].name, n)) return (int)PINS[k].gpio; return -1; };
    digitalWrite(pin("OE"), HIGH);
    for (int k = 0; k < 6; k++) digitalWrite(PINS[k].gpio, HIGH);          // R1 G1 B1 R2 G2 B2 = 1
    for (int i = 0; i < 256; i++) { digitalWrite(pin("CLK"), HIGH); delayMicroseconds(500); digitalWrite(pin("CLK"), LOW); delayMicroseconds(500); }
    digitalWrite(pin("LAT"), HIGH); delay(2); digitalWrite(pin("LAT"), LOW);
    digitalWrite(pin("OE"), LOW);                                           // A B C D stay LOW = row 0 (and row 16)
    Serial.println("[TEST] static: 256 ones clocked in, latched, OE LOW, row 0. Expect one full white line per panel half.");
  }
  printHelp();
  i2cScan();
}

void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'w' || c == 'p' || c == 'm' || c == 'z') setMode(c, 0);
    else if (c == 'h') { delay(30); setMode('h', constrain((int)Serial.parseInt(), 0, NP - 1)); }
    else if (c == '?') printHelp();
    else if (c == 's') i2cScan();
    else if (c == '8' || c == '6' || c == '9') { prefs.putInt("spd", c == '8' ? 8 : c == '6' ? 16 : 20); prefs.putInt("drv", 0); setMode('p', 0); }   // 8 = 8 MHz, 6 = 16 MHz, 9 = 20 MHz
    else if (c == 'k') { prefs.putBool("ph", !prefs.getBool("ph", true)); setMode('p', 0); }
    else if (c >= '0' && c <= '5') { prefs.putInt("drv", c - '0'); setMode('p', 0); }   // single key: panel driver chip + pattern mode
    else if (c == 'd') { delay(30); prefs.putInt("drv", constrain((int)Serial.parseInt(), 0, 6)); setMode('p', 0); }
  }
  uint32_t now = millis();
  if (mode == 'w') {
    static uint32_t next = micros(); static uint32_t tick = 0;
    if ((int32_t)(micros() - next) >= 0) {
      next += TICK_US; tick++;
      for (int k = 0; k < NP; k++) if (tick % (k + 1) == 0) digitalWrite(PINS[k].gpio, !digitalRead(PINS[k].gpio));
    }
  } else if (mode == 'p' && display && now - lastStep >= 2000) {
    lastStep = now;
    static const char* names[] = {"RED", "GREEN", "BLUE", "WHITE", "BARS"};
    if (step < 4) display->fillScreenRGB888(step == 0 || step == 3 ? 255 : 0, step == 1 || step == 3 ? 255 : 0, step == 2 || step == 3 ? 255 : 0);
    else for (int x = 0; x < 128; x++) display->drawFastVLine(x, 0, 32, display->color565((x / 16) & 1 ? 255 : 0, (x / 16) & 2 ? 255 : 0, (x / 16) & 4 ? 255 : 0));
    Serial.printf("[TEST] pattern %s\r\n", names[step]);
    step = (step + 1) % 5;
  }
  if (now - lastPrint >= 5000) { lastPrint = now; Serial.printf("[TEST] alive mode=%c up=%lus heap=%u\r\n", mode, (unsigned long)(now / 1000), (unsigned)ESP.getFreeHeap()); }
}
