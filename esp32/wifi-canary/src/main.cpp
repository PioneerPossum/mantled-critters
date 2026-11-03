// Wi-Fi canary: joins the HOME router's 2.4 GHz Wi-Fi (pinned BSSID) and every CYCLE_MS probes the internet:
//   gw   = ICMP echo to the DHCP gateway
//   ping = ICMP echo to 1.1.1.1
//   dnsr = raw DNS query (random, uncached label) to the DHCP-provided resolver
//   dnsp = raw DNS query (random label) straight to 1.1.1.1  (separates "resolver" from "path")
//   tcp  = TCP connect to 1.1.1.1:443
// and reports one text line by UDP to audire-videre (wifi-canary-rx.py, see protocol below). -1 = probe failed.
//
// Why: a fault on 2026-10-23 only hit Wi-Fi clients behind the home router (ICMP + DNS to the internet dropped
// 80-100%) while every wired monitor looked clean. This is the Wi-Fi vantage point.
//
// Packet: "WC1 id=<hex6> seq=<n> up=<s> rssi=<dBm> bssid=<mac> ip=<a.b.c.d> gw=<ms> ping=<ms> dnsr=<ms> dnsp=<ms> tcp=<ms> out=<s> rst=<n>"
//
// Notes: modem sleep is OFF so latency numbers are stable. REPORT_IP is audire-videre's DHCP address; if it moves,
// change it here and re-flash (the receiver's log shows SILENT if packets stop arriving).
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WiFiClient.h>
#include "esp_system.h"
#include "esp_random.h"
extern "C" {
#include "ping/ping_sock.h"
#include "lwip/ip_addr.h"
}
#include "secrets.h"

#define REPORT_IP    "192.168.8.136"
#define REPORT_PORT  9631
#define CYCLE_MS     10000
// Home router (GL-BE3600) 2.4 GHz BSSID, channel 6. The Mudi's own AP has the SAME SSID, so pin to the home router.
// PLACEHOLDER: set to your AP's BSSID (the published copy is scrubbed)
static const uint8_t PIN_BSSID[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x00};
#define PIN_CHANNEL  6

static WiFiUDP udp;
static uint32_t seqNo = 0;
static uint32_t lastOutageS = 0;      // length of the last Wi-Fi outage this device saw
static uint32_t downSince = 0;        // millis() when Wi-Fi was first seen down (0 = up)
static uint8_t  pinFails = 0;
static bool     pinned = true;

// ---------------- ICMP via esp_ping ----------------
struct PingRes { volatile int ms; volatile bool done; };
static void onSuccess(esp_ping_handle_t h, void *a) {
  uint32_t t = 0; esp_ping_get_profile(h, ESP_PING_PROF_TIMEGAP, &t, sizeof(t));
  PingRes *r = (PingRes *)a; r->ms = (int)t;
}
static void onEnd(esp_ping_handle_t h, void *a) { ((PingRes *)a)->done = true; }
static void onTimeout(esp_ping_handle_t h, void *a) {}

static int pingOnce(IPAddress ip, uint32_t timeoutMs) {
  PingRes r; r.ms = -1; r.done = false;
  esp_ping_config_t cfg = ESP_PING_DEFAULT_CONFIG();
  cfg.count = 1; cfg.timeout_ms = timeoutMs; cfg.interval_ms = 1000; cfg.task_stack_size = 3072;
  ip_addr_t t; IP_ADDR4(&t, ip[0], ip[1], ip[2], ip[3]); cfg.target_addr = t;
  esp_ping_callbacks_t cbs = {};
  cbs.cb_args = &r; cbs.on_ping_success = onSuccess; cbs.on_ping_timeout = onTimeout; cbs.on_ping_end = onEnd;
  esp_ping_handle_t h = nullptr;
  if (esp_ping_new_session(&cfg, &cbs, &h) != ESP_OK) return -1;
  esp_ping_start(h);
  uint32_t t0 = millis();
  while (!r.done && millis() - t0 < timeoutMs + 1500) delay(10);
  esp_ping_stop(h); esp_ping_delete_session(h);
  return r.ms;
}

