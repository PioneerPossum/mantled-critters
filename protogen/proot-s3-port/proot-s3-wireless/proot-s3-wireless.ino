/* ============================================================================
 * PROOTOS - ESP32 PROTOGEN MATRIX CONTROLLER
 * ============================================================================
 * NOTE BEFORE USING:
 * - Use VSCode and PlatformIO to flash the ESP32. Make sure to change
 *   platformio.ini configs to match the specific board you are using.
 * - Tested on a FireBeetle ESP32-E and a standard ESP32 devboard.
 * - Reference wiring guide: https://github.com/mrcodetastic/ESP32-HUB75-MatrixPanel-DMA
 *   (Note: This code uses a modified wiring scheme—check pin mappings in drawfaces.h!)
 *
 * Based on: https://github.com/PanickingLynx/ProtOpenSource
 * Author:   Katxe
 * Telegram: @Katxe
 * Bsky:     @katxe.bsky.social
 * ============================================================================ */

// ============================================================================
// DISPLAY & PROJECT DEPENDENCIES
// ============================================================================

// Uncomment this if your matrices display errors like color shifts or flickering pixels
// (Common with matrices bought from AliExpress)
#define PxMATRIX_SPI_FREQUENCY 19900000

// Enables double buffering for seamless image transitions on the matrix
#define double_buffer true

#include <Arduino.h>
#include "functions/misc.h"
#include "functions/drawfaces.h"
#include "user/faces.h"
#include "user/boops.h"

// ---- WIRELESS SENSOR STREAM (proof of concept, 2026-11-08) ----
// Mic level, boop and face-button events arrive over ESP-NOW from the sensor
// board (sensor-node.ino). This is a COPY of proot-s3; the original is untouched.
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "sensor_packet.h"

// Bluetooth module (WIP - uncomment when reimplementing Bluetooth)
// #include "bt/bluetooth.h"


// ============================================================================
// HARDWARE PIN DEFINITIONS
// ============================================================================

#define MIC_PIN       1  // Connect the OUT pin of the MAX4466 microphone here
#define BOOP_PIN      9  // Detects booping & confirms BT pairing (connect sensor positive output relative to GND)
#define FACE_SELECTOR 10  // Cycles through protogen emotions (wire a button connected to 3.3V)


// ============================================================================
// MICROPHONE & TIMING CONFIGURATION
// ============================================================================

// Baseline analog reading from the microphone in a quiet environment.
// To calibrate: temporarily set this to 0, print `microphoneLevel` to Serial,
// and observe the resting value on the Serial Plotter with no sound.
const int baseline = 1945;

// Default frame delay (in ms) used when holding static sprites in `holdFace()`
const int defaultFaceDelay = 100;


// ============================================================================
// STATE & TIMING VARIABLES
// ============================================================================

unsigned long lastDrawMillis    = 0;    // Timestamp of the last frame redraw (`faceDelay`)
unsigned long lastMouthMillis   = 0;    // Timestamp when the current speaking sprite was chosen
unsigned long mouthHoldDuration = 0;    // Duration (in ms) to hold the current speaking sprite
unsigned long blinkMillis       = 0;    // Timestamp of the last blink animation
unsigned long nextBlinkInterval = 4000; // Random interval (in ms) until the next blink
unsigned long prevButton        = 0;    // Used to debounce the face selector button

const uint32_t* currentSpeakSprite = nullptr; // Currently locked-in speaking sprite

uint8_t currFace   = 1;                 // Currently active face index in `faceSelector[]` (1-indexed)
Face    activeFace = *faceSelector[0];  // Cached active Face struct (only updated when `selectFaceISR` triggers)
bool    booped     = false;             // Flag set by boopISR when the boop sensor triggers


// ============================================================================
// FUNCTION PREDEFINES
// ============================================================================

void isSpeaking(const uint32_t* speakSprite);
void isIdle();
void holdFace(int time, int offset_x, int offset_y, const uint32_t image[], bool overlay, bool mirror, FaceModifier* modifier);
void IRAM_ATTR boopISR();
void IRAM_ATTR selectFaceISR();


