// magi-pair: two C6 modules find each other over ESP-NOW (no router, no Wi-Fi join) and measure the link.
// Each node broadcasts "hello" until it hears the other, then pings once per second; the other answers with a pong.
// Serial (USB) prints one JSON line per second: loss, round-trip time, RSSI. Built-in LED blinks on each pong.
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

struct __attribute__((packed)) Msg { uint8_t type; uint32_t seq; uint32_t t_us; char name[12]; };   // 1 hello, 2 ping, 3 pong
static const uint8_t BCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static uint8_t peer[6]; static bool have_peer = false; static char peer_name[12] = "";
static char me[12];
static volatile bool pong_due = false; static volatile uint32_t pong_seq = 0, pong_t = 0;
static volatile uint32_t sent = 0, got = 0, last_seq_sent = 0; static volatile int32_t rssi = 0; static volatile uint32_t rtt_last = 0; static volatile uint64_t rtt_sum = 0;
static volatile uint32_t pings_heard = 0;

static void led(bool on) {
#ifdef RGB_BUILTIN
  neopixelWrite(RGB_BUILTIN, on ? 0 : 0, on ? 24 : 0, 0);
#elif defined(LED_BUILTIN)
  digitalWrite(LED_BUILTIN, on ? LOW : HIGH);          // XIAO C6 user LED is active low
#endif
}

static void add_peer(const uint8_t *mac) {
  memcpy(peer, mac, 6);
  if (!esp_now_is_peer_exist(mac)) { esp_now_peer_info_t p = {}; memcpy(p.peer_addr, mac, 6); p.channel = 0; p.encrypt = false; esp_now_add_peer(&p); }
  have_peer = true;
}

static void on_recv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  if (len < (int)sizeof(Msg)) return;
  Msg m; memcpy(&m, data, sizeof m); m.name[11] = 0;
  rssi = info->rx_ctrl->rssi;
  if (m.type == 1 || m.type == 2) { if (!have_peer || memcmp(peer, info->src_addr, 6)) add_peer(info->src_addr); strncpy(peer_name, m.name, 11); }
  if (m.type == 2) { pong_seq = m.seq; pong_t = m.t_us; pong_due = true; pings_heard++; }
  if (m.type == 3) { uint32_t rtt = micros() - m.t_us; rtt_last = rtt; rtt_sum += rtt; got++; }
}

static void tx(uint8_t type, uint32_t seq, uint32_t t_us, const uint8_t *to) {
  Msg m = {type, seq, t_us, {0}}; strncpy(m.name, me, 11);
  esp_now_send(to, (uint8_t *)&m, sizeof m);
}

void setup() {
  Serial.begin(115200); delay(1500);
  uint64_t em = ESP.getEfuseMac(); snprintf(me, sizeof me, "c6-%02x%02x%02x", (unsigned)((em >> 24) & 0xff), (unsigned)((em >> 32) & 0xff), (unsigned)((em >> 40) & 0xff));
#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
#endif
  WiFi.mode(WIFI_STA); WiFi.disconnect(); esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) { Serial.println("{\"error\":\"esp_now_init failed\"}"); delay(5000); ESP.restart(); }
  esp_now_register_recv_cb(on_recv);
  esp_now_peer_info_t b = {}; memcpy(b.peer_addr, BCAST, 6); b.channel = 0; b.encrypt = false; esp_now_add_peer(&b);
  Serial.printf("{\"pair\":\"start\",\"me\":\"%s\",\"mac\":\"%s\",\"channel\":1}\n", me, WiFi.macAddress().c_str());
}

void loop() {
  static uint32_t t = 0, seq = 0;
  if (pong_due) { pong_due = false; tx(3, pong_seq, pong_t, peer); }
  if (millis() - t >= 1000) {
    t = millis();
    if (!have_peer) tx(1, 0, 0, BCAST); else { seq++; last_seq_sent = seq; sent++; tx(2, seq, micros(), peer); }
    uint32_t g = got, s = sent; float loss = s > 1 ? 100.0f * (s - 1 - (g > s - 1 ? s - 1 : g)) / (s - 1) : 0;   // the newest ping may still be in flight
    Serial.printf("{\"me\":\"%s\",\"peer\":%s%s%s,\"pings_sent\":%u,\"pongs\":%u,\"pings_heard\":%u,\"loss_pct\":%.1f,\"rtt_ms_last\":%.2f,\"rtt_ms_avg\":%.2f,\"rssi\":%d}\n",
                  me, have_peer ? "\"" : "null", have_peer ? peer_name : "", have_peer ? "\"" : "", (unsigned)s, (unsigned)g, (unsigned)pings_heard, loss, rtt_last / 1000.0, g ? (double)rtt_sum / g / 1000.0 : 0.0, (int)rssi);
    led(got != 0 && g > 0 && (t / 1000) % 2); 
  }
  delay(2);
}
