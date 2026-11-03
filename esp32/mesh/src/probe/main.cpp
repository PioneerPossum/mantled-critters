// magi-probe: read-only discovery for a XIAO ESP32-C6 (or C6 DevKit): which I2C devices answer, what do the pins read.
// Prints one JSON line every 2 s over USB serial. Changes nothing, drives no pin (all INPUT), joins no network.
#include <Arduino.h>
#include <Wire.h>

struct Known { uint8_t a; const char *n; };
static const Known KNOWN[] = {{0x76, "BME280/BMP280 (alt)"}, {0x77, "BME280/BMP280/BME680"}, {0x68, "MPU6050/DS3231/ICM-20948"}, {0x69, "MPU6050 alt"},
  {0x3C, "SSD1306 OLED"}, {0x3D, "SSD1306 alt"}, {0x29, "VL53L0X/TSL2591/BNO055"}, {0x28, "BNO055"}, {0x48, "ADS1115/TMP102"}, {0x40, "INA219/SHT/HTU21D/PCA9685"},
  {0x44, "SHT3x/OPT3001"}, {0x45, "SHT3x alt"}, {0x23, "BH1750"}, {0x5C, "BH1750 alt/AM2320"}, {0x38, "AHT10/AHT20/FT6x36"}, {0x39, "APDS9960/TSL2561"},
  {0x57, "MAX30102/EEPROM"}, {0x53, "ADXL345"}, {0x1D, "ADXL345 alt/LIS3DH"}, {0x19, "LIS3DH/LSM303"}, {0x6A, "LSM6DS3/LSM6DSO"}, {0x6B, "LSM6DS alt"},
  {0x5A, "MLX90614/CCS811/MPR121"}, {0x62, "SCD30/SCD4x"}, {0x10, "VEML7700"}, {0x1E, "HMC5883L/LIS3MDL"}, {0x60, "Si1145/MCP4725"}};

static const char *name_of(uint8_t a) { for (auto &k : KNOWN) if (k.a == a) return k.n; return "unknown"; }

void setup() {
  Serial.begin(115200); delay(1500);
  Wire.begin();                                        // XIAO C6 default: SDA=D4(GPIO22) SCL=D5(GPIO23)
  Serial.printf("{\"probe\":\"start\",\"chip\":\"%s\",\"sda\":%d,\"scl\":%d}\n", ESP.getChipModel(), SDA, SCL);
}

void loop() {
  String i2c = "[";
  int n = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { i2c += String(n++ ? "," : "") + "{\"addr\":\"0x" + String(a, HEX) + "\",\"maybe\":\"" + name_of(a) + "\"}"; }
  }
  i2c += "]";
  // pins D0..D10 on the XIAO C6 = GPIO 0,1,2,21,22,23,16,17,19,20,18 ; GPIO0-6 are ADC-capable. I2C pins are skipped once devices answer.
  static const int PINS[] = {0, 1, 2, 21, 16, 17, 19, 20, 18};
  String pins = "{";
  for (size_t k = 0; k < sizeof(PINS) / sizeof(PINS[0]); k++) {
    int p = PINS[k]; pinMode(p, INPUT);
    pins += String(k ? "," : "") + "\"io" + p + "\":{\"d\":" + digitalRead(p) + (p <= 6 ? ",\"mv\":" + String(analogReadMilliVolts(p)) : "") + "}";
  }
  pins += "}";
  Serial.printf("{\"t\":%lu,\"i2c_count\":%d,\"i2c\":%s,\"pins\":%s}\n", millis(), n, i2c.c_str(), pins.c_str());
  delay(2000);
}