// ============================================================================
// WIRELESS SENSOR RECEIVER (ESP-NOW)
// ============================================================================
// Only packets from the sensor board's MAC with the right magic/version are used.
// Mic: the sender's mean |sample - its baseline| is scaled by WL_MIC_GAIN so it
// lands on the scale of the face thresholds (talk 300 / loud 600 / yell 1500).
// If the link is stale (> WL_STALE_MS) the mic level is 0 (face idles), NOT the
// local ADC: an unconnected GPIO1 floats around 57 and would read as shouting.
// Boop and button arrive as COUNTERS; any change fires one event, so a lost
// packet cannot swallow a boop.

// 2026-11-15: two wrist boards are accepted: the original (…:02, screw-terminal base) and the wrist v2 board (…:03).
// Status packets go back to whichever one was heard last (wl_peerMac).
static const uint8_t  WL_SENDER_MACS[2][6] = {{0x02, 0x00, 0x00, 0x00, 0x00, 0x02}, {0x02, 0x00, 0x00, 0x00, 0x00, 0x03}};
static uint8_t        wl_peerMac[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x03};
#define WL_STALE_MS   500
#define WL_MIC_GAIN   3
#define WL_STATS_MS   2000

static portMUX_TYPE   wl_mux = portMUX_INITIALIZER_UNLOCKED;
static SensorPacket   wl_pkt;                       // latest valid packet
static volatile uint32_t wl_lastRxMs = 0;
static volatile uint32_t wl_rx = 0, wl_bad = 0, wl_foreign = 0, wl_lost = 0;
static volatile bool     wl_boopPending = false, wl_btnPending = false;
static volatile uint32_t wl_boopsRx = 0, wl_btnsRx = 0;
static bool     wl_ok = false, wl_haveFirst = false;
static uint16_t wl_lastSeq = 0;
static uint8_t  wl_lastBoopCount = 0, wl_lastBtnCount = 0;
static uint32_t wl_boopsPlayed = 0, wl_facesChanged = 0, wl_lastStatsMs = 0, wl_lastRxSnap = 0;

// ---- commands from the control board (ring queue: filled by the Wi-Fi task, drained in loop()) ----
static CommandPacket     wl_cmdQ[8];
static volatile uint8_t  wl_cmdHead = 0, wl_cmdTail = 0;
static uint16_t          wl_lastCmdSeq = 0;
static bool              wl_haveCmd = false;
static volatile uint32_t wl_cmdsRx = 0, wl_cmdsDup = 0, wl_cmdsDropped = 0;

// ---- Link quality: the wrist module acknowledges (MAC level) every status packet we send it ----
static volatile uint16_t wl_txHist = 0;      // last 16 send results, 1 = acknowledged
static volatile uint32_t wl_txSent = 0;      // total status sends completed
static volatile uint32_t wl_rxRate = 0;      // sensor-stream packets per second (0 when no sensor node streams)

static void onWirelessSent(const esp_now_send_info_t*, esp_now_send_status_t status) {
  wl_txHist = (uint16_t)((wl_txHist << 1) | (status == ESP_NOW_SEND_SUCCESS ? 1 : 0));
  wl_txSent++;
}
static uint8_t           wl_brightness = 255, wl_volume = 50, wl_mute = 0;
static const Boop*       wl_forcedBoop = nullptr;       // set by CMD_BOOP with a specific index
static uint16_t          wl_statusSeq = 0;
static uint32_t          wl_lastStatusMs = 0;
static bool              wl_statusNow = false;

// ---- AUDIOLINK: rainbow colour wash on the face, pulsing and shifting on the beat ----
// The BPM comes from the control board (tap tempo or a typed value) for now; automatic BPM
// from the INMP441 mics plugs into the same wl_bpm / wl_beatEpoch later.
static volatile bool     wl_audiolink = false;
static volatile uint8_t  wl_bpm = 120;
static volatile uint32_t wl_beatEpoch = 0;                // millis() at a downbeat