// ---------------- raw DNS query (NXDOMAIN counts as an answer) ----------------
static int dnsProbe(IPAddress server, uint32_t timeoutMs) {
  if (server == IPAddress(0, 0, 0, 0)) return -1;
  uint8_t q[80]; int n = 0;
  uint16_t id = (uint16_t)esp_random();
  q[n++] = id >> 8; q[n++] = id & 0xff; q[n++] = 0x01; q[n++] = 0x00;          // flags: RD
  q[n++] = 0; q[n++] = 1; q[n++] = 0; q[n++] = 0; q[n++] = 0; q[n++] = 0; q[n++] = 0; q[n++] = 0;
  char lab[16]; snprintf(lab, sizeof(lab), "wc%08x", (unsigned)esp_random());   // random label => not cached
  q[n++] = strlen(lab); for (const char *p = lab; *p; p++) q[n++] = *p;
  q[n++] = 7; memcpy(q + n, "example", 7); n += 7;
  q[n++] = 3; memcpy(q + n, "com", 3); n += 3;
  q[n++] = 0; q[n++] = 0; q[n++] = 1; q[n++] = 0; q[n++] = 1;                  // QTYPE A, QCLASS IN
  WiFiUDP u; if (!u.begin(0)) return -1;
  uint32_t t0 = millis();
  u.beginPacket(server, 53); u.write(q, n); u.endPacket();
  int result = -1;
  while (millis() - t0 < timeoutMs) {
    int len = u.parsePacket();
    if (len >= 12) {
      uint8_t b[16]; u.read(b, 16);
      if (b[0] == (id >> 8) && b[1] == (id & 0xff) && (b[2] & 0x80)) {
        int rcode = b[3] & 0x0f;
        if (rcode == 0 || rcode == 3) result = (int)(millis() - t0);           // NOERROR or NXDOMAIN
        break;
      }
    }
    delay(5);
  }
  u.stop();
  return result;
}

static int tcpProbe(IPAddress ip, uint16_t port, uint32_t timeoutMs) {
  WiFiClient c; uint32_t t0 = millis();
  bool ok = c.connect(ip, port, timeoutMs);
  int ms = ok ? (int)(millis() - t0) : -1;
  c.stop();
  return ms;
}

// ---------------- Wi-Fi ----------------
static void startWifi() {
  WiFi.disconnect(true, false);
  delay(100);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  if (pinned) {
    Serial.printf("wifi: begin pinned bssid 02:00:00:00:00:00 ch%d\n", PIN_CHANNEL);
    WiFi.begin(WIFI_SSID, WIFI_PSK, PIN_CHANNEL, PIN_BSSID, true);
  } else {
    Serial.println("wifi: begin UNPINNED (home router BSSID not found; may land on another AP, see bssid= in reports)");
    WiFi.begin(WIFI_SSID, WIFI_PSK);
  }
}

static bool waitConnected(uint32_t ms) {
  uint32_t t0 = millis();
  while (millis() - t0 < ms) { if (WiFi.status() == WL_CONNECTED) return true; delay(100); }
  return WiFi.status() == WL_CONNECTED;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nwifi-canary v1 starting");
  startWifi();
  if (waitConnected(20000)) { pinFails = 0; Serial.printf("wifi: up ip=%s\n", WiFi.localIP().toString().c_str()); }
}

void loop() {
  static uint32_t lastCycle = 0;
  if (WiFi.status() != WL_CONNECTED) {
    if (!downSince) { downSince = millis(); Serial.println("wifi: DOWN"); }
    // after a while, re-begin; after 3 failed pinned attempts, fall back to unpinned (visible in bssid=)
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 25000) {
      lastTry = millis();
      if (pinned && ++pinFails >= 3) { pinned = false; pinFails = 0; }
      startWifi();
    }
    delay(500);
    return;
  }
  if (downSince) {
    lastOutageS = (millis() - downSince) / 1000; downSince = 0; pinFails = 0;
    Serial.printf("wifi: back after %us ip=%s\n", (unsigned)lastOutageS, WiFi.localIP().toString().c_str());
    if (!pinned) pinned = true;   // try the pin again at the next drop
  }
  if (millis() - lastCycle < CYCLE_MS && lastCycle != 0) { delay(50); return; }
  lastCycle = millis();

  IPAddress gw = WiFi.gatewayIP(), dns = WiFi.dnsIP(0), one(1, 1, 1, 1);
  int mGw   = pingOnce(gw, 1000);
  int mPing = pingOnce(one, 1000);
  int mDnsR = dnsProbe(dns, 1500);
  int mDnsP = dnsProbe(one, 1500);
  int mTcp  = tcpProbe(one, 443, 2000);

  uint8_t *b = WiFi.BSSID();
  char bs[20] = "00:00:00:00:00:00";
  if (b) snprintf(bs, sizeof(bs), "%02x:%02x:%02x:%02x:%02x:%02x", b[0], b[1], b[2], b[3], b[4], b[5]);
  uint64_t mac = ESP.getEfuseMac();
  char line[256];
  int n = snprintf(line, sizeof(line),
    "WC1 id=%02x%02x%02x seq=%u up=%u rssi=%d bssid=%s ip=%s gw=%d ping=%d dnsr=%d dnsp=%d tcp=%d out=%u rst=%d",
    (unsigned)((mac >> 24) & 0xff), (unsigned)((mac >> 32) & 0xff), (unsigned)((mac >> 40) & 0xff),
    (unsigned)++seqNo, (unsigned)(millis() / 1000), WiFi.RSSI(), bs, WiFi.localIP().toString().c_str(),
    mGw, mPing, mDnsR, mDnsP, mTcp, (unsigned)lastOutageS, (int)esp_reset_reason());
  Serial.println(line);
  IPAddress rip; rip.fromString(REPORT_IP);
  udp.beginPacket(rip, REPORT_PORT); udp.write((const uint8_t *)line, n); udp.endPacket();
}
