# Mounting skills-fs (optional, read this first)

`skills-fs` is an **optional** FUSE layer that overlays the napcat-cli skill
directory with a virtual `/napcat/` filesystem — you can `cat` events, `echo`
JSON into `send_group` to send messages, browse `groups/{id}/...` as directories.
The `napcat` CLI does **not** need it; this is for agents/workflows that prefer a
filesystem interface.

> ⚠️ **Known hazard — FUSE D-state wedges.** If multiple skills-fs daemons mount
> the same point, or the daemon does an unbounded read on a hung FUSE, the process
> enters **uninterruptible D-state sleep** — unkillable even with `SIGKILL`,
> surviving `umount -l`. On 2026-07-22 a 2-week-old pileup of these forced a
> **kernel panic on reboot**. skills-fs is therefore shipped **disabled by
> default** (`skills_fs_enabled=false`).

## Prevention already in place (napcat-cli ≥ 2.0.0)

- All mount probes run in a **timeout-guarded thread** (`_run_with_timeout`) — a
  hung FUSE syscall only blocks an abandoned thread, never the daemon's main loop.
- `SkillsFsManager` **reuses an already-healthy mount** instead of stacking a
  second FUSE daemon on the same point (the actual deadlock trigger).
- If the mount isn't healthy within the deadline, the child is **killed and the
  daemon goes degraded** (no half-dead FUSE left behind).
- `napcat daemon start` **refuses to launch when `http_port` is already in use**
  (a second daemon, or a D-state zombie still holding it).

## Enabling

```bash
napcat config set skills_fs_enabled true
napcat daemon stop && napcat daemon start
# verify: a single skills-fs process, mount present, daemon NOT in D state
napcat daemon status
mount | grep napcat              # should show one skillsfs mount
ps -o pid,stat,cmd -C python3 | grep -E 'D |watch'   # must show no 'D'
```

If you only want it for a session, leave `skills_fs_enabled=false` and mount by
hand:

```bash
skills-fs fuse --config ~/.napcat-data/skills-fs.json \
  --mountpoint ~/.napcat-data/skills --allow-other \
  --log-file ~/.napcat-data/skills-fs.log --log-level info
```

## If it wedges again (D-state)

Symptom: `ps` shows the daemon/skills-fs in state `D`, port 18821 stuck held.
Recovery does **not** require losing the wake pipeline (skills-fs is supplementary):

```bash
napcat config set skills_fs_enabled false   # wake/events/alerts keep working
napcat daemon stop && napcat daemon start
# the D-state processes themselves only clear on reboot; they cost no CPU while stuck
```

Only if the box is already wedged (e.g. can't cleanly shut down): reboot. The
prevention above is precisely what avoids reaching that point.

## Troubleshooting

- **`--config '' --mountpoint ''` in the log** — fixed (empty config fields now
  fall back to defaults). Re-run `napcat setup` or set `skills_fs_mountpoint` /
  `skills_fs_config` explicitly if it recurs.
- **`skills-fs: no binary found`** — install/build the Go binary (`cd skills-fs
  && make build`) or set `skills_fs_binary`.
- **`SKILLS_FS_DEGRADED` appears in the skill dir** — skills-fs gave up; the
  daemon is running without the FUSE tree. CLI still works.