static uint16_t hsv565(uint16_t h, uint8_t sat, uint8_t val) {
  uint8_t region = h / 60;
  uint8_t rem = (uint8_t)(((h % 60) * 255) / 60);
  uint8_t p = (val * (255 - sat)) >> 8;
  uint8_t q = (val * (255 - ((sat * rem) >> 8))) >> 8;
  uint8_t t = (val * (255 - ((sat * (255 - rem)) >> 8))) >> 8;
  uint8_t r, g, b;
  switch (region) {
    case 0:  r = val; g = t;   b = p;   break;
    case 1:  r = q;   g = val; b = p;   break;
    case 2:  r = p;   g = val; b = t;   break;
    case 3:  r = p;   g = q;   b = val; break;
    case 4:  r = t;   g = p;   b = val; break;
    default: r = val; g = p;   b = q;   break;
  }
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

class RainbowModifier : public FaceModifier {
public:
  uint16_t baseHue = 0;
  uint8_t  pulseV  = 255;
  void frameBegin(uint16_t*, int, int) override {
    uint32_t bpm = wl_bpm ? wl_bpm : 120;
    uint32_t period = 60000UL / bpm;
    uint32_t t = millis() - wl_beatEpoch;
    uint32_t beats = t / period;
    uint32_t phase = t % period;                              // ms into the current beat
    baseHue = (uint16_t)((beats * 30 + (phase * 30) / period) % 360);   // hue slides 30 degrees per beat
    pulseV  = (uint8_t)(255 - (phase * 155) / period);        // full brightness on the beat, decaying to ~40%
  }
  uint16_t process(int index, uint16_t* pb, int w, int) override {
    uint16_t pix = pb[index];
    if (pix == 0) return 0;                                   // lit pixels only
    uint8_t r = ((pix >> 11) & 0x1F) << 3, g = ((pix >> 5) & 0x3F) << 2, b = (pix & 0x1F) << 3;
    uint8_t v0 = r > g ? (r > b ? r : b) : (g > b ? g : b);   // keep the sprite's own brightness
    uint8_t v = (uint8_t)(((uint16_t)v0 * pulseV) / 255);
    return hsv565((baseHue + (index % w) * 4) % 360, 255, v); // plus a hue gradient across the face
  }
};
static RainbowModifier rainbowMod;

static void wirelessHandleCommand(const uint8_t* data, int len) {
  if (len != (int)sizeof(CommandPacket)) { wl_bad++; return; }
  CommandPacket c;
  memcpy(&c, data, sizeof c);
  if (c.ver != SP_VER) { wl_bad++; return; }
  if (wl_haveCmd && c.seq == wl_lastCmdSeq) { wl_cmdsDup++; wl_statusNow = true; return; }   // a retry of a command we already took
  uint8_t next = (uint8_t)((wl_cmdHead + 1) & 7);
  if (next == wl_cmdTail) { wl_cmdsDropped++; return; }                                        // queue full: drop (sender retries)
  wl_cmdQ[wl_cmdHead] = c;
  wl_cmdHead = next;
  wl_lastCmdSeq = c.seq;
  wl_haveCmd = true;
  wl_cmdsRx++;
}

static void onWirelessRx(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len < 4) { wl_bad++; return; }
  if (memcmp(info->src_addr, WL_SENDER_MACS[0], 6) != 0 && memcmp(info->src_addr, WL_SENDER_MACS[1], 6) != 0) { wl_foreign++; return; }
  memcpy(wl_peerMac, info->src_addr, 6);
  uint32_t magic;
  memcpy(&magic, data, 4);
  if (magic == CP_MAGIC) { wirelessHandleCommand(data, len); return; }
  if (len != (int)sizeof(SensorPacket)) { wl_bad++; return; }
  SensorPacket p;
  memcpy(&p, data, sizeof p);
  if (p.magic != SP_MAGIC || p.ver != SP_VER) { wl_bad++; return; }

  if (!wl_haveFirst) {                              // first packet only sets the reference counters
    wl_haveFirst = true;
    wl_lastBoopCount = p.boopCount;
    wl_lastBtnCount  = p.btnCount;
    wl_lastSeq = p.seq;
  } else {
    uint16_t gap = (uint16_t)(p.seq - wl_lastSeq - 1);
    if (gap < 1000) wl_lost += gap;                 // bigger jumps mean the sender restarted
    wl_lastSeq = p.seq;
    if (p.boopCount != wl_lastBoopCount) { wl_lastBoopCount = p.boopCount; wl_boopPending = true; wl_boopsRx++; }
    if (p.btnCount  != wl_lastBtnCount)  { wl_lastBtnCount  = p.btnCount;  wl_btnPending  = true; wl_btnsRx++; }
  }
  portENTER_CRITICAL(&wl_mux);
  wl_pkt = p;
  wl_lastRxMs = millis();
  portEXIT_CRITICAL(&wl_mux);
  wl_rx++;
}

