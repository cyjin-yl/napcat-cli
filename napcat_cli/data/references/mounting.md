# Mounting skills-fs (optional, read this first)

`skills-fs` is an **optional** FUSE layer. It mounts **directly on the napcat-cli
skill directory** (`~/.hermes/skills/napcat-cli/`) and takes it over: while
mounted, that directory serves the generated `SKILL.md`/`AGENTS.md`/`persona.md`
plus the virtual `/napcat/` filesystem (read events, `echo` JSON into
`send_group`, browse `groups/{id}/...`). **Unmount and the original static skill
files reappear** — skills-fs generates into memory only, never overwriting them.

The `napcat` CLI does **not** need the mount; this is for agents/workflows that
prefer a filesystem interface.

> ⚠️ **Known hazard — FUSE D-state wedges.** If multiple skills-fs daemons mount
> the same point, or the daemon does an unbounded read on a hung FUSE, the process
> enters **uninterruptible D-state sleep** — unkillable even with `SIGKILL`,
> surviving `umount -l`. On 2026-07-22 a 2-week-old pileup of these forced a
> **kernel panic on reboot**. skills-fs is therefore shipped **disabled by
> default** (`skills_fs_enabled=false`).

## Overlay semantics (what the agent sees)

- **Not mounted** → the skill dir shows the **static** files: the pre-mount
  `SKILL.md` (says so, full CLI guide, how to mount), `references/`, etc.
- **Mounted** → skills-fs overlays the dir: the agent reads the **generated**
  `SKILL.md` (describes the `/napcat/` tree), generated `AGENTS.md`, `persona.md`,
  and the `/napcat/` virtual filesystem. `SKILLS_FS_DEGRADED` (if present) is
  hidden under the mount.
- skills-fs **generates into memory only** — it does not write `SKILL.md` to the
  skill dir on disk, so the static files underneath are preserved. `Generate`'s
  `Remove` only drops memory state; it never `rm`s the skill dir.
- Generated files are served **read-only (0o444)** — writes are denied, since the
  content is regenerated on next start.

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
napcat config set skills_fs_mountpoint ~/.hermes/skills/napcat-cli   # the skill dir
napcat daemon stop && napcat daemon start
# verify: a single skills-fs process, mount on the skill dir, daemon NOT in D state
napcat daemon status
mount | grep napcat              # one skillsfs mount on ~/.hermes/skills/napcat-cli
ps -o pid,stat,cmd -C python3 | grep -E 'D |watch'   # must show no 'D'
```

If you only want it for a session, leave `skills_fs_enabled=false` and mount by
hand:

```bash
skills-fs fuse --config ~/.napcat-data/skills-fs.json \
  --mountpoint ~/.hermes/skills/napcat-cli --allow-other \
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
  daemon is running without the FUSE tree. CLI still works. (It disappears
  automatically once skills-fs mounts successfully.)

