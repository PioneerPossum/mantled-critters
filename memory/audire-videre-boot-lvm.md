---
name: audire-videre-boot-lvm
description: "why audire-videre drops to initramfs every boot (VG spans two LUKS PVs), the corrected fix staged in ~/initramfs-lvm-fix awaiting a sudo run, and how to verify it"
metadata: 
  node_type: memory
  type: project
  originSessionId: 000a3034-3f49-5f2d-adc6-239473e7eeee
  modified: 2026-09-21T01:47:47.688Z
---

audire-videre's `audire-videre-vg` spans TWO LUKS drives: nvme1n1p3_crypt (root/var/tmp/swap, unlocked in initramfs via `x-initrd.attach`) and kali_extra_crypt (part of `home`, opened later from `/etc/luks-keys/nvme1n1.key`, which lives on the encrypted root so it cannot be opened in initramfs). lvm2 2.03.31 in the initramfs has no `local-top/lvm2` script; it relies on udev auto-activation, which only fires for a COMPLETE VG. So root never activates and boot stops at the initramfs shell. Every boot since at least 2026-09-19 22:06 needed manual vgchange.

An earlier hand-made `/etc/initramfs-tools/scripts/local-top/zzz-lvmfix` was already installed but had 3 bugs: `lvm vchange` typo, wrong device path (`audire--videre-vg-root`, needs `--vg-`), and `PREREQ="lvm2"` (nonexistent) so the ORDER file ran it BEFORE cryptroot had unlocked anything.

**Design lesson (learned the hard way, 2026-09-20):** in initramfs-tools, `local-top/cryptroot`'s own `prereqs()` lists EVERY other local-top script as its prerequisite (it is meant to run last), so any local-top script with `PREREQ="cryptroot"` creates a `tsort` loop (the first install printed "tsort: input contains a loop"). The right hook is **`local-block`**: `local_device_setup` calls it once a second while root is missing (about 30s), after local-top has finished. `local-premount` is too late (runs after the root wait). So: script lives in `scripts/local-block/`, one pass per call, wait loop retries. Verify ordering offline by sourcing `/usr/share/initramfs-tools/hook-functions` under `dash` (NOT zsh) with a sandbox CONFDIR (`get_source` reads `$CONFDIR/scripts/<stage>/`).

**CONFIRMED WORKING 2026-09-20 (~21:00 CDT): Gil rebooted, decrypt succeeded with no initramfs shell. `/run/zzz-lvmfix.log` ended `OK: root present`; lvm activated 4 LVs (root/var/tmp/swap) and correctly refused `home` (partial), which systemd + crypttab then completed. Nothing further needed on this issue; the notes below are history and design rationale.**

**Earlier status 20:44 CDT: INSTALLED (local-block version), both initrds rebuilt, no tsort warnings, verified inside each image. Awaiting the real proof: the first reboot after install.** (The 20:32 install was the broken local-top/loop version; superseded.) Gil was about to reboot audire-videre right after this.

**How to judge the reboot:** `cat /run/zzz-lvmfix.log` should end `OK: root present` (fix worked). No such file = root came up without help (also fine). If boot STILL lands in the initramfs shell: run `cat /run/zzz-lvmfix.log` there FIRST (it shows what lvm said on each pass) before doing the manual vgchange; that log is the diagnostic. Backups of earlier scripts are in /root/zzz-lvmfix.*. Once confirmed working, close the "audire-videre drops to initramfs" card on the Board and the "reboot-survival" card can also be partly closed.

(Older note follows.) audire-videre has no passwordless sudo, and I chose not to use the `docker` group as a root bypass. Corrected script + installer: `~/initramfs-lvm-fix/{zzz-lvmfix,install.sh}`; logic tested only against stub lvm, never against real LVM. Gil must run `sudo bash ~/initramfs-lvm-fix/install.sh` (backs up old script to /root, rebuilds both initrds, verifies cryptroot-before-zzz ordering inside each rebuilt image). Then after a reboot: `cat /run/zzz-lvmfix.log` should end `OK: root present`.

**Why it matters:** the magi net is off for a UPS move ([[magi-ups-power-down]]), so audire-videre may reboot soon. Without the fix it needs manual recovery each time.

**How to apply:** if asked about boot problems here, check whether the install happened (`diff ~/initramfs-lvm-fix/zzz-lvmfix /etc/initramfs-tools/scripts/local-top/zzz-lvmfix`). /boot is tight (~210MB free, initrds ~210MB each): rebuild only works as root using reserved blocks. Never activate `home` in partial mode from initramfs; it spans the missing PV.

Related: the Board tab (`/static/board.html`, data in `~/magi_dashboard/static/board.json`) tracks this; update it when the fix is installed.