static void wirelessBegin() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_channel(SP_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    if (Serial) Serial.print("WL ESP-NOW init FAILED\r\n");
    return;
  }
  esp_now_register_recv_cb(onWirelessRx);
  esp_now_register_send_cb(onWirelessSent);
  for (int i = 0; i < 2; i++) {
    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, WL_SENDER_MACS[i], 6);
    peer.channel = SP_CHANNEL;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;
    esp_now_add_peer(&peer);
  }
                           // needed to send StatusPackets back
  wl_ok = true;
  if (Serial) Serial.printf("WL ready ch%d  my MAC %s  free heap %u B\r\n", SP_CHANNEL, WiFi.macAddress().c_str(), (unsigned)ESP.getFreeHeap());
}

static bool wirelessFresh() {
  return wl_ok && wl_rx > 0 && (millis() - wl_lastRxMs) < WL_STALE_MS;
}

// Local MAX4466 on MIC_PIN: mean |sample - DC| over a 16-sample burst (about 1-2 ms). The DC level is tracked
// automatically, so the old fixed `baseline` constant is not needed. Scale = raw 12-bit ADC counts, the scale the
// face thresholds (talk 300 / loud 600 / yell 1500) were written for. With NOTHING on GPIO1 the pin floats and reads noise.
#define LOCAL_MIC 1
static volatile int g_localMic = 0;
static int localMicLevel() {
  static float dc = -1.0f;
  long sum = 0;
  for (int i = 0; i < 16; i++) {
    int raw = analogRead(MIC_PIN);
    if (dc < 0) dc = raw;
    dc += (raw - dc) * 0.002f;
    sum += abs(raw - (int)dc);
  }
  return (int)(sum / 16);
}

// Mic level on the face-threshold scale; 0 when the link is stale.
static int wirelessMicLevel() {
  if (!wirelessFresh()) return 0;
  uint16_t m;
  portENTER_CRITICAL(&wl_mux);
  m = wl_pkt.mic1;
  portEXIT_CRITICAL(&wl_mux);
  return (int)m * WL_MIC_GAIN;
}

// Tell the control board what the visor is doing (twice a second, and right after a command).
static void wirelessSendStatus() {
  if (!wl_ok) return;
  StatusPacket st;
  memset(&st, 0, sizeof st);
  st.magic = ST_MAGIC;
  st.ver = SP_VER;
  st.face = currFace;
  st.numFaces = numFaces;
  st.numBoops = numBoopAnimations;
  st.brightness = wl_brightness;
  st.volume = wl_volume;
  st.mute = wl_mute;
  st.flags = wirelessFresh() ? 1 : 0;
  st.seq = wl_statusSeq++;
  st.lastCmdSeq = wl_lastCmdSeq;
  st.upMs = millis();
  st.boopsPlayed = (uint16_t)wl_boopsPlayed;
  portENTER_CRITICAL(&wl_mux);
  st.micLevel = wl_pkt.mic1;
  portEXIT_CRITICAL(&wl_mux);
  st.audiolink = wl_audiolink ? 1 : 0;
  st.bpm = wl_bpm;
  esp_now_send(wl_peerMac, (const uint8_t*)&st, sizeof st);
}

