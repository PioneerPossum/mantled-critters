---
name: magi-backups
description: "Automated 'shit hit the fan' disaster-recovery backups of melchior/balthazar state onto repositorium, built 2026-09-17"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-17T13:28:22.679Z
---

Built 2026-09-17. This is a separate, independent recovery layer from the existing git-based code sync (`~/magi/dedup` snapshots, `git` repo history) — it captures the *live, uncommitted* state of each Pi (running credentials, systemd wiring, installed packages) that git doesn't track. Explicitly a first step toward an eventual full-cluster "carbon copy" DR capability (a bigger hardware idea — SD-less boot, NVMe HATs — is a separate future discussion, not part of this).

## Architecture

**Push model, from each Pi to repositorium.** Chosen because melchior/balthazar already have working passwordless SSH *to* repositorium (`~/.ssh/id_ed25519_repositorium`, set up during the earlier dedup/git-sync work) — confirmed working in both directions during this session. The reverse (repositorium → melchior/balthazar) has **no keys configured at all** (`Host key verification failed` / hostname doesn't even resolve) — pulling would have needed new key provisioning, so pushing was the natural fit with zero new keys.

- **Script**: `~/magi/backup/magi_backup.sh`, deployed identically on both melchior and balthazar (behavior derives from `hostname` at runtime, so one script serves both).
  - Note: balthazar's actual OS hostname is still `dinky-pai` (only the SSH alias/config was ever renamed to "balthazar", not the underlying hostname — a known inconsistency from earlier sessions). The script explicitly normalizes `dinky-pai` → `balthazar` so the backup tree matches the name used everywhere else in the project.
- **Trigger**: systemd timer (not cron, to match this project's conventions) — `magi-backup.timer` + `magi-backup.service` (`Type=oneshot`) on each Pi, both at `/etc/systemd/system/`.
  - melchior: daily at 03:15 (+random up to 2 min)
  - balthazar: daily at 03:35 (+random up to 2 min) — offset from melchior deliberately so the two don't hit repositorium's disk/network at the exact same moment
  - Both `Persistent=true` (catches up if the Pi was off at the scheduled time) and enabled (`systemctl enable --now magi-backup.timer`).
- **Destination**: `/home/sinewave/512_VideoStick/backups/<hostname>/<timestamp>/` on repositorium — deliberately on the 469GB NVMe mount (435G free as of this session), **not** the root SD card partition (only 49G free, 13% used, tighter margin). The NVMe mount root itself is owned by `root:root`; the `backups` subdirectory was created once with `sudo mkdir` + `chown sinewave:sinewave` + `chmod 700` so the unprivileged push script can write to it going forward without needing sudo on repositorium.
- **Incremental mechanism**: `rsync -a --link-dest=<dest>/latest ...` — unchanged files between runs are hardlinked to the previous snapshot's copy (verified: a file's link count was `2` across two consecutive melchior snapshots), so repeated daily backups cost near-zero extra disk for the (currently tiny, <2MB) per-node payload even though each snapshot dir looks like a full copy. `<hostname>/latest` is a symlink, atomically repointed (`ln -sfn ... && mv -Tf ...`) to the newest *successfully completed* snapshot only — a failed rsync leaves `latest` untouched so `--link-dest` next time still compares against the last good snapshot, and no half-written snapshot is ever pointed to by `latest`.
- **Permissions**: backup dirs `700`, files `600` throughout (`rsync --chmod=Du=rwx,Dgo=,Fu=rw,Fgo=`), enforced by the script itself regardless of source file perms. Nothing under `~/magi/backup/` or the backup tree is in any git repo.

## What's backed up (per node, per snapshot)

- `magi/` — everything under `~/magi/` (all code/config), excluding `__pycache__`/`*.pyc`
- `config_magi/` — `~/.config/magi/*.env` (the actual Redis/Postgres credential files) — copied then immediately `chmod 600`'d in the staging dir before transfer. **Credential file contents are never printed to stdout/logs at any point in the script.**
- `ssh/` — `~/.ssh/config`, `authorized_keys`, `known_hosts`, and `*.pub` files only. **Private keys are deliberately never copied** (the task explicitly scoped this to "non-private key material").
- `systemd/` — copies of `/etc/systemd/system/magi-*.service` (all four: `magi-backup`, `magi-failover`, `magi-heartbeat`, `magi-live-detect`/`magi-llm-api` as applicable per node)
- `meta/` — reconstruction metadata, not raw file copies:
  - `dpkg-selections.txt` — full `dpkg --get-selections` output (reproducible package list)
  - `crontab.txt` / `crontab-root.txt` — user and root crontabs (both currently empty on both nodes, captured anyway for future-proofing)
  - `uname.txt` — kernel/arch info
  - `hailo-apps-version.txt` — **not** a copy of the 1.1GB `~/hailo-apps` checkout (explicitly out of scope per task) — just its `git rev-parse HEAD` + `git describe --tags` (both nodes pinned to commit `891ce70`, tag `26.03.1`, confirmed identical across melchior/balthazar)
  - `backup_timestamp.txt` — ISO-8601 timestamp of the run

## Retention

**Keep last 14 snapshots per node** (roughly 2 weeks of daily backups), pruned automatically at the end of every successful run: `ls -1dt */ | grep -v latest | tail -n +15 | xargs rm -rf`. Chosen somewhat generously since real per-snapshot disk cost is tiny (hardlinked unchanged files) — the 14-count cap is really just a sanity backstop against unbounded growth if the payload ever grows (e.g. if someone starts storing larger artifacts under `~/magi/`), not a tight constraint against current usage. Deleting an old snapshot is safe even for hardlinked files — the OS keeps the underlying inode alive as long as any newer snapshot still references it.

## Verified 2026-09-17

- Ran the backup live on both melchior and balthazar (via `sudo systemctl start magi-backup.service`), confirmed `status=0/SUCCESS` in both `systemctl status` and `journalctl -u magi-backup.service`.
- Real files landed on repositorium at `/home/sinewave/512_VideoStick/backups/melchior/<ts>/` and `.../balthazar/<ts>/` with the full expected subtree (`magi/`, `config_magi/`, `ssh/`, `systemd/`, `meta/`).
- `latest` symlinks correctly point at the newest snapshot on both.
- Ran melchior's backup twice in a row to prove the incremental path: `magi_publish.py` showed `links=2` in both the older and newer snapshot dirs, confirming rsync hardlinked the unchanged file via `--link-dest` rather than re-copying it.
- Permissions confirmed: backup dirs `700`, `config_magi/publish.env` `600`, all owned by `sinewave:sinewave`.
- `systemctl list-timers magi-backup.timer` on both nodes shows `enabled` with a real next-fire time (melchior 03:15, balthazar 03:35, both "tomorrow" relative to this session).
- `df -h` on repositorium's NVMe mount: 435G free after real backups landed (started at 436G) — negligible footprint, no risk of filling disk under the 14-snapshot retention policy for the foreseeable future.

## How to restore from a backup (conceptual — not yet drilled/tested as a full restore)

Given a snapshot at `repositorium:/home/sinewave/512_VideoStick/backups/<node>/latest/` (or any specific timestamped dir):

1. **Rebuild the base OS/packages**: flash a fresh Raspberry Pi OS image matching `meta/uname.txt`'s kernel/arch, then `sudo dpkg --set-selections < meta/dpkg-selections.txt && sudo apt-get dselect-upgrade` to reinstall the same package set (note: this restores *package names*, not custom-built things like the Hailo stack that came from `hailo-apps`'s own `install.sh`, which pins its own dependencies — see step 3).
2. **Restore code/config**: `rsync -a <snapshot>/magi/ ~/magi/` on the rebuilt node.
3. **Rebuild the Hailo stack**: `git clone https://github.com/hailo-ai/hailo-apps ~/hailo-apps && cd ~/hailo-apps && git checkout $(cat <snapshot>/meta/hailo-apps-version.txt | grep -A1 '^---' | head -1)` (the commit hash), then re-run `sudo ./install.sh` and `hailo-post-install --group detection` per the existing setup docs in `magi-cluster-status.md` — the repo itself was deliberately not backed up (1.1GB, fully reproducible from the pinned commit).
4. **Restore credentials**: `cp <snapshot>/config_magi/*.env ~/.config/magi/ && chmod 600 ~/.config/magi/*.env` — these still contain the real Redis/Postgres passwords from when the snapshot was taken, so they'll work as-is unless those passwords were rotated after the snapshot.
5. **Restore SSH config**: `cp <snapshot>/ssh/config ~/.ssh/config` — but the **private key itself is not in any backup** (by design), so a new keypair must be generated and its public key added to repositorium's `~/.ssh/authorized_keys` (or restore repositorium's own `authorized_keys` backup, which lists what public keys it used to trust, as a reference for what to re-add).
6. **Restore systemd units**: `sudo cp <snapshot>/systemd/magi-*.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now <unit>` for each.
7. **Restore crontabs** (if ever non-empty): `crontab <snapshot>/meta/crontab.txt` / `sudo crontab <snapshot>/meta/crontab-root.txt`.

This restore path has **not** been end-to-end drilled (e.g. no actual reflash-and-restore of a spare SD card was performed this session) — it's the documented intended procedure based on what's captured, not a tested runbook. Worth doing a real dry-run restore at some point before actually relying on this in a true "shit hit the fan" scenario.

## Not covered by this backup (known gaps, consistent with task scope)

- **repositorium itself** is not backed up (its Postgres/Redis data, `magi_worker.py`, dashboard code) — this task was explicitly scoped to melchior/balthazar → repositorium, treating repositorium as the (currently singular, not-yet-redundant) backup target. A future "carbon copy" phase would need repositorium's own data backed up somewhere else too.
- Postgres/Redis **data** (the actual `events` table contents, embeddings, etc.) isn't part of this — this backs up the Pis' code/config/credentials to *reconnect* to that data store, not the data store's contents itself.
- The D555 camera bridge state on audire-videre (`magi_stream_d555.py`, the dashboard) is out of scope — audire-videre wasn't part of this task and has no passwordless sudo/existing key relationship to repositorium to build on without extra provisioning.
