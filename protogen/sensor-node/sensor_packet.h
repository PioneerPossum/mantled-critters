// ============================================================================
// sensor_packet.h : wire format for the visor's wireless sensor stream
// MASTER COPY: protogen/shared/sensor_packet.h
// Copies live next to sensor-node.ino and proot-s3-wireless.ino. If you change
// this file, copy it to both and bump SP_VER. Packets travel over ESP-NOW
// (2.4 GHz, no router needed) from the sensor board to the visor controller.
// ============================================================================
#pragma once
#include <stdint.h>

#define SP_MAGIC    0x544F5250u   // bytes 'P','R','O','T'
#define SP_VER      3
#define SP_CHANNEL  1             // both boards pin the radio to this Wi-Fi channel

// flags: LEVELS and VALIDITY at the moment of sending
enum : uint8_t {
  SPF_BOOP   = 1 << 0,   // boop input is high right now (or a synthetic boop is active)
  SPF_BTN    = 1 << 1,   // face button is down right now
  SPF_VL     = 1 << 2,   // rangeMm is valid
  SPF_MIC1   = 1 << 3,   // analog mic burst is valid
  SPF_MIC2   = 1 << 4,   // I2S mic returned samples this tick (0 = dropped out / absent)
  SPF_BMEIN  = 1 << 5,   // tIn/hIn valid
  SPF_BMEOUT = 1 << 6,   // tOut/hOut valid
  SPF_IMU    = 1 << 7    // ax/ay/az valid
};

struct __attribute__((packed)) SensorPacket {
  uint32_t magic;       // SP_MAGIC
  uint8_t  ver;         // SP_VER
  uint8_t  flags;       // SPF_*
  uint16_t seq;         // increments every packet; gaps = lost packets
  uint32_t ms;          // sender uptime in ms
  uint8_t  boopCount;   // +1 on every boop rising edge (wraps); act on CHANGE, so a lost packet cannot swallow a boop
  uint8_t  btnCount;    // +1 on every button press (wraps)
  uint16_t mic1;        // MAX4466: mean |sample - baseline| over a ~3 ms burst
  uint16_t mic1pk;      // MAX4466: peak |sample - baseline| in that burst
  uint16_t mic2;        // INMP441: RMS (18-bit scale), 0 if no samples
  uint16_t rangeMm;     // VL53L1X distance, 0xFFFF = none
  int16_t  tIn, tOut;   // 0.01 degC
  uint16_t hIn, hOut;   // 0.1 %RH
  int16_t  ax, ay, az;  // milli-g
};

static_assert(sizeof(SensorPacket) <= 250, "ESP-NOW payload limit is 250 bytes");

// ============================================================================
// TWO-WAY CONTROL (added 2026-11-08): control board -> visor commands, visor -> control board status
// ============================================================================
// Commands are sent unicast with a sequence number. The visor ignores a repeated seq (so the
// sender can retry safely), applies the command in its main loop, and answers with a StatusPacket
// whose lastCmdSeq confirms it. State commands carry ABSOLUTE values (face number, brightness,
// volume, mute), so a retry or a late duplicate can never drift the state.
#define CP_MAGIC    0x4D435250u   // bytes 'P','R','C','M'
#define ST_MAGIC    0x54535250u   // bytes 'P','R','S','T'

enum : uint8_t {
  CMD_NOP        = 0,
  CMD_FACE_SET   = 1,   // arg = face number 1..numFaces
  CMD_FACE_NEXT  = 2,   // one-shot
  CMD_BOOP       = 3,   // arg = boop animation index 0..numBoops-1, 255 = next from the pool
  CMD_BRIGHTNESS = 4,   // arg = 0..255, LED panel brightness
  CMD_VOLUME     = 5,   // arg = 0..100, voice-amp volume (visor stores and reports it)
  CMD_MUTE       = 6,   // arg = 0 or 1
  CMD_AUDIOLINK  = 7,   // arg = 0/1: rainbow beat effect on the face, synced to the BPM
  CMD_BPM        = 8,   // arg = BPM 40..220; also re-aligns the beat phase to "now" (tap tempo)
  CMD_BEAT       = 9    // one-shot: this instant is a downbeat (re-aligns the phase only)
};

struct __attribute__((packed)) CommandPacket {
  uint32_t magic;       // CP_MAGIC
  uint8_t  ver;         // SP_VER
  uint8_t  type;        // CMD_*
  uint8_t  arg;
  uint8_t  arg2;        // reserved
  uint16_t seq;         // changes for every NEW command; a retry repeats the same seq
};

struct __attribute__((packed)) StatusPacket {
  uint32_t magic;       // ST_MAGIC
  uint8_t  ver;         // SP_VER
  uint8_t  face;        // current face number, 1-based
  uint8_t  numFaces;
  uint8_t  numBoops;
  uint8_t  brightness;  // 0..255
  uint8_t  volume;      // 0..100
  uint8_t  mute;        // 0/1
  uint8_t  flags;       // bit0 = sensor stream link is UP
  uint16_t seq;         // status counter
  uint16_t lastCmdSeq;  // seq of the last command the visor accepted
  uint32_t upMs;        // visor uptime
  uint16_t boopsPlayed;
  uint16_t micLevel;    // latest mic level the face is using (before gain)
  uint8_t  audiolink;   // 0/1
  uint8_t  bpm;         // current beat rate used by audiolink
};

static_assert(sizeof(CommandPacket) <= 250 && sizeof(StatusPacket) <= 250, "ESP-NOW payload limit");