// Run one command in loop() context (face, boop and brightness touch the display and animation state).
static void wirelessExecute(const CommandPacket& c) {
  switch (c.type) {
    case CMD_FACE_SET:
      if (c.arg >= 1 && c.arg <= numFaces && faceSelector[c.arg - 1] != nullptr) {
        currFace = c.arg;
        activeFace = *faceSelector[currFace - 1];
        activeFace.initCounts();
        wl_facesChanged++;
        if (Serial) Serial.printf("WL cmd face -> %u/%u\r\n", currFace, numFaces);
      } else if (Serial) Serial.printf("WL cmd face %u out of range (1..%u)\r\n", c.arg, numFaces);
      break;
    case CMD_FACE_NEXT: {
      uint8_t before = currFace;
      selectFaceISR();
      wl_facesChanged++;
      if (Serial) Serial.printf("WL cmd next face %u -> %u\r\n", before, currFace);
      break;
    }
    case CMD_BOOP:
      if (c.arg == 255) {
        booped = true;
        if (Serial) Serial.print("WL cmd boop (next in pool)\r\n");
      } else if (c.arg < numBoopAnimations && boopPool[c.arg] != nullptr) {
        wl_forcedBoop = boopPool[c.arg];
        booped = true;
        if (Serial) Serial.printf("WL cmd boop #%u\r\n", c.arg);
      } else if (Serial) Serial.printf("WL cmd boop %u out of range (0..%u)\r\n", c.arg, numBoopAnimations - 1);
      wl_boopsPlayed++;
      break;
    case CMD_BRIGHTNESS:
      wl_brightness = c.arg;
      display->setBrightness8(wl_brightness);
      if (Serial) Serial.printf("WL cmd brightness %u\r\n", wl_brightness);
      break;
    case CMD_VOLUME:
      wl_volume = c.arg > 100 ? 100 : c.arg;
      if (Serial) Serial.printf("WL cmd volume %u (amp not wired yet, stored only)\r\n", wl_volume);
      break;
    case CMD_MUTE:
      wl_mute = c.arg ? 1 : 0;
      if (Serial) Serial.printf("WL cmd mute %u (amp not wired yet, stored only)\r\n", wl_mute);
      break;
    case CMD_AUDIOLINK:
      wl_audiolink = c.arg ? true : false;
      if (wl_audiolink && wl_beatEpoch == 0) wl_beatEpoch = millis();
      if (Serial) Serial.printf("WL cmd audiolink %s (%u BPM)\r\n", wl_audiolink ? "ON" : "off", (unsigned)wl_bpm);
      break;
    case CMD_BPM:
      wl_bpm = (uint8_t)constrain(c.arg, 40, 220);
      wl_beatEpoch = millis();                                // a tap is a downbeat
      if (Serial) Serial.printf("WL cmd bpm %u\r\n", (unsigned)wl_bpm);
      break;
    case CMD_BEAT:
      wl_beatEpoch = millis();
      if (Serial) Serial.print("WL cmd beat (downbeat)\r\n");
      break;
    default:
      if (Serial) Serial.printf("WL cmd unknown type %u\r\n", c.type);
      break;
  }
}

