// magi-mesh: every C6 node broadcasts a small beacon every ~2 s over ESP-NOW (no router) and relays other nodes' beacons
// up to MAXHOP hops, so a gateway node on USB can see the whole neighbourhood. Power on a new node: it appears.
// With -DWITH_BLE each node also passively counts nearby BLE advertisers and measures movement (see ble block). PRIVACY: addresses are
// hashed with a per-boot random salt, kept only in RAM for 60 s, never printed, never sent; only aggregate numbers leave the chip.
// Output: one JSON line every 2 s on USB serial (and on UART0 for the DevKit): me + every node heard (rssi, loss, hops, via, age, telemetry).
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_mac.h>
#ifdef WITH_BLE
#include <NimBLEDevice.h>
#endif

#define MAXN 16
#define MAXHOP 3
#define BEACON_MS 2000

struct __attribute__((packed)) Beacon {
  uint8_t magic0, magic1, ver, hop;
  uint8_t origin[6];
  uint32_t seq, uptime_s, heap;
  int16_t temp_c10;
  uint8_t chip;                 // 1 xiao-c6, 2 c6-devkit, 0 other
  char name[12];
  uint16_t ble_n, ble_near;     // distinct BLE advertisers heard in the last window / of those, strong (> -70 dBm)
  uint8_t move_x10, churn_pct;  // mean |RSSI change| of devices seen in two windows (0.1 dB), share of devices that came or went
};
struct Node {
  bool used; uint8_t mac[6]; char name[12]; uint8_t chip;
  uint32_t last_seq; bool have_seq; uint32_t rx, expect; float rssi; uint32_t last_direct_ms;
  uint8_t hops; uint8_t via[6]; uint32_t last_ms, uptime_s, heap; int16_t temp;
  uint16_t ble_n, ble_near; uint8_t move_x10, churn_pct;
};
struct Pending { bool used; uint32_t due; Beacon b; };

static const uint8_t BCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static Node nodes[MAXN];
static Pending relay[6];
static uint8_t selfmac[6]; static char selfname[12]; static uint8_t selfchip;
static volatile uint32_t blink_until = 0;
static uint16_t g_ble_n = 0, g_ble_near = 0; static uint8_t g_move_x10 = 0, g_churn = 0;

#ifdef WITH_BLE
struct Dev { uint32_t id; int8_t rssi, rssi_prev; uint16_t hits; bool in_prev; uint32_t last_ms; bool used; };
static Dev devs[256]; static uint32_t salt; static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
static void ble_record(const uint8_t *addr, int rssi) {
  uint32_t h = 2166136261u ^ salt; for (int i = 0; i < 6; i++) { h ^= addr[i]; h *= 16777619u; }      // salted FNV-1a: not reversible, changes every boot
  uint32_t now = millis(); portENTER_CRITICAL(&mux);
  Dev *d = nullptr, *old = &devs[0];
  for (auto &x : devs) { if (x.used && x.id == h) { d = &x; break; } if (!x.used) { if (!d) d = &x; old = &x; } else if (x.last_ms < old->last_ms && old->used) old = &x; }
  if (!d || (d->used && d->id != h)) d = old;
  if (!d->used || d->id != h) { memset(d, 0, sizeof *d); d->used = true; d->id = h; d->rssi = rssi; d->rssi_prev = rssi; }
  d->rssi = (int8_t)((d->rssi * 3 + rssi) / 4); d->hits++; d->last_ms = now; portEXIT_CRITICAL(&mux);
}
class ScanCb : public NimBLEScanCallbacks { void onResult(const NimBLEAdvertisedDevice *d) override { ble_record(d->getAddress().getVal(), d->getRSSI()); } };
static ScanCb scan_cb; static NimBLEScan *scanner = nullptr;
static void ble_window() {                                  // called every 5 s: turn raw sightings into aggregates, then forget the addresses
  uint32_t now = millis(); int n = 0, near = 0, mv_n = 0, fresh = 0, gone = 0; float mv = 0;
  portENTER_CRITICAL(&mux);
  for (auto &d : devs) {
    if (!d.used) continue;
    bool present = d.hits > 0;
    if (present) { n++; if (d.rssi > -70) near++; if (d.in_prev) { mv += abs((int)d.rssi - (int)d.rssi_prev); mv_n++; } else fresh++; }
    else if (d.in_prev) gone++;
    d.in_prev = present; d.rssi_prev = d.rssi; d.hits = 0;
    if (!present && now - d.last_ms > 60000) d.used = false;
  }
  portEXIT_CRITICAL(&mux);
  g_ble_n = n; g_ble_near = near; g_move_x10 = mv_n ? (uint8_t)min(255.0f, mv / mv_n * 10) : 0;
  g_churn = (n + gone) ? (uint8_t)min(100, 100 * (fresh + gone) / (n + gone)) : 0;
}
#endif

