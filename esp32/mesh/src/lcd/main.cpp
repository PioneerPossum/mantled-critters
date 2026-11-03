// magi-lcd: Waveshare ESP32-C6 LCD (ST7789 170x320). Status/test panel.
//   Shows host, IP, RSSI, uptime, a frame counter and a colour bar that changes every second, so a camera
//   pointed at the screen can prove the whole chain. GET /status (JSON), GET /text?msg=hello (set a line).
// Pins from the Waveshare ESP32-C6-LCD-1.9 schematic (2026-10-09): DIN=IO4 CLK=IO5 DC=IO6 CS=IO7 RST=IO14 BL=IO15, WS2812 on IO3.
#include <Arduino.h>
#include "secrets.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <Arduino_GFX_Library.h>

#define C_BLACK 0x0000
#define C_WHITE 0xFFFF
#define C_RED 0xF800
#define C_GREEN 0x07E0
#define C_BLUE 0x001F
#define C_YELLOW 0xFFE0
#define C_CYAN 0x07FF
#define C_MAGENTA 0xF81F

#ifndef LCD_MOSI
#define LCD_MOSI 4
#define LCD_SCLK 5
#define LCD_CS 7
#define LCD_DC 6
#define LCD_RST 14
#define LCD_BL 15
#endif
#ifndef LCD_BL_ON
#define LCD_BL_ON LOW
#endif

Arduino_DataBus *bus = new Arduino_ESP32SPI(LCD_DC, LCD_CS, LCD_SCLK, LCD_MOSI, GFX_NOT_DEFINED);
Arduino_GFX *gfx = new Arduino_ST7789(bus, LCD_RST, 0 /*rotation*/, true /*IPS*/, 170, 320, 35, 0, 35, 0);
WebServer web(80);
static char host[24];
static String line = "magi";
static uint32_t frame = 0;

static void draw() {
  static const uint16_t pal[] = {C_RED, C_GREEN, C_BLUE, C_YELLOW, C_CYAN, C_MAGENTA, C_WHITE};
  gfx->fillScreen(C_BLACK);
  gfx->fillRect(0, 0, 170, 60, pal[frame % 7]);                       // colour bar changes every tick
  gfx->setTextColor(C_WHITE); gfx->setTextSize(2);
  gfx->setCursor(4, 70);  gfx->print(host);
  gfx->setCursor(4, 94);  gfx->print(WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : String("no wifi"));
  gfx->setCursor(4, 118); gfx->printf("rssi %d", WiFi.RSSI());
  gfx->setCursor(4, 142); gfx->printf("up %us", (unsigned)(millis() / 1000));
  gfx->setTextSize(6); gfx->setCursor(4, 176); gfx->printf("%04u", (unsigned)(frame % 10000));
  gfx->setTextSize(2); gfx->setCursor(4, 240); gfx->print(line);
}

void setup() {
  Serial.begin(115200); delay(1200);
  uint64_t em = ESP.getEfuseMac(); snprintf(host, sizeof host, "magi-lcd-%02x%02x%02x", (unsigned)((em >> 24) & 0xff), (unsigned)((em >> 32) & 0xff), (unsigned)((em >> 40) & 0xff));
  pinMode(LCD_BL, OUTPUT); digitalWrite(LCD_BL, LCD_BL_ON);
  gfx->begin(); gfx->setRotation(0); gfx->fillScreen(C_BLACK);
  gfx->setTextColor(C_WHITE); gfx->setTextSize(2); gfx->setCursor(4, 4); gfx->print("joining wifi");
  WiFi.mode(WIFI_STA); WiFi.setHostname(host); WiFi.setSleep(false); WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PSK);
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print('.'); }
  Serial.printf("\n%s ip=%s\n", host, WiFi.localIP().toString().c_str());
  if (WiFi.status() == WL_CONNECTED) {
    MDNS.begin(host); MDNS.addService("http", "tcp", 80); MDNS.addService("magi-lcd", "tcp", 80);
    ArduinoOTA.setHostname(host); ArduinoOTA.setPassword(OTA_PASS); ArduinoOTA.begin();
  }
  web.on("/status", []() {
    char b[240];
    snprintf(b, sizeof b, "{\"host\":\"%s\",\"mac\":\"%s\",\"ip\":\"%s\",\"rssi\":%d,\"frame\":%u,\"uptime_s\":%u,\"line\":\"%s\"}",
             host, WiFi.macAddress().c_str(), WiFi.localIP().toString().c_str(), WiFi.RSSI(), (unsigned)frame, (unsigned)(millis() / 1000), line.c_str());
    web.send(200, "application/json", b);
  });
  web.on("/text", []() { line = web.arg("msg").substring(0, 24); web.send(200, "text/plain", "ok"); });
  web.begin();
}

void loop() {
  static uint32_t t = 0, down_since = 0;
  ArduinoOTA.handle(); web.handleClient();
  if (WiFi.status() != WL_CONNECTED) { if (!down_since) down_since = millis(); if (millis() - down_since > 120000) ESP.restart(); }
  else down_since = 0;
  if (millis() - t >= 1000) { t = millis(); frame++; draw(); Serial.printf("%s frame=%u rssi=%d\n", host, (unsigned)frame, WiFi.RSSI()); }
  delay(5);
}