// Called at the top of every loop(): turns pending radio events into the face
// firmware's own actions, and prints a stats line every WL_STATS_MS.
static void wirelessService() {
  if (wl_boopPending) {
    wl_boopPending = false;
    booped = true;                                  // same flag the local boop ISR sets
    wl_boopsPlayed++;
    if (Serial) Serial.printf("WL boop #%lu -> boop animation\r\n", (unsigned long)wl_boopsPlayed);
  }
  while (wl_cmdTail != wl_cmdHead) {
    CommandPacket c = wl_cmdQ[wl_cmdTail];
    wl_cmdTail = (uint8_t)((wl_cmdTail + 1) & 7);
    wirelessExecute(c);
    wl_statusNow = true;
  }
  if (wl_statusNow || millis() - wl_lastStatusMs >= 500) {
    wl_statusNow = false;
    wl_lastStatusMs = millis();
    wirelessSendStatus();
  }
  if (wl_btnPending) {
    wl_btnPending = false;
    uint8_t before = currFace;
    selectFaceISR();                                // same function the local button ISR calls
    wl_facesChanged++;
    if (Serial) Serial.printf("WL button -> face %u -> %u\r\n", before, currFace);
  }
  uint32_t now = millis();
  if (now - wl_lastStatsMs >= WL_STATS_MS) {
    wl_lastStatsMs = now;
    uint32_t rx = wl_rx;
    SensorPacket p;
    portENTER_CRITICAL(&wl_mux);
    p = wl_pkt;
    portEXIT_CRITICAL(&wl_mux);
    if (Serial) {
      Serial.printf("WL up=%lus rx=%lu (%lu/s) lost=%lu bad=%lu foreign=%lu link=%s seq=%u mic1=%u pk=%u mic2=%u rng=%u boops=%lu btns=%lu heap=%uK lmic=%d\r\n",
        now / 1000, (unsigned long)rx, (unsigned long)((rx - wl_lastRxSnap) * 1000UL / WL_STATS_MS),
        (unsigned long)wl_lost, (unsigned long)wl_bad, (unsigned long)wl_foreign,
        wirelessFresh() ? "UP" : "STALE", (unsigned)p.seq, (unsigned)p.mic1, (unsigned)p.mic1pk,
        (unsigned)p.mic2, (unsigned)p.rangeMm, (unsigned long)wl_boopsRx, (unsigned long)wl_btnsRx,
        (unsigned)(ESP.getFreeHeap() / 1024), (int)g_localMic);
    }
    wl_rxRate = (rx - wl_lastRxSnap) * 1000UL / WL_STATS_MS;
    wl_lastRxSnap = rx;
  }
}


// ============================================================================
// OLED STATUS PAGE (bottom half of the 1.3" OLED; the top half mirrors the face)
// ============================================================================
static const char* const FACE_NAMES[] = {"normal", "smug", "smug 2", "silent"};

static void buildOledStatus(char (*lines)[OLED_STATUS_COLS]) {
  const int C = OLED_STATUS_COLS;
  uint8_t f = currFace;
  const char* nm = (f >= 1 && f <= sizeof(FACE_NAMES) / sizeof(FACE_NAMES[0])) ? FACE_NAMES[f - 1] : "";
  snprintf(lines[0], C, "FACE %u/%u %s", (unsigned)f, (unsigned)numFaces, nm);

  char link[8];
  if (!wl_ok) snprintf(link, sizeof link, "OFF");
  else if (wl_txSent < 4) snprintf(link, sizeof link, "--");
  else snprintf(link, sizeof link, "%d%%", __builtin_popcount((unsigned)wl_txHist) * 100 / 16);
  snprintf(lines[1], C, "LINK %s CMD %lu", link, (unsigned long)wl_cmdsRx);

  snprintf(lines[2], C, "BR %u VOL %u%s", (unsigned)wl_brightness, (unsigned)wl_volume, wl_mute ? " MUTE" : "");

  if (wirelessFresh()) {
    uint16_t m;
    portENTER_CRITICAL(&wl_mux);
    m = wl_pkt.mic1;
    portEXIT_CRITICAL(&wl_mux);
    snprintf(lines[3], C, "AL %s %ubpm MIC %u", wl_audiolink ? "ON" : "off", (unsigned)wl_bpm, (unsigned)m);
  } else {
    snprintf(lines[3], C, "AL %s %ubpm", wl_audiolink ? "ON" : "off", (unsigned)wl_bpm);
  }
}


// ============================================================================
// SETUP & MAIN LOOP
// ============================================================================