static void out(const String &s) { Serial.println(s);
#ifdef HAS_UART0
  Serial0.println(s);
#endif
}
static String macs(const uint8_t *m) { char b[18]; snprintf(b, sizeof b, "%02x:%02x:%02x:%02x:%02x:%02x", m[0], m[1], m[2], m[3], m[4], m[5]); return String(b); }

static void led(bool on) {
#ifdef RGB_BUILTIN
  neopixelWrite(RGB_BUILTIN, 0, on ? 20 : 0, 0);
#elif defined(LED_BUILTIN)
  digitalWrite(LED_BUILTIN, on ? LOW : HIGH);
#endif
}

static Node *find_or_add(const uint8_t *mac) {
  Node *free_slot = nullptr, *oldest = &nodes[0];
  for (auto &n : nodes) {
    if (n.used && !memcmp(n.mac, mac, 6)) return &n;
    if (!n.used && !free_slot) free_slot = &n;
    if (n.used && n.last_ms < oldest->last_ms) oldest = &n;
  }
  Node *n = free_slot ? free_slot : oldest;
  memset(n, 0, sizeof *n); n->used = true; memcpy(n->mac, mac, 6); n->hops = 255;
  return n;
}

static void on_recv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  if (len != (int)sizeof(Beacon)) return;
  Beacon b; memcpy(&b, data, sizeof b);
  if (b.magic0 != 'M' || b.magic1 != 'S' || b.ver != 2 || !memcmp(b.origin, selfmac, 6)) return;
  uint32_t now = millis();
  Node *n = find_or_add(b.origin);
  b.name[11] = 0; strncpy(n->name, b.name, 11); n->chip = b.chip; n->last_ms = now;
  if (n->have_seq && (int32_t)(b.seq - n->last_seq) <= 0) return;                  // duplicate or older copy: ignore, never relay
  if (b.hop == 0) {                                                                 // heard straight from the origin: RSSI + loss
    float r = info->rx_ctrl->rssi; n->rssi = n->last_direct_ms ? 0.7f * n->rssi + 0.3f * r : r;
    n->expect += n->have_seq ? (b.seq - n->last_seq) : 1; n->rx++; n->last_direct_ms = now;
    if (n->expect >= 120) { n->expect /= 2; n->rx /= 2; }
  }
  n->hops = (now - n->last_direct_ms < 8000) ? 0 : b.hop;
  if (b.hop > 0) memcpy(n->via, info->src_addr, 6); else memset(n->via, 0, 6);
  n->uptime_s = b.uptime_s; n->heap = b.heap; n->temp = b.temp_c10; n->ble_n = b.ble_n; n->ble_near = b.ble_near; n->move_x10 = b.move_x10; n->churn_pct = b.churn_pct; n->last_seq = b.seq; n->have_seq = true;
  blink_until = now + 60;
  if (b.hop < MAXHOP) for (auto &p : relay) if (!p.used) { p.used = true; p.due = now + 15 + (esp_random() % 70); p.b = b; p.b.hop = b.hop + 1; break; }
}

void setup() {
  Serial.begin(115200);
#ifdef HAS_UART0
  Serial0.begin(115200);
#endif
  delay(1200);
#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
#endif
  WiFi.mode(WIFI_STA); WiFi.disconnect(); esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);
  esp_read_mac(selfmac, ESP_MAC_WIFI_STA);
  uint32_t fs = ESP.getFlashChipSize(); selfchip = fs <= 4u * 1024 * 1024 ? 1 : 2;
  snprintf(selfname, sizeof selfname, "%s-%02x%02x%02x", selfchip == 1 ? "xiao" : "devkit", selfmac[3], selfmac[4], selfmac[5]);
  if (esp_now_init() != ESP_OK) { out("{\"error\":\"esp_now_init failed\"}"); delay(5000); ESP.restart(); }
  esp_now_register_recv_cb(on_recv);
