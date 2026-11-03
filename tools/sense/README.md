# sense.py

Stdlib-only helper for a Linux handheld: scans I2C bus 3 (allow-list), USB and serial ports, writes findings under `~/sensor-desk`. No network, no sudo. Probing is read-only (`i2cget` sends the register-pointer byte). Review before running; this is v2 (fixed-column `i2cdetect` parser, `req` sender default).