void setup() {
  // Serial must never stall the face loop: with the USB host attached but nobody reading,
  // the default TX timeout blocks every Serial.print. Zero timeout drops instead of waiting.
  // (startUP() calls Serial.begin later; the buffer size has to be set before that.)
  Serial.setTxBufferSize(1024);
  Serial.setTxTimeoutMs(0);

  // Initialize Bluetooth (WIP)
  // setupBluetooth();

  // Initialize face selector, initial face sprite pool counts, and boop pool counts once at startup
  initFaceSelector();
  activeFace.initCounts();
  initBoopPool();

  // Initialize the LED matrix display and boot screen
  startUP();

  // Probe I2C bus for internal helmet SSD1306 OLED (SDA = 21, SCK = 22) and start Core 0 task if connected
  setupOLED();
  if (oledConnected) oledSetStatusProvider(buildOledStatus);

  // Start the ESP-NOW receiver (after the display is up, so the HUB75 DMA is already running)
  wirelessBegin();

  // Configure microphone input
  pinMode(MIC_PIN, INPUT);

  // Configure hardware interrupts for boop sensor and face selector button
  pinMode(BOOP_PIN, INPUT_PULLDOWN);
  attachInterrupt(BOOP_PIN, boopISR, RISING);

  pinMode(FACE_SELECTOR, INPUT_PULLDOWN);
  attachInterrupt(FACE_SELECTOR, selectFaceISR, FALLING);
}

void loop() {
  // Wireless events (boop / button) and the stats line
  wirelessService();

  // Handle Boop Sensor Trigger
  if (booped) {
    // Reset flag immediately (and after the animation) to prevent buffering multiple boops
    booped = false;

    const Boop* chosenBoop = wl_forcedBoop ? wl_forcedBoop : pickNextBoop();
    wl_forcedBoop = nullptr;
    if (chosenBoop != nullptr) {
      chosenBoop->play();
    }

    booped      = false;
    blinkMillis = millis();
  }

  // Read & Filter Microphone Input
  // Subtract baseline to center the waveform, take the absolute amplitude for loudness,
  // and pass it through a rolling average (low-pass filter) to smooth spikes.
  // WIRELESS BUILD: the level comes from the sensor board over ESP-NOW (0 when the link is stale).
  // Original local read, kept for reference: rollingAverage(abs(analogRead(MIC_PIN) - baseline))
  // 2026-11-14: LOCAL_MIC 1 = when no sensor packets are arriving, use the MAX4466 on MIC_PIN (GPIO1) on the visor itself.
  g_localMic = localMicLevel();
  int microphoneLevel = rollingAverage((LOCAL_MIC && !wirelessFresh()) ? g_localMic : wirelessMicLevel());

  // AUDIOLINK: swap in the rainbow modifier and redraw faster so the beat pulse is smooth
  {
    const Face* base = faceSelector[currFace - 1];
    if (base != nullptr) {
      activeFace.modifier  = wl_audiolink ? &rainbowMod : base->modifier;
      activeFace.faceDelay = wl_audiolink ? (base->faceDelay < 40 ? base->faceDelay : 40) : base->faceDelay;
    }
  }

  // Update Facial Animation (Speaking vs. Idle) using cached `activeFace`
  if (activeFace.canTalk && microphoneLevel > activeFace.talkThreshold) {
    // Only pick a new random mouth sprite once the previous mouth's hold duration has elapsed
    if (currentSpeakSprite == nullptr || timerDelay(lastMouthMillis, mouthHoldDuration)) {
      if (microphoneLevel > activeFace.yellThreshold) {
        currentSpeakSprite = activeFace.yell();
      } else if (microphoneLevel > activeFace.loudThreshold) {
        currentSpeakSprite = activeFace.loud();
      } else {
        currentSpeakSprite = activeFace.talk();
      }

      lastMouthMillis   = millis();
      mouthHoldDuration = random(100, 200);
    }

    isSpeaking(currentSpeakSprite);
  } else {
    currentSpeakSprite = nullptr;
    isIdle();
  }

  /* Bluetooth Loop (WIP - TODO: Add in Bluetooth)
  uint8_t btMessage = btServerLoop();
  switch (btMessage) {
    case 0: // Run normally
      if (!boopSensor()) {
        getMicrophoneLevel();
      }
      break;

    case 1: // Play Bluetooth display image
      break;
  }
  */
}


