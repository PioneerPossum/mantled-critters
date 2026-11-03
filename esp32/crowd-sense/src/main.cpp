// Crowd sensor for the MAGI VJ / fursuit HUD, XIAO ESP32-S3 Sense.
// Privacy by design: frames and audio never leave the board. Only small numbers are sent:
//   motion (0..1), a 4x3 grid of motion per region (0..255), mean brightness, mic level (dBFS)
//   and three mic band levels, as one UDP JSON datagram ~5 times a second.
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_camera.h"
#include "driver/i2s.h"
#include <math.h>
#include "secrets.h"

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
// Onboard PDM microphone
#define MIC_CLK_GPIO 42
#define MIC_DATA_GPIO 41

static const int GX = 4, GY = 3;           // motion grid
static const int SEND_MS = 200;            // 5 Hz
static const int MIC_RATE = 16000;
static const int MIC_BLOCK = 512;

WiFiUDP udp;
static uint8_t prevCells[GX * GY];
static bool havePrev = false;
static bool camOk = false;

static bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM; c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_GRAYSCALE;     // motion only needs luma
  c.frame_size = FRAMESIZE_QVGA;            // 320x240
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) return false;
  sensor_t *s = esp_camera_sensor_get();
  if (s) s->set_hmirror(s, 1);              // OV5640 boards come out mirrored (known from the fursuit firmware)
  return true;
}

static bool initMic() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM);
  cfg.sample_rate = MIC_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 4;
  cfg.dma_buf_len = 256;
  if (i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL) != ESP_OK) return false;
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = I2S_PIN_NO_CHANGE;
  pins.ws_io_num = MIC_CLK_GPIO;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = MIC_DATA_GPIO;
  return i2s_set_pin(I2S_NUM_0, &pins) == ESP_OK;
}

// Goertzel power of one frequency over a block, normalised to 0..1 full scale
static float goertzel(const int16_t *x, int n, float freq) {
  float w = 2.0f * M_PI * freq / MIC_RATE, coeff = 2.0f * cosf(w), s1 = 0, s2 = 0;
  for (int i = 0; i < n; i++) { float s = x[i] / 32768.0f + coeff * s1 - s2; s2 = s1; s1 = s; }
  float p = s1 * s1 + s2 * s2 - coeff * s1 * s2;
  return sqrtf(fabsf(p)) / n * 2.0f;
}

struct Audio { float db = -120, bass = 0, mid = 0, high = 0; };
static Audio audio;

static void readMic() {
  static int16_t buf[MIC_BLOCK];
  size_t got = 0;
  if (i2s_read(I2S_NUM_0, buf, sizeof(buf), &got, 0) != ESP_OK || got < sizeof(buf)) return;
  double sq = 0;
  for (int i = 0; i < MIC_BLOCK; i++) sq += (double)buf[i] * buf[i];
  float rms = sqrt(sq / MIC_BLOCK) / 32768.0f;
  audio.db = 20.0f * log10f(fmaxf(rms, 1e-6f));
  audio.bass = goertzel(buf, MIC_BLOCK, 120);
  audio.mid = goertzel(buf, MIC_BLOCK, 1000);
  audio.high = goertzel(buf, MIC_BLOCK, 3500);
}

struct Vision { float motion = 0, lux = 0; uint8_t cells[GX * GY] = {0}; bool ok = false; };

static Vision readCamera() {
  Vision v;
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return v;
  const int W = fb->width, H = fb->height, cw = W / GX, ch = H / GY;
  uint8_t cur[GX * GY];
  uint32_t total = 0;
  for (int gy = 0; gy < GY; gy++)
    for (int gx = 0; gx < GX; gx++) {
      uint32_t sum = 0, n = 0;
      for (int y = gy * ch; y < (gy + 1) * ch; y += 4)
        for (int x = gx * cw; x < (gx + 1) * cw; x += 4) { sum += fb->buf[y * W + x]; n++; }
      cur[gy * GX + gx] = sum / n;
      total += sum / n;
    }
  esp_camera_fb_return(fb);
  v.lux = total / (float)(GX * GY) / 255.0f;
  if (havePrev) {
    float acc = 0;
    for (int i = 0; i < GX * GY; i++) {
      int d = abs((int)cur[i] - (int)prevCells[i]);
      d = d > 3 ? d - 3 : 0;                    // ignore sensor noise
      int scaled = d * 8; if (scaled > 255) scaled = 255;
      v.cells[i] = scaled; acc += scaled / 255.0f;
    }
    v.motion = acc / (GX * GY);
  }
  memcpy(prevCells, cur, sizeof(cur));
  havePrev = true;
  v.ok = true;
  return v;
}

static void sendPacket(const Vision &v) {
  char out[320];
  int n = snprintf(out, sizeof(out), "{\"v\":1,\"id\":\"%s\",\"ms\":%lu,\"motion\":%.3f,\"lux\":%.2f,\"cells\":[",
                   DEVICE_ID, (unsigned long)millis(), v.motion, v.lux);
  for (int i = 0; i < GX * GY; i++) n += snprintf(out + n, sizeof(out) - n, "%s%u", i ? "," : "", v.cells[i]);
  n += snprintf(out + n, sizeof(out) - n, "],\"mic_db\":%.1f,\"bands\":[%.4f,%.4f,%.4f],\"cam\":%d,\"rssi\":%d}",
                audio.db, audio.bass, audio.mid, audio.high, camOk ? 1 : 0, WiFi.RSSI());
  udp.beginPacket(CROWD_RX_HOST, CROWD_RX_PORT);
  udp.write((const uint8_t *)out, n);
  udp.endPacket();
}

void setup() {
  Serial.begin(115200);
  delay(300);
  camOk = initCamera();
  bool micOk = initMic();
  Serial.printf("camera %s, mic %s\n", camOk ? "ok" : "FAILED", micOk ? "ok" : "FAILED");
  WiFi.setHostname("magi-crowd");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void loop() {
  static uint32_t last = 0;
  readMic();                                   // keep draining the DMA buffer between sends
  if (millis() - last < SEND_MS) return;
  last = millis();
  if (WiFi.status() != WL_CONNECTED) return;   // keeps sensing; reconnect is automatic in STA mode
  Vision v = camOk ? readCamera() : Vision();
  sendPacket(v);
}
