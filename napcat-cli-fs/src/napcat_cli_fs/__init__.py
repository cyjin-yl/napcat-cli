"""napcat_cli_fs — compiled skills-fs Go binary for napcat-cli.

This package contains a pre-built ``skills-fs`` (or ``skills-fs.exe`` on
Windows) binary.  It is installed automatically when you choose the ``[fs]``
extra::

    pip install napcat-cli[fs]
    # or
    uv tool install napcat-cli --with napcat-cli-fs

The daemon's ``SkillsFsManager`` finds the binary via this package and uses it
to mount a FUSE overlay on the napcat-cli skill directory.

Platform support
----------------
- **Linux**           (builds tested on amd64; arm64 via CI)
- **macOS**           (amd64 + arm64 via CI; FUSE is **untested** — open a PR if
                      you hit issues)
- **Windows**         (amd64 via CI; FUSE is **untested** — open a PR if you hit
                      issues)
"""

__version__ = "2.0.0"