// ============================================================================
// DISPLAY & ANIMATION HELPER FUNCTIONS
// ============================================================================

/**
 * Non-blocking speaking animation handler. Redraws `speakSprite` at the interval
 * specified by `activeFace.faceDelay` and triggers a blink every 5000–10000ms.
 *
 * @param speakSprite  Pointer to the RLE sprite selected from the face's talk/loud/yell pool
 */
void isSpeaking(const uint32_t* speakSprite) {
  if (activeFace.blinkSprite != nullptr && timerDelay(blinkMillis, nextBlinkInterval)) {
    activeFace.blink();
    blinkMillis       = millis();
    lastDrawMillis    = blinkMillis;
    nextBlinkInterval = random(5000, 10000);
  } else if (timerDelay(lastDrawMillis, activeFace.faceDelay)) {
    activeFace.draw(speakSprite);
  }
}

/**
 * Non-blocking idle animation handler. Redraws `activeFace.idleSprite` at the
 * interval specified by `activeFace.faceDelay` and triggers a blink every 2000–10000ms.
 */
void isIdle() {
  if (activeFace.blinkSprite != nullptr && timerDelay(blinkMillis, nextBlinkInterval)) {
    activeFace.blink();
    blinkMillis       = millis();
    lastDrawMillis    = blinkMillis;
    nextBlinkInterval = random(2000, 10000);
  } else if (timerDelay(lastDrawMillis, activeFace.faceDelay)) {
    activeFace.draw(activeFace.idleSprite);
  }
}

/**
 * Holds a static RLE-encoded sprite (generated by convimg.py) on the screen
 * for a specified duration.
 *
 * @param time      Duration to hold the image (in milliseconds)
 * @param offset_x  Horizontal pixel offset
 * @param offset_y  Vertical pixel offset
 * @param image     RLE-encoded sprite array (first 3 entries: width, height, array size;
 *                  subsequent entries: 3 bytes RGB + 1 byte repeat count)
 * @param overlay   If false, clears the display before drawing (allows layering when true)
 * @param mirror    If true, mirrors the sprite across the second panel
 * @param modifier  Optional post-processing effect (e.g., aberration, CRT)
 */
void holdFace(int time, int offset_x, int offset_y, const uint32_t image[], bool overlay, bool mirror, FaceModifier* modifier) {
  if (image == nullptr) {
    return;
  }

  unsigned long startHoldMillis = millis();
  unsigned long lastFrameMillis = millis();

  drawFace(offset_x, offset_y, image, overlay, mirror, modifier);

  while (!timerDelay(startHoldMillis, time)) {
    if (timerDelay(lastFrameMillis, defaultFaceDelay)) {
      drawFace(offset_x, offset_y, image, overlay, mirror, modifier);
    }
  }
}


// ============================================================================
// INTERRUPT SERVICE ROUTINES (ISRs)
// ============================================================================

/**
 * Triggered when BOOP_PIN goes HIGH. Flags the main loop to play a boop animation.
 */
void IRAM_ATTR boopISR() {
  booped = true;
}

/**
 * Triggered when FACE_SELECTOR button is pressed. Cycles through `faceSelector[]`
 * with a 250ms debounce window, updates `activeFace`, and recalculates its pool counts.
 */
void IRAM_ATTR selectFaceISR() {
  if (timerDelay(prevButton, 250)) {
    if (currFace >= numFaces) {
      currFace = 1;
    } else {
      currFace++;
    }

    if (faceSelector[currFace - 1] != nullptr) {
      activeFace = *faceSelector[currFace - 1];
      activeFace.initCounts();
    }
  }
}