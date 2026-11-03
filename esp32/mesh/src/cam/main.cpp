// magi-cam: XIAO ESP32-S3 Sense. MJPEG over Wi-Fi + status JSON, mDNS, OTA.
//   http://<ip>/            small index        http://<ip>/jpg     one JPEG
//   http://<ip>/status      JSON               http://<ip>:81/stream   MJPEG
//   /control?size=qvga|vga|svga|hd|fhd&quality=4..63&hmirror=0|1&vflip=0|1
#include <Arduino.h>
#include "secrets.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include "esp_camera.h"
#include "sensor.h"
#include "esp_http_server.h"

// XIAO ESP32S3 Sense camera pins
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 10
#define SIOD_GPIO_NUM 40
#define SIOC_GPIO_NUM 39
#define Y9_GPIO_NUM 48
#define Y8_GPIO_NUM 11
#define Y7_GPIO_NUM 12
#define Y6_GPIO_NUM 14
#define Y5_GPIO_NUM 16
#define Y4_GPIO_NUM 18
#define Y3_GPIO_NUM 17
#define Y2_GPIO_NUM 15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM 47
#define PCLK_GPIO_NUM 13

static const char *SENSOR_NAME = "unknown";
static char host[24];
static httpd_handle_t ctl = nullptr, strm = nullptr;
static volatile uint32_t frames_sent = 0;
static uint32_t boot_ms;
static bool net_up = false;

static bool cam_init() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM; c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000; c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA; c.jpeg_quality = 12; c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM; c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) return false;
  sensor_t *s = esp_camera_sensor_get();
  if (s->id.PID == OV5640_PID) { SENSOR_NAME = "OV5640"; s->set_hmirror(s, 1); }  // known mirrored on these boards
  else if (s->id.PID == OV2640_PID) SENSOR_NAME = "OV2640";
  else if (s->id.PID == OV3660_PID) SENSOR_NAME = "OV3660";
  return true;
}

static esp_err_t index_h(httpd_req_t *r) {
  char b[400];
  snprintf(b, sizeof b, "<html><body style='font-family:monospace'><h3>%s (%s)</h3>"
           "<img src='http://%s:81/stream' style='max-width:100%%'><p><a href='/status'>status</a> <a href='/jpg'>jpg</a></p></body></html>",
           host, SENSOR_NAME, WiFi.localIP().toString().c_str());
  httpd_resp_set_type(r, "text/html"); return httpd_resp_send(r, b, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t jpg_h(httpd_req_t *r) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(r); return ESP_FAIL; }
  httpd_resp_set_type(r, "image/jpeg");
  httpd_resp_set_hdr(r, "Cache-Control", "no-store");
  esp_err_t e = httpd_resp_send(r, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb); return e;
}

