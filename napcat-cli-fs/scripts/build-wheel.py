"""Build a platform-specific wheel that wraps a compiled skills-fs binary.

Usage::

    python scripts/build-wheel.py <binary> <wheel-tag> [--version X.Y.Z] [--output-dir dist]

Example::

    # After building skills-fs for linux/amd64:
    python scripts/build-wheel.py skills-fs manylinux_2_17_x86_64.manylinux2014_x86_64

The output wheel is named ``napcat_cli_fs-{version}-py3-none-{tag}.whl`` and
contains::

    napcat_cli_fs/
        __init__.py
        bin/skills-fs                   (or skills-fs.exe on Windows)
    napcat_cli_fs-{version}.dist-info/
        METADATA
        WHEEL
        RECORD
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
import zipfile

PACKAGE = "napcat_cli_fs"
VERSION = "2.0.0"

WHEEL_METADATA = """\
Metadata-Version: 2.1
Name: napcat-cli-fs
Version: {version}
Summary: Pre-built skills-fs FUSE binary for napcat-cli
License: MIT
Requires-Python: >=3.10
"""


def build_wheel(
    binary_path: str,
    wheel_tag: str,
    version: str = VERSION,
    output_dir: str = "dist",
) -> str:
    """Build a single platform wheel.

    Returns the path to the created .whl file.
    """
    is_win = wheel_tag.startswith("win")
    bin_name = "skills-fs.exe" if is_win else "skills-fs"

    os.makedirs(output_dir, exist_ok=True)
    wheel_name = f"{PACKAGE}-{version}-py3-none-{wheel_tag}.whl"
    wheel_path = os.path.join(output_dir, wheel_name)

    records: list[str] = []

    def add_file(zf: zipfile.ZipFile, arcname: str, content: bytes, path: str = "") -> None:
        """Add a file to the wheel and record its hash."""
        if path:
            arcname = os.path.join(path, arcname)
        else:
            arcname = arcname
        info = zipfile.ZipInfo(arcname)
        info.date_time = (2026, 1, 1, 0, 0, 0)
        info.external_attr = 0o644 << 16
        zf.writestr(info, content)
        sha = hashlib.sha256(content).hexdigest()
        records.append(f"{arcname},sha256={sha},{len(content)}")

    def add_data_file(zf: zipfile.ZipFile, src_path: str, arcname: str) -> None:
        """Read *src_path* (disk) and write into *arcname* inside the wheel."""
        with open(src_path, "rb") as f:
            data = f.read()
        info = zipfile.ZipInfo(arcname)
        info.date_time = (2026, 1, 1, 0, 0, 0)
        info.external_attr = 0o755 << 16  # executable
        zf.writestr(info, data)
        sha = hashlib.sha256(data).hexdigest()
        records.append(f"{arcname},sha256={sha},{len(data)}")

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # WHEEL
        wheel_content = (
            "Wheel-Version: 1.0\n"
            f"Generator: napcat-cli-fs build-wheel.py\n"
            "Root-Is-Purelib: false\n"
            f"Tag: py3-none-{wheel_tag}\n"
        )
        add_file(zf, "WHEEL", wheel_content.encode(), f"{PACKAGE}-{version}.dist-info")

        # METADATA
        meta = WHEEL_METADATA.format(version=version)
        add_file(zf, "METADATA", meta.encode(), f"{PACKAGE}-{version}.dist-info")

        # napcat_cli_fs/__init__.py
        init_py = f'"""skills-fs binary bundle (platform: {wheel_tag})."""\n__version__ = "{version}"\n'
        add_file(zf, "__init__.py", init_py.encode(), PACKAGE)

        # napcat_cli_fs/bin/skills-fs
        add_data_file(zf, binary_path, f"{PACKAGE}/bin/{bin_name}")

        # RECORD (must be last)
        record_content = "\n".join(records) + "\n"
        add_file(zf, "RECORD", record_content.encode(), f"{PACKAGE}-{version}.dist-info")

    print(f"Built: {wheel_path}  ({os.path.getsize(wheel_path)} bytes)")
    return wheel_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a skills-fs platform wheel")
    parser.add_argument("binary", help="Path to compiled skills-fs binary")
    parser.add_argument("tag", help="Wheel platform tag (e.g. manylinux_2_17_x86_64)")
    parser.add_argument("--version", default=VERSION, help=f"Version (default {VERSION})")
    parser.add_argument("--output-dir", default="dist", help="Output directory (default dist/)")
    args = parser.parse_args()
    build_wheel(args.binary, args.tag, version=args.version, output_dir=args.output_dir)


if __name__ == "__main__":
    main()