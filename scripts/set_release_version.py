#!/usr/bin/env python3
"""Validate and stage the Email HA manifest release version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "email_ha" / "manifest.json"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_LINE = re.compile(r'(^\s*"version"\s*:\s*")([^"]+)("\s*$)', re.MULTILINE)


def current_version() -> str:
    """Return the tracked manifest version after validating its format."""
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = raw.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise RuntimeError(f"Manifest version is not X.Y.Z: {version!r}")
    return version


def set_release_version(version: str) -> None:
    """Update only the manifest version while preserving existing formatting."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"Release version must use X.Y.Z format, got {version!r}")

    text = MANIFEST.read_text(encoding="utf-8")
    updated, count = VERSION_LINE.subn(
        lambda match: f"{match.group(1)}{version}{match.group(3)}",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Expected exactly one manifest version line; found {count}")
    MANIFEST.write_text(updated, encoding="utf-8")

    staged = current_version()
    if staged != version:
        raise RuntimeError(f"Version staging produced {staged!r}, expected {version!r}")


def main() -> None:
    """Validate the current version or stage the requested release version."""
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", help="target X.Y.Z release version")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only validate and print the current tracked version",
    )
    args = parser.parse_args()

    if args.check:
        if args.version is not None:
            parser.error("--check does not accept a target version")
        sys.stdout.write(f"{current_version()}\n")
        return
    if args.version is None:
        parser.error("a target version is required unless --check is used")

    set_release_version(args.version)


if __name__ == "__main__":
    main()