static esp_err_t status_h(httpd_req_t *r) {
  sensor_t *s = esp_camera_sensor_get();
  char b[420];
  snprintf(b, sizeof b, "{\"host\":\"%s\",\"mac\":\"%s\",\"ip\":\"%s\",\"rssi\":%d,\"sensor\":\"%s\",\"pid\":\"0x%04x\","
           "\"framesize\":%d,\"quality\":%d,\"frames_sent\":%u,\"uptime_s\":%u,\"free_heap\":%u,\"free_psram\":%u}",
           host, WiFi.macAddress().c_str(), WiFi.localIP().toString().c_str(), WiFi.RSSI(), SENSOR_NAME, s->id.PID,
           s->status.framesize, s->status.quality, (unsigned)frames_sent, (unsigned)((millis() - boot_ms) / 1000),
           (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram());
  httpd_resp_set_type(r, "application/json"); return httpd_resp_send(r, b, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t control_h(httpd_req_t *r) {
  char q[128], v[16]; sensor_t *s = esp_camera_sensor_get();
  if (httpd_req_get_url_query_str(r, q, sizeof q) == ESP_OK) {
    if (httpd_query_key_value(q, "size", v, sizeof v) == ESP_OK) {
      framesize_t f = !strcmp(v, "qvga") ? FRAMESIZE_QVGA : !strcmp(v, "svga") ? FRAMESIZE_SVGA : !strcmp(v, "hd") ? FRAMESIZE_HD
                    : !strcmp(v, "fhd") ? FRAMESIZE_FHD : FRAMESIZE_VGA;
      s->set_framesize(s, f);
    }
    if (httpd_query_key_value(q, "quality", v, sizeof v) == ESP_OK) s->set_quality(s, constrain(atoi(v), 4, 63));
    if (httpd_query_key_value(q, "hmirror", v, sizeof v) == ESP_OK) s->set_hmirror(s, atoi(v));
    if (httpd_query_key_value(q, "vflip", v, sizeof v) == ESP_OK) s->set_vflip(s, atoi(v));
  }
  return status_h(r);
}

static esp_err_t stream_h(httpd_req_t *r) {
  httpd_resp_set_type(r, "multipart/x-mixed-replace;boundary=magiframe");
  httpd_resp_set_hdr(r, "Access-Control-Allow-Origin", "*");
  char part[64];
  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { delay(20); continue; }
    esp_err_t e = httpd_resp_send_chunk(r, "\r\n--magiframe\r\nContent-Type: image/jpeg\r\n", 0);
    if (e == ESP_OK) { snprintf(part, sizeof part, "Content-Length: %u\r\n\r\n", (unsigned)fb->len); e = httpd_resp_send_chunk(r, part, strlen(part)); }
    if (e == ESP_OK) e = httpd_resp_send_chunk(r, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (e != ESP_OK) break;   // client went away
    frames_sent++;
  }
  return ESP_OK;
}

static void start_servers() {
  httpd_config_t c = HTTPD_DEFAULT_CONFIG(); c.server_port = 80; c.max_uri_handlers = 8;
  httpd_uri_t u[] = {{"/", HTTP_GET, index_h, nullptr}, {"/jpg", HTTP_GET, jpg_h, nullptr},
                     {"/status", HTTP_GET, status_h, nullptr}, {"/control", HTTP_GET, control_h, nullptr}};
  if (httpd_start(&ctl, &c) == ESP_OK) for (auto &x : u) httpd_register_uri_handler(ctl, &x);
  c.server_port = 81; c.ctrl_port += 1;
  httpd_uri_t st = {"/stream", HTTP_GET, stream_h, nullptr};
  if (httpd_start(&strm, &c) == ESP_OK) httpd_register_uri_handler(strm, &st);
}

static void wifi_connect() {
  WiFi.onEvent([](arduino_event_id_t, arduino_event_info_t info) { Serial.printf("wifi disconnect reason=%d\n", (int)info.wifi_sta_disconnected.reason); }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.setMinSecurity(WIFI_AUTH_WPA2_PSK);
  WiFi.persistent(false); WiFi.disconnect(true, true); delay(300);   // drop any stored Wi-Fi config in NVS
  WiFi.mode(WIFI_STA); WiFi.setHostname(host); WiFi.setSleep(false); WiFi.setAutoReconnect(true);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);   // max; a weak uplink on one board (cam2) made lower power worse
  uint8_t best[6] = {0}; int bestch = 0, bestr = -127;
  int n0 = WiFi.scanNetworks();                         // before joining: which APs carry our SSID? (BSSIDs are not secrets)
  for (int i = 0; i < n0; i++) if (WiFi.SSID(i) == WIFI_SSID) {
    Serial.printf("  AP %s ch%d rssi%d enc%d\n", WiFi.BSSIDstr(i).c_str(), WiFi.channel(i), WiFi.RSSI(i), (int)WiFi.encryptionType(i));
    if (WiFi.RSSI(i) > bestr) { bestr = WiFi.RSSI(i); bestch = WiFi.channel(i); memcpy(best, WiFi.BSSID(i), 6); }
  }
  if (bestch) WiFi.begin(WIFI_SSID, WIFI_PSK, bestch, best); else WiFi.begin(WIFI_SSID, WIFI_PSK);
  Serial.printf("Wi-Fi: joining '%s'", WIFI_SSID);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print('.'); }
  Serial.printf("\nstatus=%d (3=connected 4=connect_failed 1=no_ssid_avail 6=wrong_pw/disconnected)\n", (int)WiFi.status());
  if (WiFi.status() != WL_CONNECTED) {            // diagnostics: what can this radio hear? (SSIDs are not secrets)
    int n = WiFi.scanNetworks();
    Serial.printf("scan: %d networks (2.4 GHz only)\n", n);
    for (int i = 0; i < n && i < 12; i++) Serial.printf("  '%s' ch%d rssi%d enc%d\n", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i), (int)WiFi.encryptionType(i));
  }
}

void setup() {
  Serial.begin(115200); delay(1500); boot_ms = millis();
  uint64_t em = ESP.getEfuseMac(); snprintf(host, sizeof host, "magi-cam-%02x%02x%02x", (unsigned)((em >> 24) & 0xff), (unsigned)((em >> 32) & 0xff), (unsigned)((em >> 40) & 0xff));
  Serial.printf("\n%s booting\n", host);
  if (!cam_init()) { Serial.println("camera init FAILED, rebooting in 10 s"); delay(10000); ESP.restart(); }
  Serial.printf("camera: %s pid=0x%04x\n", SENSOR_NAME, esp_camera_sensor_get()->id.PID);
  wifi_connect();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("IP %s RSSI %d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    MDNS.begin(host); MDNS.addService("http", "tcp", 80); MDNS.addService("magi-cam", "tcp", 81);
    ArduinoOTA.setHostname(host); ArduinoOTA.setPassword(OTA_PASS); ArduinoOTA.begin();
    start_servers(); net_up = true;
  }
}

void loop() {
  static uint32_t down_since = 0, last = 0;
  ArduinoOTA.handle();
  if (!net_up && WiFi.status() == WL_CONNECTED) {   // joined after the first attempt timed out
    Serial.printf("late join IP %s\n", WiFi.localIP().toString().c_str());
    MDNS.begin(host); MDNS.addService("http", "tcp", 80); MDNS.addService("magi-cam", "tcp", 81);
    ArduinoOTA.setHostname(host); ArduinoOTA.setPassword(OTA_PASS); ArduinoOTA.begin();
    start_servers(); net_up = true;
  }
  if (WiFi.status() != WL_CONNECTED) {
    static uint32_t retry = 0;
    if (millis() - retry > 60000) { retry = millis(); WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PSK); }
    if (!down_since) down_since = millis();
    if (millis() - down_since > 120000) ESP.restart();   // lost Wi-Fi for 2 min: reboot and re-join
  } else down_since = 0;
  if (millis() - last > 15000) {
    last = millis();
    Serial.printf("%s %s ip=%s rssi=%d frames=%u heap=%u\n", host, SENSOR_NAME, WiFi.localIP().toString().c_str(), WiFi.RSSI(), (unsigned)frames_sent, (unsigned)ESP.getFreeHeap());
  }
  delay(20);
}
