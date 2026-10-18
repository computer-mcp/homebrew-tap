#!/usr/bin/env python3
"""Check installed packages against the accepted distribution inputs."""

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_cli(metadata):
    prefix = Path(subprocess.check_output(["brew", "--prefix", "apple-cli"], text=True).strip())
    for record in metadata["binaries"]:
        relative = Path(record["path"])
        require(len(relative.parts) == 2 and relative.parts[0] == "bin", "Unexpected executable inventory")
        file = prefix / "libexec" / relative
        checksum = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        require(checksum.hexdigest() == record["sha256"], "Homebrew changed an accepted executable")
        subprocess.run(["codesign", "--verify", "--strict", str(file)], check=True, timeout=30)
    for name in ("apple", "apple-cli-mcp"):
        executable = prefix / "bin" / name
        require(executable.resolve() == (prefix / "libexec/bin" / name).resolve(), "Executable link does not preserve the runtime layout")
        require(subprocess.check_output([str(executable), "--version"], text=True, timeout=30).strip() == metadata["version"], "Installed version mismatch")


def verify_app(metadata, appdir):
    app = appdir / "Computer MCP.app"
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    require(info["CFBundleShortVersionString"] == metadata["version"], "Installed app version mismatch")
    require(info["LSMinimumSystemVersion"].split(".")[0] == metadata["minimum_macos"], "Review the app's new platform floor")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True, timeout=60)
    subprocess.run(["xcrun", "stapler", "validate", str(app)], check=True, timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=("apple-cli", "computer-mcp"))
    parser.add_argument("--appdir", type=Path, default=Path("/Applications"))
    options = parser.parse_args()
    metadata = json.loads((ROOT / "Metadata" / f"{options.name}.json").read_text())
    if options.name == "apple-cli":
        verify_cli(metadata)
    else:
        verify_app(metadata, options.appdir)
    print(f"Verified {options.name} {metadata['version']} installation")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"installation verification: {error}") from None