#ifdef WITH_BLE
  salt = esp_random(); NimBLEDevice::init(""); scanner = NimBLEDevice::getScan();
  // BLE and ESP-NOW share ONE radio and coexistence is nearly winner-takes-all: a continuous scan kills the mesh, a low-duty one hears nothing.
  // So: one short full-power scan burst per 5 s window (started in loop), mesh traffic in the gaps.
  scanner->setScanCallbacks(&scan_cb, true);
  scanner->setActiveScan(false);
  scanner->setInterval(100);
  scanner->setWindow(99);
  scanner->setMaxResults(0);
#endif
  esp_now_peer_info_t p = {}; memcpy(p.peer_addr, BCAST, 6); p.channel = 0; p.encrypt = false; esp_now_add_peer(&p);
  out("{\"mesh_start\":\"" + String(selfname) + "\",\"mac\":\"" + macs(selfmac) + "\",\"channel\":1}");
}

void loop() {
  static uint32_t next_beacon = 0, next_print = 0, seq = 0, next_win = 5000;
  uint32_t now = millis();
#ifdef WITH_BLE
  if ((int32_t)(now - next_win) >= 0) { next_win = now + 5000; ble_window(); scanner->start(1500, false, true); }
#endif
  for (auto &p : relay) if (p.used && (int32_t)(now - p.due) >= 0) { esp_now_send(BCAST, (uint8_t *)&p.b, sizeof p.b); p.used = false; }
  if ((int32_t)(now - next_beacon) >= 0) {
    next_beacon = now + BEACON_MS + (esp_random() % 400) - 200;
    Beacon b = {'M', 'S', 2, 0, {0}, ++seq, now / 1000, ESP.getFreeHeap(), (int16_t)(temperatureRead() * 10), selfchip, {0}, g_ble_n, g_ble_near, g_move_x10, g_churn};
    memcpy(b.origin, selfmac, 6); strncpy(b.name, selfname, 11);
    esp_now_send(BCAST, (uint8_t *)&b, sizeof b);
  }
  if ((int32_t)(now - next_print) >= 0) {
    next_print = now + BEACON_MS;
    String j = "{\"mesh\":1,\"t\":" + String(now) + ",\"me\":{\"name\":\"" + selfname + "\",\"mac\":\"" + macs(selfmac) + "\",\"chip\":" + selfchip +
               ",\"uptime\":" + String(now / 1000) + ",\"heap\":" + ESP.getFreeHeap() + ",\"temp\":" + String(temperatureRead(), 1) + ",\"ble\":{\"n\":" + g_ble_n + ",\"near\":" + g_ble_near + ",\"move\":" + String(g_move_x10 / 10.0f, 1) + ",\"churn\":" + g_churn + "}},\"nodes\":[";
    bool first = true;
    for (auto &n : nodes) {
      if (!n.used) continue;
      float loss = n.expect ? 100.0f * (1.0f - (float)n.rx / n.expect) : 0; if (loss < 0) loss = 0;
      j += String(first ? "" : ",") + "{\"name\":\"" + n.name + "\",\"mac\":\"" + macs(n.mac) + "\",\"chip\":" + n.chip + ",\"rssi\":" + String(n.rssi, 0) +
           ",\"loss\":" + String(loss, 0) + ",\"hops\":" + (n.hops == 255 ? 0 : n.hops) + ",\"via\":\"" + (n.hops ? macs(n.via) : String("")) + "\",\"age_ms\":" + String(now - n.last_ms) +
           ",\"uptime\":" + n.uptime_s + ",\"heap\":" + n.heap + ",\"temp\":" + String(n.temp / 10.0f, 1) +
           ",\"ble\":{\"n\":" + n.ble_n + ",\"near\":" + n.ble_near + ",\"move\":" + String(n.move_x10 / 10.0f, 1) + ",\"churn\":" + n.churn_pct + "}}";
      first = false;
    }
    out(j + "]}");
  }
  led((int32_t)(blink_until - now) > 0);
  delay(2);
}
