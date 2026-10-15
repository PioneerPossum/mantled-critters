---
name: secret-handoff-method
description: "How Gil hands me passwords/keys without typing them into chat: named files in ~/.secrets via the set-secret script, optional passphrase-encrypted copy only he can decrypt; use this for every future credential"
metadata:
  node_type: memory
  type: feedback
---

**Rule (Gil, 2026-10-09):** when I need a password, token or key, do NOT ask him to paste it in chat or in a command I can see. Use the secret handoff: he runs `~/bin/set-secret NAME` in a real terminal on audire-videre (silent prompts; he chooses NAME and the key names), which writes `~/.secrets/NAME.secret` (KEY=value lines, chmod 600, dir 700). `--backup` also writes `NAME.secret.gpg` (gpg AES256, passphrase only he knows; decrypt with `gpg -d ~/.secrets/NAME.secret.gpg`, I cannot read it). He wanted a decryptable copy "for his own quality of life" and wants to NAME the file, not get an auto-assigned name. He can read the plaintext with `cat ~/.secrets/NAME.secret`.
**Why:** he does not want passwords in chat, commands, transcripts or git. I never print a secret: verify by structure (key names, lengths, character classes, "found verbatim in artifact: True/False") and grep logs for the value without echoing it.
**How to apply:** ask him to run `set-secret <proposed-name>` and say which keys I need (e.g. USER, PASS). Read the file in scripts at run time; never copy it into a repo, memory, backup tarball (exclude `~/.secrets`, `~/esp32-tools/*.secret`) or log. Existing: `~/esp32-tools/wifi.secret` (SSID, PSK; made by the older `~/esp32-tools/set-wifi.sh`) and `ota.secret`. Expected next: `overflow-storage` (SMB share login), the Mudi root login, the work Pi.

**Trap found 2026-10-09:** the Wi-Fi password contained `$`; passing it as a PlatformIO `-D` compiler flag mangled it (the ESP boards got "connect_failed" and I first suspected a bad copy). Never inject secrets through shell/-D flags; generate a header or read a file, and verify the bytes landed (`psk in firmware.bin`). Also: opening an ESP32-S3/C6 USB-serial port with pyserial resets the chip (rst USB_UART_CHIP_RESET) even with dtr/rts False; use `stty -F port -hupcl raw 115200; timeout N cat port` instead.
Related: [[esp32-av-ndi-streaming]], [[magi-github-sync]].

## 2026-10-09 UPDATE: tools
- `set-secret NAME -k USER,PASS` (fixed fields; saves only on finish; Ctrl-C discards; prints the saved key names). Old files use `USERNAME=PASSWORD` (key = account name); both styles work with `~/bin/askpass-secret` and `~/bin/ssh-secret NAME [ssh args]` (password login without a tty, nothing printed). For people who must sign in to a web page (VJ control) use `~/bin/vj-adduser`, which stores only a hash.
- Classifier: changes to a remote host's sshd/known_hosts trust are denied even with Gil's go-ahead given in chat; hand him the one-liner to run on that host. Gil confirmed step 1 (known_hosts refresh) and approved key-only SSH on `ramiel`, but the sshd drop-in was denied twice: he runs `printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' | sudo tee /etc/ssh/sshd_config.d/10-keyonly.conf && sudo sshd -t && sudo systemctl reload ssh` locally on ramiel.
